"""Seed ERPNext: create reference data first, then master data, then business data."""
import json
import os
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

BASE_URL = os.getenv("ERPNEXT_BASE_URL", "http://localhost:8080")
API_KEY = os.getenv("ERPNEXT_API_KEY", "")
API_SECRET = os.getenv("ERPNEXT_API_SECRET", "")
AUTH = f"token {API_KEY}:{API_SECRET}" if API_KEY and API_SECRET else ""

def api(path, method="GET", body=None):
    encoded = urllib.parse.quote(path, safe="/?=&[]")
    url = f"{BASE_URL}/api/resource/{encoded}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", AUTH)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except:
            return e.code, {"raw": raw[:300]}

def create(doctype, data, name_field):
    code, resp = api(doctype, "POST", data)
    if code in (200, 201):
        name = resp.get("data", {}).get("name", "")
        print(f"    OK: {doctype} '{data.get(name_field,'')}' -> {name}")
        return name
    else:
        exc = resp.get("exception", resp.get("raw", ""))[:150]
        print(f"    SKIP: {doctype} '{data.get(name_field,'')}' -> {code}: {exc}")
        return None

def main():
    print("=== ERPNext Reference Data + Master Data + Business Data ===\n")

    # 1. Create UOMs
    print("[1] Creating UOMs ...")
    for uom in ["Nos", "Unit", "Box", "Kg", "Meter"]:
        create("UOM", {"uom_name": uom, "name": uom}, "uom_name")

    # 2. Create Warehouse Types
    print("\n[2] Creating warehouse types ...")
    for wt in ["Transit", "Stores", "Finished Goods"]:
        create("Warehouse Type", {"warehouse_type": wt, "name": wt}, "warehouse_type")

    # 3. Create root tree nodes
    print("\n[3] Creating root tree nodes ...")
    create("Customer Group", {"customer_group_name": "All Customer Groups", "is_group": 1}, "customer_group_name")
    create("Item Group", {"item_group_name": "All Item Groups", "is_group": 1}, "item_group_name")
    create("Territory", {"territory_name": "All Territories", "is_group": 1}, "territory_name")

    # 4. Create Company
    print("\n[4] Creating company ...")
    create("Company", {
        "company_name": "AutoParts Manufacturing",
        "abbr": "APM",
        "default_currency": "CNY",
        "country": "China",
        "domain": "Manufacturing",
    }, "company_name")

    # 5. Create sub-groups
    print("\n[5] Creating sub-groups ...")
    create("Item Group", {"item_group_name": "Products", "is_group": 0, "parent_item_group": "All Item Groups"}, "item_group_name")
    create("Item Group", {"item_group_name": "Raw Material", "is_group": 0, "parent_item_group": "All Item Groups"}, "item_group_name")

    # 6. Create Price Lists
    print("\n[6] Creating price lists ...")
    create("Price List", {"price_list_name": "Standard Selling", "selling": 1, "enabled": 1, "currency": "CNY"}, "price_list_name")
    create("Price List", {"price_list_name": "Standard Buying", "buying": 1, "enabled": 1, "currency": "CNY"}, "price_list_name")

    # 7. Create Items
    print("\n[7] Creating items ...")
    items = [
        {"item_code": "BD-2401", "item_name": "制动盘-前轮 Brake Disc Front", "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "BD-2402", "item_name": "制动盘-后轮 Brake Disc Rear", "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "SK-3401", "item_name": "转向节 Steering Knuckle", "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "TS-4501", "item_name": "传动轴总成 Transmission Shaft", "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "CP-5601", "item_name": "离合器片 Clutch Plate", "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "CI-RAW", "item_name": "铸铁毛坯 Cast Iron Blank", "item_group": "Raw Material", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "M10-BOLT", "item_name": "螺栓M10x40 Bolt M10x40", "item_group": "Raw Material", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "SEAL-RING", "item_name": "密封圈 Seal Ring", "item_group": "Raw Material", "stock_uom": "Nos", "is_stock_item": 1},
        {"item_code": "BRG-6204", "item_name": "轴承6204 Bearing 6204", "item_group": "Raw Material", "stock_uom": "Nos", "is_stock_item": 1},
    ]
    for item in items:
        create("Item", item, "item_code")

    # 8. Create Customers
    print("\n[8] Creating customers ...")
    customers = [
        {"customer_name": "上汽集团", "customer_group": "All Customer Groups", "territory": "All Territories", "customer_type": "Company"},
        {"customer_name": "比亚迪汽车工业有限公司", "customer_group": "All Customer Groups", "territory": "All Territories", "customer_type": "Company"},
        {"customer_name": "长城汽车股份有限公司", "customer_group": "All Customer Groups", "territory": "All Territories", "customer_type": "Company"},
    ]
    for c in customers:
        create("Customer", c, "customer_name")

    # 9. Create Suppliers
    print("\n[9] Creating suppliers ...")
    for s in [
        {"supplier_name": "上海铸锻厂", "supplier_group": "All Supplier Groups", "country": "China"},
        {"supplier_name": "宁波紧固件有限公司", "supplier_group": "All Supplier Groups", "country": "China"},
        {"supplier_name": "江苏轴承制造有限公司", "supplier_group": "All Supplier Groups", "country": "China"},
    ]:
        create("Supplier", s, "supplier_name")

    # 10. Create BOM
    print("\n[10] Creating BOM ...")
    bom_data = {
        "item": "BD-2401", "quantity": 1, "is_active": 1, "is_default": 1,
        "items": [
            {"item_code": "CI-RAW", "qty": 1, "uom": "Nos"},
            {"item_code": "M10-BOLT", "qty": 4, "uom": "Nos"},
            {"item_code": "SEAL-RING", "qty": 1, "uom": "Nos"},
            {"item_code": "BRG-6204", "qty": 2, "uom": "Nos"},
        ]
    }
    create("BOM", bom_data, "item")

    # 11. Create Item Prices
    print("\n[11] Creating item prices ...")
    for p in [
        {"item_code": "BD-2401", "price_list": "Standard Selling", "price_list_rate": 85.00, "currency": "CNY"},
        {"item_code": "BD-2402", "price_list": "Standard Selling", "price_list_rate": 78.00, "currency": "CNY"},
        {"item_code": "SK-3401", "price_list": "Standard Selling", "price_list_rate": 120.00, "currency": "CNY"},
        {"item_code": "TS-4501", "price_list": "Standard Selling", "price_list_rate": 350.00, "currency": "CNY"},
        {"item_code": "CP-5601", "price_list": "Standard Selling", "price_list_rate": 65.00, "currency": "CNY"},
        {"item_code": "CI-RAW", "price_list": "Standard Buying", "price_list_rate": 12.50, "currency": "CNY"},
        {"item_code": "M10-BOLT", "price_list": "Standard Buying", "price_list_rate": 0.80, "currency": "CNY"},
        {"item_code": "SEAL-RING", "price_list": "Standard Buying", "price_list_rate": 3.50, "currency": "CNY"},
        {"item_code": "BRG-6204", "price_list": "Standard Buying", "price_list_rate": 8.20, "currency": "CNY"},
    ]:
        create("Item Price", p, "item_code")

    # 12. Verify
    print("\n[12] Verification ...")
    for dt in ["Company", "Customer", "Item", "Supplier", "BOM", "Item Price", "Warehouse", "UOM", "Price List"]:
        path = f'{dt}?fields=["name"]&limit_page_length=50'
        code, resp = api(path)
        if code == 200:
            count = len(resp.get("data", []))
            print(f"    {dt}: {count}")
        else:
            print(f"    {dt}: HTTP {code}")

    print("\nDone.")

if __name__ == "__main__":
    main()
