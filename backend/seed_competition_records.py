"""Create three linked competition records in ERPNext, OpenMES and local PPAP.

This is a one-off, explicitly authorized data import, not an agent write tool.
All writes use the systems' normal HTTP APIs. It creates ERP Sales Order drafts,
OpenMES work orders and PPAP tasks; it never submits or approves documents.
Existing records are read and checked, never overwritten. No secret is printed.

Run on the application server from the project root:
    .venv/bin/python backend/seed_competition_records.py --check
    .venv/bin/python backend/seed_competition_records.py --apply
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import html
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit

import httpx
from dotenv import dotenv_values, load_dotenv


ROOT = Path(__file__).resolve().parents[1]
SEED_BATCH = "BUPT-AP-20261017"
LINE_CODE = "AP_LINE_01"


@dataclass(frozen=True)
class Case:
    suffix: str
    customer_name: str
    item_code: str
    part_name: str
    quantity: int
    unit_price: int
    delivery_date: str

    @property
    def po_no(self) -> str:
        return f"{SEED_BATCH}-{self.suffix}"

    @property
    def work_order_no(self) -> str:
        return f"WO-BUPT-261017-{self.suffix}"

    @property
    def authorization_ref(self) -> str:
        return f"比赛业务数据建立:{self.po_no}"


CASES = (
    Case("01", "上汽集团", "BD-2401", "制动盘-前轮", 500, 85, "2026-11-20"),
    Case("02", "比亚迪汽车工业有限公司", "SK-3401", "转向节", 300, 120, "2026-11-27"),
    Case("03", "长城汽车股份有限公司", "TS-4501", "传动轴总成", 200, 350, "2026-12-04"),
)


class SeedError(RuntimeError):
    """Error text deliberately contains no response body, headers or credentials."""


def erp_validation_hint(response: httpx.Response) -> str:
    """Extract one short Frappe validation message without logging the response body."""
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            return ""
        raw_messages = payload.get("_server_messages") or []
        messages = json.loads(raw_messages) if isinstance(raw_messages, str) else raw_messages
        first = messages[0] if isinstance(messages, list) and messages else None
        entry = json.loads(first) if isinstance(first, str) else first
        message = entry.get("message", "") if isinstance(entry, dict) else ""
        if not message:
            message = payload.get("message", "")
        if not isinstance(message, str):
            return ""
        message = html.unescape(re.sub(r"<[^>]*>", " ", message))
        message = " ".join(message.split())[:300]
        for key in ("ERPNEXT_API_KEY", "ERPNEXT_API_SECRET", "OPENMES_TOKEN", "OPENMES_ERP_API_KEY"):
            secret = os.getenv(key, "")
            if secret:
                message = message.replace(secret, "[隐藏]")
        return message
    except (ValueError, TypeError, KeyError, IndexError):
        return ""


def local_base(value: str, name: str) -> str:
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise SeedError(f"{name} 必须是服务器本机 HTTP 地址")
    return value.rstrip("/")


class Api:
    def __init__(self, base_url: str, name: str, *, headers: dict | None = None):
        self.name = name
        self.client = httpx.Client(base_url=local_base(base_url, name) + "/",
                                   headers={"Accept": "application/json", **(headers or {})},
                                   timeout=25, trust_env=False, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    def call(self, method: str, path: str, *, params: dict | None = None,
             body: dict | None = None, headers: dict | None = None) -> dict:
        try:
            response = self.client.request(method, path.lstrip("/"), params=params, json=body,
                                           headers=headers)
        except httpx.HTTPError as exc:
            raise SeedError(f"{self.name} {method} {path} 连接失败：{type(exc).__name__}") from None
        if response.status_code >= 300:
            location = response.headers.get("Location", "")
            parsed = urlsplit(location)
            # A redirect can contain a login token in its query string. Show only
            # the destination path and host needed for troubleshooting.
            destination = f"{parsed.scheme}://{parsed.hostname}{parsed.path}" if parsed.hostname else parsed.path
            suffix = f"，跳转至 {destination}" if 300 <= response.status_code < 400 and destination else ""
            if self.name == "ERPNext" and response.status_code == 417:
                hint = erp_validation_hint(response)
                suffix = f"，校验提示：{hint}" if hint else ""
            raise SeedError(f"{self.name} {method} {path} 返回 HTTP {response.status_code}{suffix}")
        try:
            result = response.json()
        except ValueError:
            raise SeedError(f"{self.name} {path} 未返回 JSON") from None
        if not isinstance(result, dict):
            raise SeedError(f"{self.name} {path} 返回结构异常")
        return result


class Erp:
    def __init__(self):
        key = os.getenv("ERPNEXT_API_KEY", "").strip()
        secret = os.getenv("ERPNEXT_API_SECRET", "").strip()
        if not key or not secret:
            raise SeedError("ERPNext API 凭据未配置")
        self.api = Api(os.getenv("ERPNEXT_BASE_URL", ""), "ERPNext",
                       headers={"Authorization": f"token {key}:{secret}"})

    def close(self):
        self.api.close()

    def list(self, doctype: str, fields: list[str], filters: list | None = None) -> list[dict]:
        import json
        result = self.api.call("GET", f"api/resource/{quote(doctype, safe='')}", params={
            "fields": json.dumps(fields, ensure_ascii=False),
            "filters": json.dumps(filters or [], ensure_ascii=False),
            "limit_page_length": 100,
        })
        rows = result.get("data")
        if not isinstance(rows, list):
            raise SeedError(f"ERPNext {doctype} 列表结构异常")
        return rows

    def get(self, doctype: str, name: str) -> dict:
        result = self.api.call("GET", f"api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}")
        if not isinstance(result.get("data"), dict):
            raise SeedError(f"ERPNext {doctype} 回读结构异常")
        return result["data"]

    def preflight(self) -> dict[str, str]:
        user = self.api.call("GET", "api/method/frappe.auth.get_logged_user").get("message")
        if not user or user == "Guest":
            raise SeedError("ERPNext 接口账号没有登录")
        companies = self.list("Company", ["name"])
        company = next((row["name"] for row in companies if row.get("name") == "AutoParts Manufacturing"), None)
        if not company:
            raise SeedError("ERPNext 缺少 AutoParts Manufacturing 公司，请先准备基础资料")
        warehouses = self.list("Warehouse", ["name", "is_group"])
        warehouse = next((row["name"] for row in warehouses
                          if row.get("name") == "Stores - APM" and not row.get("is_group")), None)
        if not warehouse:
            warehouse = next((row["name"] for row in warehouses if not row.get("is_group")), None)
        if not warehouse:
            raise SeedError("ERPNext 没有可用的叶子仓库，无法创建销售订单草稿")
        customers = self.list("Customer", ["name", "customer_name"])
        items = self.list("Item", ["name", "item_code"])
        result = {"company": company, "warehouse": warehouse}
        for case in CASES:
            customer = next((row["name"] for row in customers
                             if row.get("customer_name") == case.customer_name or row.get("name") == case.customer_name), None)
            if not customer:
                raise SeedError(f"ERPNext 缺少客户：{case.customer_name}")
            if not any(row.get("item_code") == case.item_code for row in items):
                raise SeedError(f"ERPNext 缺少物料：{case.item_code}")
            result[case.suffix] = customer
        # Validate Sales Order read access before the first write.
        self.list("Sales Order", ["name"], [["po_no", "=", CASES[0].po_no]])
        return result

    def ensure_fiscal_year(self, company: str, *, apply: bool) -> str | None:
        """Make the order date valid without changing an existing fiscal year."""
        today = date.today()
        rows = self.list("Fiscal Year", ["name", "year_start_date", "year_end_date", "disabled"])
        covering = [row for row in rows
                    if str(row.get("year_start_date") or "") <= today.isoformat()
                    <= str(row.get("year_end_date") or "")]
        for row in covering:
            detail = self.get("Fiscal Year", row["name"])
            companies = [entry.get("company") for entry in detail.get("companies") or []]
            if not companies or company in companies:
                if row.get("disabled") in (0, False, None):
                    return str(row["name"])
                raise SeedError(f"ERPNext 已有覆盖今天的会计年度 {row['name']}，但它已停用；不会修改既有配置")
        if not apply:
            return None
        year_name = str(today.year)
        if any(row.get("name") == year_name for row in rows):
            year_name = f"{today.year}-APM"
        if any(row.get("name") == year_name for row in rows):
            raise SeedError(f"ERPNext 会计年度名称 {year_name} 已占用；不会覆盖")
        payload = {"year": year_name, "year_start_date": f"{today.year}-01-01",
                   "year_end_date": f"{today.year}-12-31", "disabled": 0,
                   "companies": [{"company": company}]}
        data = self.api.call("POST", "api/resource/Fiscal%20Year", body=payload).get("data")
        if not isinstance(data, dict) or not data.get("name"):
            raise SeedError("ERPNext 已发送会计年度创建请求，但未返回编号；请回读检查")
        print(f"ERPNext 创建会计年度 {data['name']}，正在回读")
        detail = self.get("Fiscal Year", data["name"])
        if (detail.get("disabled") not in (0, False, None)
                or str(detail.get("year_start_date")) != payload["year_start_date"]
                or str(detail.get("year_end_date")) != payload["year_end_date"]
                or company not in [entry.get("company") for entry in detail.get("companies") or []]):
            raise SeedError("ERPNext 会计年度回读不一致")
        return str(data["name"])

    def find_order(self, case: Case) -> dict | None:
        rows = self.list("Sales Order", ["name", "po_no"], [["po_no", "=", case.po_no]])
        if len(rows) > 1:
            raise SeedError(f"ERPNext 出现重复的客户采购单号：{case.po_no}")
        return self.get("Sales Order", rows[0]["name"]) if rows else None

    @staticmethod
    def verify_order(order: dict, case: Case, customer: str) -> str:
        items = order.get("items") or []
        if (order.get("po_no") != case.po_no or order.get("customer") != customer
                or len(items) != 1 or items[0].get("item_code") != case.item_code
                or float(items[0].get("qty") or 0) != case.quantity
                or str(order.get("delivery_date")) != case.delivery_date
                or order.get("docstatus") not in (0, 1)):
            raise SeedError(f"ERPNext 已有订单与本批数据不一致：{case.po_no}；不会覆盖")
        return str(order["name"])

    def ensure_order(self, case: Case, customer: str, company: str, warehouse: str,
                     *, apply: bool) -> str | None:
        existing = self.find_order(case)
        if existing:
            return self.verify_order(existing, case, customer)
        if not apply:
            return None
        payload = {
            "customer": customer, "company": company, "currency": "CNY",
            "transaction_date": date.today().isoformat(),
            "delivery_date": case.delivery_date, "po_no": case.po_no,
            "po_date": date.today().isoformat(), "docstatus": 0,
            "items": [{"item_code": case.item_code, "qty": case.quantity,
                       "rate": case.unit_price, "uom": "Nos", "delivery_date": case.delivery_date,
                       "warehouse": warehouse}],
        }
        data = self.api.call("POST", "api/resource/Sales%20Order", body=payload).get("data")
        if not isinstance(data, dict) or not data.get("name"):
            raise SeedError(f"ERPNext 已发送创建请求但没有订单号：{case.po_no}；请回读检查")
        print(f"ERPNext 创建订单 {case.po_no} -> {data['name']}，正在回读")
        return self.verify_order(self.get("Sales Order", data["name"]), case, customer)


class Mes:
    def __init__(self):
        self.api = Api(os.getenv("OPENMES_BASE_URL", ""), "OpenMES")
        self.token = os.getenv("OPENMES_TOKEN", "").strip()
        self.erp_key = os.getenv("OPENMES_ERP_API_KEY", "").strip()
        if not self.erp_key:
            raise SeedError("OpenMES ERP 导入 Key 未配置")

    def close(self):
        self.api.close()

    def preflight(self) -> None:
        if self.token:
            try:
                self.list_work_orders("WO-BUPT-261017")
            except SeedError as exc:
                if "HTTP 401" not in str(exc):
                    raise
                self.token = ""
        if not self.token:
            openmes_env = dotenv_values(ROOT / "services" / "OpenMes" / ".env")
            username = str(openmes_env.get("ADMIN_USERNAME") or "").strip()
            password = str(openmes_env.get("ADMIN_PASSWORD") or "").strip()
            if not username or not password:
                raise SeedError("OpenMES 用户令牌无效且管理员登录配置不可用")
            response = self.api.call("POST", "api/auth/login", body={"username": username, "password": password})
            self.token = str((response.get("data") or {}).get("token") or "")
            if not self.token:
                raise SeedError("OpenMES 登录成功但未返回用户令牌")
            self.list_work_orders("WO-BUPT-261017")
        auth = {"Authorization": f"Bearer {self.token}"}
        lines = self.api.call("GET", "api/v1/lines", headers=auth).get("data")
        if not isinstance(lines, list):
            raise SeedError("OpenMES 产线列表结构异常")
        products = self.api.call("GET", "api/v1/product-types", headers=auth).get("data")
        if not isinstance(products, list):
            raise SeedError("OpenMES 产品类型列表结构异常")
        for case in CASES:
            product = next((row for row in products if row.get("code") == case.item_code), None)
            if not product:
                raise SeedError(f"OpenMES 缺少产品类型：{case.item_code}")
            detail = self.api.call("GET", f"api/v1/product-types/{product['id']}", headers=auth).get("data")
            templates = detail.get("process_templates") if isinstance(detail, dict) else None
            if not isinstance(templates, list) or not any(row.get("is_active") for row in templates):
                raise SeedError(f"OpenMES 产品类型缺少激活的工艺模板：{case.item_code}")

    def ensure_line(self, *, apply: bool) -> bool:
        auth = {"Authorization": f"Bearer {self.token}"}
        rows = self.api.call("GET", "api/v1/lines", headers=auth).get("data")
        if not isinstance(rows, list):
            raise SeedError("OpenMES 产线列表结构异常")
        matches = [row for row in rows if row.get("code") == LINE_CODE]
        if len(matches) > 1:
            raise SeedError(f"OpenMES 产线编号重复：{LINE_CODE}")
        if matches:
            if not matches[0].get("is_active"):
                raise SeedError(f"OpenMES 产线已停用：{LINE_CODE}；不会修改")
            return True
        if not apply:
            return False
        self.api.call("POST", "api/v1/lines", headers=auth,
                      body={"code": LINE_CODE, "name": "汽车零部件装配线", "is_active": True})
        print(f"OpenMES 创建产线 {LINE_CODE}，正在回读")
        return self.ensure_line(apply=False)

    def list_work_orders(self, search: str) -> list[dict]:
        result = self.api.call("GET", "api/v1/work-orders",
                               params={"search": search, "per_page": 100},
                               headers={"Authorization": f"Bearer {self.token}"})
        rows = result.get("data")
        if not isinstance(rows, list):
            raise SeedError("OpenMES 工单列表结构异常")
        return rows

    def find_work_order(self, case: Case) -> dict | None:
        rows = [row for row in self.list_work_orders(case.work_order_no)
                if row.get("order_no") == case.work_order_no]
        if len(rows) > 1:
            raise SeedError(f"OpenMES 工单号重复：{case.work_order_no}")
        return rows[0] if rows else None

    @staticmethod
    def verify_work_order(order: dict, case: Case, erp_order_id: str) -> int:
        if (order.get("order_no") != case.work_order_no
                or order.get("customer_order_no") != erp_order_id
                or float(order.get("planned_qty") or order.get("quantity") or 0) != case.quantity):
            raise SeedError(f"OpenMES 已有工单与 ERP 订单不一致：{case.work_order_no}；不会覆盖")
        return int(order["id"])

    def ensure_work_order(self, case: Case, erp_order_id: str, *, apply: bool) -> int | None:
        existing = self.find_work_order(case)
        if existing:
            return self.verify_work_order(existing, case, erp_order_id)
        if not apply:
            return None
        body = {"strategy": "skip_existing", "orders": [{
            "order_no": case.work_order_no, "line_code": LINE_CODE,
            "product_type_code": case.item_code, "planned_qty": case.quantity,
            "customer_order_no": erp_order_id, "unit_price": case.unit_price,
            "priority": 5, "due_date": case.delivery_date,
            "description": f"{case.customer_name} {case.part_name}订单生产",
        }]}
        result = self.api.call("POST", "api/v1/erp/work-orders/import", body=body,
                               headers={"X-Api-Key": self.erp_key})
        data = result.get("data") or {}
        if data.get("errors") or data.get("updated"):
            raise SeedError(f"OpenMES 导入工单返回错误或修改了已有记录：{case.work_order_no}")
        print(f"OpenMES 导入工单 {case.work_order_no}，正在回读")
        order = self.find_work_order(case)
        if not order:
            raise SeedError(f"OpenMES 未回读到工单：{case.work_order_no}")
        return self.verify_work_order(order, case, erp_order_id)


class Ppap:
    def __init__(self, base_url: str):
        self.api = Api(base_url, "PPAP")

    def close(self):
        try:
            self.api.call("POST", "api/auth/logout")
        except SeedError:
            pass
        self.api.close()

    def preflight(self) -> None:
        username = os.getenv("PROJECT_AUTH_USERNAME", "admin").strip()
        password = os.getenv("PROJECT_AUTH_PASSWORD", "").strip()
        if not username or not password:
            raise SeedError("项目登录账号未配置，PPAP 任务无法创建")
        result = self.api.call("POST", "api/auth/login", body={"username": username, "password": password})
        roles = (result.get("identity") or {}).get("roles") or []
        if "quality_manager" not in roles:
            raise SeedError("项目账号没有 PPAP 质量管理员权限")
        self.api.call("GET", "api/ppap/tasks", params={"limit": 1})

    def find_task(self, case: Case, customer: str) -> dict | None:
        result = self.api.call("GET", "api/ppap/tasks", params={
            "part_no": case.item_code, "customer_id": customer, "limit": 200,
        })
        rows = [row for row in result.get("items", [])
                if row.get("authorization_ref") == case.authorization_ref]
        if len(rows) > 1:
            raise SeedError(f"PPAP 任务批次重复：{case.item_code}")
        return rows[0] if rows else None

    def ensure_task(self, case: Case, customer: str, erp_order_id: str,
                    work_order_id: int, *, apply: bool) -> str | None:
        task = self.find_task(case, customer)
        if not task and not apply:
            return None
        if not task:
            body = {"mode": "REAL", "customer_id": customer,
                    "customer_name": case.customer_name, "part_no": case.item_code,
                    "part_name": case.part_name, "part_side": "NONE",
                    "drawing_version": "A", "submission_reason": "新产品导入",
                    "authorization_ref": case.authorization_ref,
                    "due_date": case.delivery_date}
            result = self.api.call("POST", "api/ppap/tasks", body=body)
            task = result.get("task") or {}
            if not task.get("task_id"):
                raise SeedError(f"PPAP 未返回新任务编号：{case.item_code}")
            print(f"PPAP 创建任务 {case.item_code} -> {task['task_id']}，正在关联订单和工单")
        task_id = str(task["task_id"])
        detail = self.api.call("GET", f"api/ppap/tasks/{quote(task_id, safe='')}").get("task") or {}
        if detail.get("authorization_ref") != case.authorization_ref:
            raise SeedError(f"PPAP 任务来源不一致：{task_id}")
        if detail.get("data_mode") != "REAL" or detail.get("customer_id") != customer:
            raise SeedError(f"PPAP 已有任务模式或客户不一致：{task_id}；不会覆盖")
        links = detail.get("business_object") or {}
        desired = {"erp_order_id": erp_order_id, "work_order_no": case.work_order_no,
                   "work_order_id": str(work_order_id)}
        conflicting = [key for key, value in desired.items() if links.get(key) not in (None, "", value)]
        if conflicting:
            raise SeedError(f"PPAP 已有不同业务关联：{task_id}，不会覆盖")
        if apply and any(links.get(key) != value for key, value in desired.items()):
            self.api.call("POST", f"api/ppap/tasks/{quote(task_id, safe='')}", body={
                "patch": desired, "note": "关联 ERP 销售订单与 OpenMES 生产工单"})
            detail = self.api.call("GET", f"api/ppap/tasks/{quote(task_id, safe='')}").get("task") or {}
            if any((detail.get("business_object") or {}).get(key) != value for key, value in desired.items()):
                raise SeedError(f"PPAP 业务关联回读不一致：{task_id}")
        return task_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建立 ERP、MES、PPAP 三套可查询的比赛业务记录")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只读检查连接、基础资料和已有记录")
    mode.add_argument("--apply", action="store_true", help="创建缺失记录并回读验证")
    parser.add_argument("--ppap-base", default="http://127.0.0.1:9000")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "backend" / ".env", override=False)
    if os.getenv("APP_ADAPTER_MODE", "").strip().lower() != "real":
        print("当前 APP_ADAPTER_MODE 不是 real，停止", file=sys.stderr)
        return 1
    erp = mes = ppap = None
    try:
        erp, mes, ppap = Erp(), Mes(), Ppap(args.ppap_base)
        master = erp.preflight()
        mes.preflight()
        ppap.preflight()
        print("ERPNext、OpenMES、PPAP 已通过读权限与基础资料检查")
        fiscal = erp.ensure_fiscal_year(master["company"], apply=args.apply)
        print(f"ERPNext 当前会计年度：{fiscal or '待创建'}")
        line_ready = mes.ensure_line(apply=args.apply)
        print(f"OpenMES 产线 {LINE_CODE}: {'已存在或已创建' if line_ready else '待创建'}")
        if args.apply and not line_ready:
            raise SeedError(f"OpenMES 产线创建后未能回读：{LINE_CODE}")
        for case in CASES:
            customer = master[case.suffix]
            order_id = erp.ensure_order(case, customer, master["company"], master["warehouse"],
                                        apply=args.apply)
            work_order_id = (mes.ensure_work_order(case, order_id, apply=args.apply)
                             if order_id else None)
            task_id = (ppap.ensure_task(case, customer, order_id, work_order_id, apply=args.apply)
                       if order_id and work_order_id is not None else None)
            print(f"{case.suffix}: ERP={order_id or '待创建'} | "
                  f"MES={case.work_order_no if work_order_id else '待创建'} | "
                  f"PPAP={task_id or '待创建'}")
        if args.apply:
            print("完成：3 条 ERP 销售订单、3 条 MES 工单、3 条 PPAP 任务已逐项回读。订单保持草稿，PPAP 保持待编制。")
        else:
            print("只读检查完成；未写入任何业务数据。")
        return 0
    except (SeedError, KeyError, ValueError, TypeError) as exc:
        message = str(exc) if isinstance(exc, SeedError) else type(exc).__name__
        suffix = "；可能已有部分新记录，可用 --check 核对后重跑 --apply" if args.apply else "；只读检查没有创建记录"
        print(f"停止：{message}{suffix}", file=sys.stderr)
        return 1
    finally:
        for client in (ppap, mes, erp):
            if client is not None:
                client.close()


if __name__ == "__main__":
    raise SystemExit(main())
