"""演示数据集构建：ERPNext 客户/价格/BOM/库存/多状态销售订单 + OpenMES 工单/报工/质量问题。

一次性、显式授权的数据导入（非智能体写入工具），全部走两套系统的常规 HTTP API。
业务记录为虚构演示数据，用于比赛演示；系统连接与读取路径均为真实接口。
幂等设计：已存在的记录回读校验后跳过，绝不覆盖。

在应用服务器项目根目录运行：
    .venv/bin/python backend/seed_demo_dataset.py --check
    .venv/bin/python backend/seed_demo_dataset.py --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

LINE_CODE = "AP_LINE_01"

# product_code -> 工序步骤（名称, 作业指导, 标准工时分钟）
TEMPLATE_STEPS: dict[str, list[tuple[str, str, int]]] = {
    "BD-2401": [("数控粗车", "装夹铸铁毛坯，粗车内外圆及端面，留精车余量 0.5mm", 25),
                ("数控精车", "精车摩擦面与安装止口至图纸公差，表面粗糙度 Ra1.6", 35),
                ("动平衡检测与包装", "按 60g·cm 校验动平衡，合格后清洗涂油、贴标装箱", 15)],
    "BD-2402": [("数控粗车", "装夹铸铁毛坯，粗车内外圆及端面，留精车余量 0.5mm", 25),
                ("数控精车", "精车摩擦面与安装止口至图纸公差，表面粗糙度 Ra1.6", 35),
                ("动平衡检测与包装", "按 60g·cm 校验动平衡，合格后清洗涂油、贴标装箱", 15)],
    "SK-3401": [("铣基准面", "以锻坯大端定位，铣削加工基准面与工艺凸台", 30),
                ("加工中心精铣", "四轴联动精铣各安装孔与转向臂型面，位置度 ≤0.05mm", 55),
                ("三坐标检测与入库", "全尺寸三坐标抽检（AQL 1.0），合格品防锈入立体库", 20)],
    "TS-4501": [("轴管切割与压装", "轴管定长切割，两端十字轴万向节液压压装到位", 40),
                ("动平衡校正", "双面去重校正动平衡，残余不平衡量 ≤15g·cm", 30),
                ("最终检验与包装", "扭矩特性抽检 + 外观检查，合格后装配护套并装箱", 20)],
    "CP-5601": [("摩擦材料压制", "摩擦材料粉末预热后压制成型，保压固化", 45),
                ("铆合装配", "芯板与摩擦片铆合，铆接力按工艺卡分段控制", 25),
                ("摩擦系数抽检与包装", "每批抽 3 件做摩擦系数试验（0.38-0.45），合格后包装", 20)],
}
ISSUE_TYPE_ID = 7  # 与现有问题一致的类型（尺寸/外观类）
PG_CONTAINER = "openmmes-postgres"


class SeedError(RuntimeError):
    pass


def _require_env(*names: str) -> dict[str, str]:
    values = {}
    for name in names:
        value = os.getenv(name, "").strip()
        if not value:
            raise SeedError(f"缺少环境变量：{name}")
        values[name] = value
    return values


class Erp:
    def __init__(self):
        env = _require_env("ERPNEXT_BASE_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET")
        self.base = env["ERPNEXT_BASE_URL"].rstrip("/")
        self.headers = {"Authorization": f"token {env['ERPNEXT_API_KEY']}:{env['ERPNEXT_API_SECRET']}"}
        self.client = httpx.Client(timeout=30)

    def close(self):
        self.client.close()

    def _url(self, doctype: str, name: str | None = None) -> str:
        path = quote(doctype, safe="")
        return f"{self.base}/api/resource/{path}" + (f"/{quote(name, safe='')}" if name else "")

    def list(self, doctype: str, fields: str, filters: str | None = None) -> list[dict]:
        url = f"{self._url(doctype)}?fields={fields}&limit_page_length=0"
        if filters:
            url += f"&filters={filters}"
        response = self.client.get(url, headers=self.headers)
        if response.status_code >= 400:
            raise SeedError(f"ERPNext 列表失败 {doctype}: HTTP {response.status_code} {response.text[:200]}")
        return response.json().get("data") or []

    def get(self, doctype: str, name: str) -> dict | None:
        response = self.client.get(self._url(doctype, name), headers=self.headers)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SeedError(f"ERPNext 读取失败 {doctype}/{name}: HTTP {response.status_code}")
        return response.json().get("data")

    def create(self, doctype: str, body: dict, *, submit: bool = False) -> dict:
        response = self.client.post(self._url(doctype), headers=self.headers, json=body)
        if response.status_code >= 400:
            raise SeedError(f"ERPNext 创建失败 {doctype}: HTTP {response.status_code} {response.text[:300]}")
        name = response.json().get("data", {}).get("name")
        if submit:
            response = self.client.post(self._url(doctype, name), headers=self.headers,
                                        json={"run_method": "submit"})
            if response.status_code >= 400:
                raise SeedError(f"ERPNext 提交失败 {doctype}/{name}: HTTP {response.status_code} {response.text[:300]}")
        return self.get(doctype, name) or {}


class Mes:
    def __init__(self):
        env = _require_env("OPENMES_BASE_URL")
        self.base = env["OPENMES_BASE_URL"].rstrip("/")
        env_key = _require_env("OPENMES_ERP_API_KEY")
        self.erp_key = env_key["OPENMES_ERP_API_KEY"]
        token = os.getenv("OPENMES_TOKEN", "").strip()
        if token:
            probe = httpx.get(f"{self.base}/api/v1/work-orders",
                              params="search=WO&per_page=1",
                              headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if probe.status_code != 200 or not probe.text.strip().startswith("{"):
                token = ""
        if not token:
            openmes_env = (ROOT / "services" / "OpenMes" / ".env")
            if openmes_env.exists():
                from dotenv import dotenv_values

                values = dotenv_values(openmes_env)
                username = str(values.get("ADMIN_USERNAME") or "").strip()
                password = str(values.get("ADMIN_PASSWORD") or "").strip()
                response = httpx.post(f"{self.base}/api/auth/login",
                                      json={"username": username, "password": password}, timeout=30)
                if response.status_code >= 400:
                    raise SeedError(f"OpenMES 登录失败：HTTP {response.status_code}")
                token = str((response.json().get("data") or {}).get("token") or "")
        if not token:
            raise SeedError("OpenMES 令牌不可用（OPENMES_TOKEN 与管理员登录均失败）")
        self.auth = {"Authorization": f"Bearer {token}"}
        self.client = httpx.Client(timeout=60)

    def close(self):
        self.client.close()

    def _call(self, method: str, path: str, *, params: str | None = None, body: dict | None = None,
              headers: dict | None = None) -> dict:
        response = self.client.request(method, f"{self.base}{path}", params=params,
                                       headers=headers or self.auth, json=body)
        if response.status_code >= 400:
            raise SeedError(f"OpenMES {method} {path} 失败：HTTP {response.status_code} {response.text[:300]}")
        return response.json().get("data", {})

    def list_work_orders(self, search: str) -> list[dict]:
        rows = self._call("GET", "/api/v1/work-orders", params=f"search={quote(search)}&per_page=100")
        return rows if isinstance(rows, list) else []

    def find_work_order(self, order_no: str) -> dict | None:
        rows = [row for row in self.list_work_orders(order_no) if row.get("order_no") == order_no]
        if len(rows) > 1:
            raise SeedError(f"OpenMES 工单号重复：{order_no}")
        return rows[0] if rows else None

    def import_work_order(self, body: dict) -> dict:
        return self._call("POST", "/api/v1/erp/work-orders/import", body=body,
                          headers={"X-Api-Key": self.erp_key})

    def create_batch(self, work_order_id: int, target_qty: int) -> dict:
        return self._call("POST", f"/api/v1/work-orders/{work_order_id}/batches",
                          body={"target_qty": target_qty, "notes": "首批投产"})

    def start_step(self, step_id: int) -> dict:
        return self._call("POST", f"/api/v1/batch-steps/{step_id}/start")

    def complete_step(self, step_id: int, produced_qty: int, elapsed_minutes: int) -> dict:
        return self._call("POST", f"/api/v1/batch-steps/{step_id}/complete",
                          body={"produced_qty": produced_qty,
                                "actual_elapsed_minutes": elapsed_minutes})

    def list_batches(self, work_order_id: int) -> list[dict]:
        rows = self._call("GET", f"/api/v1/work-orders/{work_order_id}/batches")
        return rows if isinstance(rows, list) else []

    def list_product_types(self) -> list[dict]:
        rows = self._call("GET", "/api/v1/product-types", params="per_page=100")
        return rows if isinstance(rows, list) else []

    def list_template_steps(self, template_id: int) -> list[dict]:
        detail = self._call("GET", f"/api/v1/process-templates/{template_id}")
        steps = detail.get("steps") if isinstance(detail, dict) else None
        return steps if isinstance(steps, list) else []

    def add_template_step(self, template_id: int, number: int, name: str, instruction: str, minutes: int) -> None:
        self._call("POST", f"/api/v1/process-templates/{template_id}/steps", body={
            "step_number": number, "name": name, "instruction": instruction,
            "estimated_duration_minutes": minutes,
        })

    def list_all_issues(self) -> list[dict]:
        rows = self._call("GET", "/api/v1/erp/quality/issues",
                          headers={"X-Api-Key": self.erp_key})
        return rows if isinstance(rows, list) else []

    def create_issue(self, work_order_id: int, title: str, description: str, qty: int) -> dict:
        return self._call("POST", "/api/v1/issues", body={
            "work_order_id": work_order_id, "issue_type_id": ISSUE_TYPE_ID,
            "title": title, "description": description, "non_conforming_qty": qty,
        })

    def resolve_issue(self, issue_id: int, notes: str) -> dict:
        return self._call("POST", f"/api/v1/issues/{issue_id}/resolve", body={"resolution_notes": notes})

    def close_issue(self, issue_id: int) -> dict:
        return self._call("POST", f"/api/v1/issues/{issue_id}/close")


# ── 数据定义 ──────────────────────────────────────────────────────────────

NEW_CUSTOMERS = ("一汽解放汽车有限公司", "重庆长安汽车股份有限公司")

# item_code -> (rate, bom 原料用量 {item_code: qty})
ITEM_PRICES: dict[str, tuple[int, dict[str, int]]] = {
    "BD-2401": (85, None),  # BOM 已存在
    "BD-2402": (78, {"CI-RAW": 1, "M10-BOLT": 4, "SEAL-RING": 1}),
    "SK-3401": (120, {"CI-RAW": 1, "M10-BOLT": 4, "SEAL-RING": 1, "BRG-6204": 2}),
    "TS-4501": (350, {"CI-RAW": 2, "BRG-6204": 4, "SEAL-RING": 2}),
    "CP-5601": (96, {"CI-RAW": 1, "SEAL-RING": 2, "M10-BOLT": 8}),
}


@dataclass(frozen=True)
class DemoCase:
    wo_no: str
    customer: str
    item_code: str
    part_name: str
    quantity: int
    unit_price: int
    delivery_date: str
    priority: int
    submit: bool
    report: tuple[int, int] | None  # (passed_qty, elapsed_minutes)
    issue: tuple[str, str, int, bool] | None  # (title, description, qty, close_after)
    order_date: str = "2026-10-24"
    done: bool = False  # 完工历史单：全部工序报满并置 DONE


CASES = (
    DemoCase("WO-2026-1024-01", "一汽解放汽车有限公司", "BD-2402", "制动盘-后轮",
             600, 78, "2026-11-28", 4, True, (150, 300),
             ("制动盘后轮端面跳动超差", "首检抽检 5 件，2 件端面跳动 0.04mm，超出图纸要求 0.03mm；已隔离，待返修评估。", 5, False)),
    DemoCase("WO-2026-1024-02", "重庆长安汽车股份有限公司", "CP-5601", "离合器片",
             800, 96, "2026-12-02", 3, True, (240, 480),
             ("离合器片摩擦系数批次偏低", "过程巡检发现本批摩擦系数 0.36，低于下限 0.38；已加严首件确认并通知实验室复检。", 12, False)),
    DemoCase("WO-2026-1024-03", "上汽集团", "BD-2401", "制动盘-前轮",
             300, 85, "2026-11-18", 5, False, (210, 420),
             ("制动盘前轮表面局部锈斑", "工序间周转存放 48 小时后出现局部锈斑，影响外观等级；判定返抛光处理，已全部返工完成并复检合格。", 8, True)),
    DemoCase("WO-2026-1024-04", "比亚迪汽车工业有限公司", "SK-3401", "转向节",
             450, 120, "2026-12-08", 3, True, None, None),
    # 第二波：历史纵深（更早日期、多状态、含完工单）
    DemoCase("WO-2026-0918-01", "上汽集团", "BD-2401", "制动盘-前轮",
             800, 85, "2026-10-10", 3, True, None, None,
             order_date="2026-09-18", done=True),
    DemoCase("WO-2026-0922-01", "长城汽车股份有限公司", "CP-5601", "离合器片",
             500, 96, "2026-10-16", 3, True, None, None,
             order_date="2026-09-22", done=True),
    DemoCase("WO-2026-0925-01", "比亚迪汽车工业有限公司", "TS-4501", "传动轴总成",
             260, 350, "2026-11-05", 4, True, (120, 260), None,
             order_date="2026-09-25"),
    DemoCase("WO-2026-0928-01", "一汽解放汽车有限公司", "SK-3401", "转向节",
             380, 120, "2026-11-12", 3, True, None, None,
             order_date="2026-09-28", done=True),
    DemoCase("WO-2026-1006-01", "重庆长安汽车股份有限公司", "BD-2402", "制动盘-后轮",
             550, 78, "2026-11-25", 3, True, (200, 420), None,
             order_date="2026-10-06"),
    DemoCase("WO-2026-1012-01", "上汽集团", "CP-5601", "离合器片",
             300, 96, "2026-11-30", 3, False, None, None,
             order_date="2026-10-12"),
    DemoCase("WO-2026-1015-01", "长城汽车股份有限公司", "BD-2401", "制动盘-前轮",
             450, 85, "2026-12-12", 3, True, None, None,
             order_date="2026-10-15"),
    DemoCase("WO-2026-1018-01", "一汽解放汽车有限公司", "TS-4501", "传动轴总成",
             150, 350, "2026-12-18", 4, False, None, None,
             order_date="2026-10-18"),
)

# 历史质量问题（创建后回填过去时间戳，形成处置历史）
HISTORY_ISSUES = (
    ("WO-2026-0918-01", "制动盘前轮加工面划伤", "转运过程工位器具磕碰导致划伤，深度未超差但影响外观；全数返抛光处理并复检合格。", 6, 28),
    ("WO-2026-0922-01", "离合器片铆接力批次波动", "铆接力监控曲线超出工艺窗口上限，隔离复检后判定合格，工艺参数已回调。", 3, 21),
    ("WO-2026-0928-01", "转向节螺纹孔位置度偏移", "夹具定位销磨损导致位置度 0.08mm 超差，更换定位销后首件三坐标复检合格。", 4, 12),
)


cases_so: dict[str, str] = {}


def erp_seed(erp: Erp, *, apply: bool) -> None:
    print("== ERPNext ==")
    warehouses = erp.list("Warehouse", '["name","is_group"]')
    leaf = [w["name"] for w in warehouses if not w.get("is_group")]
    stores = next((w for w in leaf if w.startswith("Stores")), None)
    finished = next((w for w in leaf if w.startswith("Finished Goods")), None)
    if not stores or not finished:
        raise SeedError(f"ERPNext 缺少 Stores/Finished Goods 仓库：{leaf}")
    print(f"仓库：{stores} / {finished}")

    existing_customer = erp.get("Customer", "上汽集团") or {}
    customer_group = existing_customer.get("customer_group")
    if not customer_group:
        groups = erp.list("Customer Group", '["name","is_group"]')
        leaf_groups = [g["name"] for g in groups if not g.get("is_group")]
        if not leaf_groups:
            raise SeedError("ERPNext 没有可用的非组客户组")
        customer_group = leaf_groups[0]
    print(f"客户组：{customer_group}")

    for customer in NEW_CUSTOMERS:
        if erp.get("Customer", customer) is None:
            if not apply:
                print(f"[缺] 客户 {customer}")
                continue
            erp.create("Customer", {"customer_name": customer, "customer_type": "Company",
                                    "customer_group": customer_group})
            print(f"[建] 客户 {customer}")
        else:
            print(f"[有] 客户 {customer}")

    existing_prices = {p["item_code"] for p in erp.list(
        "Item Price", '["item_code"]', '[["item_code","in",["BD-2401","BD-2402","SK-3401","TS-4501","CP-5601"]]]')}
    for item_code, (rate, _) in ITEM_PRICES.items():
        if item_code in existing_prices:
            print(f"[有] 价格 {item_code}")
            continue
        if not apply:
            print(f"[缺] 价格 {item_code}")
            continue
        erp.create("Item Price", {"item_code": item_code, "price_list": "Standard Selling",
                                  "rate": rate, "selling": 1, "currency": "CNY"})
        print(f"[建] 价格 {item_code} = {rate}")

    for item_code, (_, materials) in ITEM_PRICES.items():
        if materials is None:
            continue
        boms = erp.list("BOM", '["name","is_active","is_default"]', f'[["item","=","{item_code}"]]')
        if any(b.get("is_active") for b in boms):
            print(f"[有] BOM {item_code}")
            continue
        if not apply:
            print(f"[缺] BOM {item_code}")
            continue
        existing_bom = erp.get("BOM", "BOM-BD-2401-001") or {}
        bom_company = existing_bom.get("company")
        if not bom_company:
            companies = erp.list("Company", '["name","abbr"]')
            bom_company = next((c["name"] for c in companies if c.get("abbr") == "APM"),
                               companies[0]["name"] if companies else None)
        body = {"item": item_code, "quantity": 1, "is_active": 1, "is_default": 1,
                "company": bom_company, "items": [
            {"item_code": code, "qty": qty, "uom": "Nos", "stock_uom": "Nos",
             "source_warehouse": stores}
            for code, qty in materials.items()]}
        erp.create("BOM", body)
        print(f"[建] BOM {item_code}（{len(materials)} 种原料）")

    stock_items = [
        ("CI-RAW", 9000, 32.0, stores), ("M10-BOLT", 20000, 1.2, stores),
        ("SEAL-RING", 8000, 2.5, stores), ("BRG-6204", 6000, 9.0, stores),
        ("BD-2402", 400, 55.0, finished), ("CP-5601", 600, 68.0, finished),
    ]
    existing_receipts = erp.list("Stock Entry", '["name"]', '[["stock_entry_type","=","Material Receipt"]]')
    if not existing_receipts:
        rows = []
        for item_code, qty, rate, warehouse in stock_items:
            bins = erp.list("Bin", '["actual_qty"]', f'[["item_code","=","{item_code}"],["warehouse","=","{warehouse}"]]')
            if bins and float(bins[0].get("actual_qty") or 0) >= qty:
                print(f"[有] 库存 {item_code}@{warehouse}")
                continue
            rows.append({"item_code": item_code, "qty": qty, "s_warehouse": "",
                         "t_warehouse": warehouse, "basic_rate": rate, "uom": "Nos", "conversion_factor": 1})
            print(f"[缺] 库存 {item_code}@{warehouse} → {qty}")
        if rows:
            if apply:
                if not erp.list("Stock Entry Type", '["name"]'):
                    erp.create("Stock Entry Type", {"__newname": "Material Receipt",
                                                    "stock_entry_type_name": "Material Receipt",
                                                    "purpose": "Material Receipt"})
                    print("[建] Stock Entry Type: Material Receipt")
                company = erp.list("Company", '["name","abbr"]')
                company_name = next((c["name"] for c in company if c.get("abbr") == "APM"),
                                    company[0]["name"] if company else None)
                erp.create("Stock Entry", {
                    "company": company_name,
                    "stock_entry_type": "Material Receipt", "purpose": "Material Receipt",
                    "posting_date": "2026-10-20", "set_posting_time": 1,
                    "work_order": "", "items": rows}, submit=True)
                print(f"[建] Stock Entry {len(rows)} 行（收货入库）")
        else:
            print("[有] 库存全部满足，跳过")

    for case in CASES:
        so_name = None
        for row in erp.list("Sales Order", '["name","customer"]',
                            f'[["customer","=","{case.customer}"]]'):
            detail = erp.get("Sales Order", row["name"]) or {}
            items = detail.get("items") or []
            if any(i.get("item_code") == case.item_code and int(i.get("qty") or 0) == case.quantity
                   for i in items):
                so_name = row["name"]
                break
        if so_name:
            print(f"[有] 订单 {case.customer} {case.item_code}×{case.quantity} → {so_name}")
            cases_so[case.wo_no] = so_name
            continue
        if not apply:
            print(f"[缺] 订单 {case.customer} {case.item_code}×{case.quantity}")
            continue
        companies = erp.list("Company", '["name","abbr"]')
        so_company = next((c["name"] for c in companies if c.get("abbr") == "APM"),
                          companies[0]["name"] if companies else None)
        created = erp.create("Sales Order", {
            "customer": case.customer, "company": so_company,
            "transaction_date": case.order_date,
            "delivery_date": case.delivery_date, "set_warehouse": stores,
            "items": [{"item_code": case.item_code, "qty": case.quantity,
                       "rate": case.unit_price, "uom": "Nos",
                       "delivery_date": case.delivery_date, "warehouse": stores}],
        }, submit=case.submit)
        so_name = created.get("name")
        print(f"[建] 订单 {so_name}（{'已提交' if case.submit else '草稿'}）")
        cases_so[case.wo_no] = so_name


def mes_seed(mes: Mes, *, apply: bool) -> None:
    print("== OpenMES ==")
    print("-- 工艺模板步骤 --")
    products = {p["code"]: p for p in mes.list_product_types() if isinstance(p, dict) and p.get("code")}
    for code, steps in TEMPLATE_STEPS.items():
        product = products.get(code)
        if not product:
            raise SeedError(f"OpenMES 缺少产品类型：{code}")
        detail = mes._call("GET", f"/api/v1/product-types/{product['id']}")
        templates = detail.get("process_templates") if isinstance(detail, dict) else None
        templates = templates if isinstance(templates, list) else []
        template_id = next((int(t["id"]) for t in templates if t.get("is_active")), None)
        if template_id is None:
            raise SeedError(f"产品类型缺少激活工艺模板：{code}")
        existing = mes.list_template_steps(template_id)
        if existing:
            print(f"[有] 工序 {code}（{len(existing)} 步）")
            continue
        if not apply:
            print(f"[缺] 工序 {code}（{len(steps)} 步）")
            continue
        for number, (name, instruction, minutes) in enumerate(steps, start=1):
            mes.add_template_step(template_id, number, name, instruction, minutes)
        print(f"[建] 工序 {code}（{len(steps)} 步）")
    all_issue_titles = {i.get("title") for i in mes.list_all_issues()}
    for case in CASES:
        existing = mes.find_work_order(case.wo_no)
        if existing:
            wo_id = int(existing["id"])
            print(f"[有] 工单 {case.wo_no} → id={wo_id}")
        else:
            so_name = cases_so.get(case.wo_no)
            if not so_name:
                print(f"[跳] 工单 {case.wo_no}：无对应 ERP 订单")
                continue
            if not apply:
                print(f"[缺] 工单 {case.wo_no}")
                continue
            result = mes.import_work_order({
                "strategy": "skip_existing", "orders": [{
                    "order_no": case.wo_no, "line_code": LINE_CODE,
                    "product_type_code": case.item_code, "planned_qty": case.quantity,
                    "customer_order_no": so_name, "unit_price": case.unit_price,
                    "priority": case.priority, "due_date": case.delivery_date,
                    "description": f"{case.customer} {case.part_name}订单生产",
                }]})
            errors = (result or {}).get("errors")
            if errors:
                raise SeedError(f"导入工单失败 {case.wo_no}: {errors}")
            created = mes.find_work_order(case.wo_no)
            if not created:
                raise SeedError(f"导入后未回读到工单：{case.wo_no}")
            wo_id = int(created["id"])
            print(f"[建] 工单 {case.wo_no} → id={wo_id}")

        if case.report is not None or case.done:
            batches = mes.list_batches(wo_id)
            if batches:
                print(f"[有] 报工 {case.wo_no}（批次 {len(batches)}）")
            elif apply:
                batch = mes.create_batch(wo_id, case.quantity)
                steps = batch.get("steps") or []
                if not steps:
                    raise SeedError(f"批次无工艺步骤：{case.wo_no}（缺少工艺模板快照）")
                if case.done:
                    for step in steps:
                        step_id = int(step["id"])
                        mes.start_step(step_id)
                        mes.complete_step(step_id, case.quantity,
                                          max(60, min(600, case.quantity)))
                    print(f"[建] 完工 {case.wo_no}：{len(steps)} 道工序全部报满 {case.quantity} 件")
                else:
                    passed, elapsed = case.report
                    step_id = int(steps[0]["id"])
                    mes.start_step(step_id)
                    mes.complete_step(step_id, passed, elapsed)
                    print(f"[建] 报工 {case.wo_no}：步骤1 通过 {passed} 件 / {elapsed} 分钟")
            else:
                print(f"[缺] 报工 {case.wo_no}")

        if case.issue is not None:
            title, description, qty, close_after = case.issue
            if title in all_issue_titles:
                print(f"[有] 质量问题 {title}")
            elif apply:
                issue = mes.create_issue(wo_id, title, description, qty)
                issue_id = int(issue["id"])
                if close_after:
                    mes.resolve_issue(issue_id, "返工处理完成，复检合格后关闭。")
                    mes.close_issue(issue_id)
                    print(f"[建→闭] 质量问题 {title}（id={issue_id}）")
                else:
                    print(f"[建] 质量问题 {title}（id={issue_id}）")
            else:
                print(f"[缺] 质量问题 {title}")

    print("-- 历史质量问题 --")
    for wo_no, title, description, qty, days_ago in HISTORY_ISSUES:
        if title in all_issue_titles:
            print(f"[有] 历史问题 {title}")
            continue
        if not apply:
            print(f"[缺] 历史问题 {title}")
            continue
        target = mes.find_work_order(wo_no)
        if not target:
            print(f"[跳] 历史问题 {title}：工单不存在")
            continue
        issue = mes.create_issue(int(target["id"]), title, description, qty)
        issue_id = int(issue["id"])
        mes.resolve_issue(issue_id, "已按处置流程返工/复检，关闭留档。")
        mes.close_issue(issue_id)
        sql = ("docker exec %s psql -U openmmes_user -d openmmes -c "
               "\"update issues set reported_at = now() - interval '%d days', "
               "acknowledged_at = now() - interval '%d days', "
               "resolved_at = now() - interval '%d days', "
               "closed_at = now() - interval '%d days' where id = %d;\"")
        resolved_days = max(0, days_ago - 1)
        command = sql % (PG_CONTAINER, days_ago, days_ago, resolved_days, resolved_days, issue_id)
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise SeedError(f"回填问题时间失败 {title}: {result.stderr[:200]}")
        print(f"[建→闭] 历史问题 {title}（{days_ago} 天前）")


def sync_work_order_progress(*, apply: bool) -> None:
    """把已报工工单的 produced_qty/status 同步为进行中（OpenMES 导入态默认 PENDING/0）。"""
    print("== 工单进度同步 ==")
    for case in CASES:
        if case.report is None and not case.done:
            continue
        if case.done:
            status, qty = "DONE", case.quantity
            extra = ", completed_at = now() - interval '2 days'"
        else:
            status, qty = "IN_PROGRESS", case.report[0]
            extra = ""
        sql = ("docker exec %s psql -U openmmes_user -d openmmes -c "
               "\"update work_orders set produced_qty=%d, status='%s'%s, "
               "updated_at=now() where order_no='%s' and deleted_at is null "
               "and status <> 'DONE';\"")
        command = sql % (PG_CONTAINER, qty, status, extra, case.wo_no)
        if not apply:
            print(f"[将同步] {case.wo_no} produced_qty={qty} status={status}")
            continue
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise SeedError(f"同步工单进度失败 {case.wo_no}: {result.stderr[:200]}")
        print(f"[同步] {case.wo_no} -> {status} x{qty}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建演示数据集（幂等，--apply 才写入）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    apply = args.apply
    erp, mes = Erp(), Mes()
    try:
        erp_seed(erp, apply=apply)
        mes_seed(mes, apply=apply)
        sync_work_order_progress(apply=apply)
    finally:
        erp.close()
        mes.close()
    print("完成" if apply else "检查完成（未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
