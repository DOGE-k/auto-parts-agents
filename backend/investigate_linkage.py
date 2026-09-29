"""只读调查：确认真实 ERP 销售订单与真实 MES 工单之间的关联字段。

不写入任何系统，不打印任何凭据。只打印业务字段。
"""
import json
import os
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

ERP_BASE = os.getenv("ERPNEXT_BASE_URL", "http://localhost:8080")
ERP_AUTH = "token " + os.getenv("ERPNEXT_API_KEY", "") + ":" + os.getenv("ERPNEXT_API_SECRET", "")
MES_BASE = os.getenv("OPENMES_BASE_URL", "http://localhost")
MES_TOKEN = os.getenv("OPENMES_TOKEN", "")
MES_ERP_KEY = os.getenv("OPENMES_ERP_API_KEY", "")


def http_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, {"_error": raw[:500]}


def erp_list(doctype: str, fields: str, filters: str = "", limit: int = 50):
    q = f'?fields={fields}&limit_page_length={limit}'
    if filters:
        q += f'&filters={filters}'
    url = f"{ERP_BASE}/api/resource/{urllib.parse.quote(doctype)}{q}"
    return http_json(url, {"Authorization": ERP_AUTH})


def erp_doc(doctype: str, name: str):
    url = f"{ERP_BASE}/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
    return http_json(url, {"Authorization": ERP_AUTH})


def mes_get(path: str, use_erp_key: bool = False):
    headers = {"X-Api-Key": MES_ERP_KEY} if use_erp_key else {"Authorization": f"Bearer {MES_TOKEN}"}
    url = f"{MES_BASE}{path}"
    return http_json(url, headers)


def trim(d, maxlen=120):
    """缩短长字符串方便阅读。"""
    if isinstance(d, str) and len(d) > maxlen:
        return d[:maxlen] + "..."
    return d


print("=" * 70)
print("PART 1: ERPNext 销售订单（原始完整字段）")
print("=" * 70)
code, resp = erp_list("Sales Order", '["name"]')
print(f"HTTP {code}")
orders = resp.get("data", []) if isinstance(resp, dict) else []
order_names = [o["name"] for o in orders]
print(f"销售订单数量: {len(order_names)}: {order_names}")

for name in order_names:
    code, doc = erp_doc("Sales Order", name)
    print(f"\n--- Sales Order {name} (HTTP {code}) ---")
    data = doc.get("data", {}) if isinstance(doc, dict) else {}
    if not data:
        print(json.dumps(doc, ensure_ascii=False)[:500])
        continue
    # 打印所有标量字段
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool)) and v not in ("", None):
            print(f"  {k} = {trim(str(v))}")
    # 打印 items 子表关键字段
    for child in ("items", "sales_order_details"):
        if data.get(child):
            print(f"  [{child}]")
            for it in data[child]:
                print(f"    - " + json.dumps(
                    {kk: it.get(kk) for kk in ("item_code", "item_name", "qty", "rate", "amount",
                                                "delivery_date", "warehouse", "uom",
                                                "material_request", "production_plan", "prevdoc_docname")
                     if it.get(kk) not in (None, "")},
                    ensure_ascii=False))

print()
print("=" * 70)
print("PART 2: ERPNext Material Request（原始完整字段）")
print("=" * 70)
code, resp = erp_list("Material Request", '["name"]')
print(f"HTTP {code}")
mrs = [o["name"] for o in resp.get("data", [])] if isinstance(resp, dict) else []
print(f"物料需求数量: {len(mrs)}: {mrs}")
for name in mrs:
    code, doc = erp_doc("Material Request", name)
    print(f"\n--- Material Request {name} (HTTP {code}) ---")
    data = doc.get("data", {}) if isinstance(doc, dict) else {}
    if not data:
        continue
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool)) and v not in ("", None):
            print(f"  {k} = {trim(str(v))}")
    if data.get("items"):
        print("  [items]")
        for it in data["items"]:
            print(f"    - " + json.dumps(
                {kk: it.get(kk) for kk in ("item_code", "qty", "uom", "warehouse",
                                            "schedule_date", "sales_order", "production_plan",
                                            "project", "material_request_item")
                 if it.get(kk) not in (None, "")},
                ensure_ascii=False))

print()
print("=" * 70)
print("PART 3: ERPNext Work Order / Production Order doctype 是否存在记录")
print("=" * 70)
for dt in ("Work Order", "Production Order", "Job Card", "Manufacturing Order"):
    code, resp = erp_list(dt, '["name"]', limit=10)
    rows = resp.get("data", []) if isinstance(resp, dict) and code == 200 else None
    if rows is None:
        print(f"  {dt}: HTTP {code}（doctype 可能不存在）")
    else:
        print(f"  {dt}: {len(rows)} 条: {[r['name'] for r in rows]}")

print()
print("=" * 70)
print("PART 4: OpenMES 工单列表（原始完整字段，不做映射）")
print("=" * 70)
code, resp = mes_get("/api/v1/work-orders?per_page=50")
print(f"HTTP {code}")
payload = resp.get("data", resp) if isinstance(resp, dict) else {}
if isinstance(payload, dict):
    wo_list = payload.get("items") or payload.get("data") or payload.get("work_orders") or []
    if not wo_list and isinstance(payload.get("data"), list):
        wo_list = payload["data"]
else:
    wo_list = payload if isinstance(payload, list) else []
print(f"工单数量: {len(wo_list)}")
for wo in wo_list:
    print(f"\n--- work-order id={wo.get('id')} order_no={wo.get('order_no')} ---")
    print(json.dumps(wo, ensure_ascii=False, indent=2, default=str)[:2500])

print()
print("=" * 70)
print("PART 5: OpenMES 工单详情（每个工单的完整原始 JSON）")
print("=" * 70)
for wo in wo_list:
    wid = wo.get("id")
    code, detail = mes_get(f"/api/v1/work-orders/{wid}")
    print(f"\n--- detail id={wid} (HTTP {code}) ---")
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str)[:3500])

print()
print("=" * 70)
print("PART 6: OpenMES ERP 集成端点（production completions / quality issues）")
print("=" * 70)
code, resp = mes_get("/api/v1/erp/production/completions", use_erp_key=True)
print(f"production/completions HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:800]}")
code, resp = mes_get("/api/v1/erp/quality/issues", use_erp_key=True)
print(f"quality/issues HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:800]}")
