"""只读调查：ERP 中可替换报价/采购占位逻辑的真实业务字段。

检查：Item 采购字段、Item Supplier（物料-供应商关系）、Item Price 供应商价格、
BOM 成本字段、Company 默认仓库。不打印凭据。
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


def http_json(url: str):
    req = urllib.request.Request(url, headers={"Authorization": ERP_AUTH, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "replace")[:400]}


def erp_list(doctype: str, fields: str, limit: int = 50):
    q = f'?fields={fields}&limit_page_length={limit}'
    return http_json(f"{ERP_BASE}/api/resource/{urllib.parse.quote(doctype)}{q}")


def erp_doc(doctype: str, name: str):
    return http_json(f"{ERP_BASE}/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}")


print("=" * 72)
print("PART 1: Item 采购相关字段（真实值）")
print("=" * 72)
code, resp = erp_list("Item", '["name"]')
items = [r["name"] for r in resp.get("data", [])] if code == 200 else []
for item in items:
    code, doc = erp_doc("Item", item)
    d = doc.get("data", {}) if code == 200 else {}
    print(f"\n--- {item} ---")
    for k in ("min_order_qty", "lead_time_days", "purchase_uom", "default_material_request_type",
              "is_purchase_item", "is_sub_contracted_item", "default_bom", "valuation_rate",
              "standard_rate", "default_warehouse", "item_group", "stock_uom"):
        print(f"  {k} = {d.get(k)}")

print()
print("=" * 72)
print("PART 2: Item Supplier（物料-供应商关系，含交期/最小量字段）")
print("=" * 72)
for item in items:
    code, resp = erp_list("Item Supplier", '["name","parent","supplier","supplier_part_no","lead_time_days","min_order_qty","qty_per_unit"]', limit=100)
    if code == 200:
        rows = [r for r in resp.get("data", []) if r.get("parent") == item]
        if rows:
            print(f"\n--- {item} ---")
            for r in rows:
                print(f"  supplier={r.get('supplier')} | part_no={r.get('supplier_part_no')} | lead_time_days={r.get('lead_time_days')} | min_order_qty={r.get('min_order_qty')} | qty_per_unit={r.get('qty_per_unit')}")
        else:
            print(f"\n--- {item} --- (无 Item Supplier 记录)")
        break_flag = False

print()
print("=" * 72)
print("PART 3: Item Price 全部记录（检查是否有 supplier 字段值/价格有效期）")
print("=" * 72)
code, resp = erp_list("Item Price", '["name","item_code","price_list","price_list_rate","currency","valid_from","valid_upto","supplier","lead_time_days","min_qty","packing_unit","uom"]', limit=100)
if code == 200:
    for r in resp.get("data", []):
        print(f"  {r.get('name')} | {r.get('item_code')} | {r.get('price_list')} | {r.get('price_list_rate')} {r.get('currency')} | 有效 {r.get('valid_from')}~{r.get('valid_upto')} | supplier={r.get('supplier')} | lead_time_days={r.get('lead_time_days')} | min_qty={r.get('min_qty')} | uom={r.get('uom')}")
else:
    print(f"HTTP {code}: {resp}")

print()
print("=" * 72)
print("PART 4: BOM 成本字段（total_cost / items[].rate / source_warehouse）")
print("=" * 72)
code, resp = erp_list("BOM", '["name"]')
boms = [r["name"] for r in resp.get("data", [])] if code == 200 else []
for bom in boms:
    code, doc = erp_doc("BOM", bom)
    d = doc.get("data", {}) if code == 200 else {}
    print(f"\n--- {bom} (item={d.get('item')}) ---")
    for k in ("total_cost", "raw_material_cost", "operating_cost", "quantity", "rm_cost_as_per", "currency", "company"):
        print(f"  {k} = {d.get(k)}")
    for it in d.get("items", []):
        print(f"  子项 {it.get('item_code')}: qty={it.get('qty')} | rate={it.get('rate')} | amount={it.get('amount')} | source_warehouse={it.get('source_warehouse')} | uom={it.get('uom')}")
    for op in d.get("operations", []):
        print(f"  工序 {op.get('operation')}: cost={op.get('operating_cost')} | time={op.get('time_in_mins')}min | workstation={op.get('workstation')}")

print()
print("=" * 72)
print("PART 5: Company 默认值（公司/默认仓库/默认报价价格表）")
print("=" * 72)
code, resp = erp_list("Company", '["name"]')
comps = [r["name"] for r in resp.get("data", [])] if code == 200 else []
for comp in comps:
    code, doc = erp_doc("Company", comp)
    d = doc.get("data", {}) if code == 200 else {}
    print(f"\n--- {comp} (abbr={d.get('abbr')}) ---")
    for k in ("default_currency", "country", "domain"):
        print(f"  {k} = {d.get(k)}")

print()
print("=" * 72)
print("PART 6: Warehouse 列表")
print("=" * 72)
code, resp = erp_list("Warehouse", '["name","warehouse_type","company","is_group"]', limit=50)
if code == 200:
    for r in resp.get("data", []):
        print(f"  {r.get('name')} | type={r.get('warehouse_type')} | company={r.get('company')} | group={r.get('is_group')}")

print()
print("=" * 72)
print("PART 7: Supplier 完整字段（默认价格表/付款条件等）")
print("=" * 72)
code, resp = erp_list("Supplier", '["name"]')
sups = [r["name"] for r in resp.get("data", [])] if code == 200 else []
for sup in sups:
    code, doc = erp_doc("Supplier", sup)
    d = doc.get("data", {}) if code == 200 else {}
    print(f"\n--- {sup} ---")
    for k in ("supplier_group", "country", "default_price_list", "payment_terms", "default_currency"):
        print(f"  {k} = {d.get(k)}")
