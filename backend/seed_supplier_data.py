"""向真实 ERPNext 补录供应商业务测试数据（用户 2026-09-28 明确批准）。

写入内容：
1. Item.supplier_items（Item Supplier 物料-供应商关系）
2. Item.lead_time_days / Item.min_order_qty（物料级交期与最小采购量）
3. Item Price（Buying，supplier 字段=具体供应商）供应商特定价格

注：本 ERPNext 版本的 Item Supplier 子表没有 lead_time_days 字段
（实测 417 Field not permitted），因此交期存储在 Item.lead_time_days（物料级）。

不打印任何凭据。幂等：重复运行会跳过已存在记录。
"""
import json
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

BASE = os.getenv("ERPNEXT_BASE_URL", "http://localhost:8080")
AUTH = "token " + os.getenv("ERPNEXT_API_KEY", "") + ":" + os.getenv("ERPNEXT_API_SECRET", "")

TODAY = date.today().isoformat()

# 物料 → (lead_time_days, min_order_qty, [(供应商, 供应商特定买价)])
SUPPLIER_DATA = {
    "CI-RAW": (12, 100, [("上海铸锻厂", 12.50), ("江苏轴承制造有限公司", 13.40)]),
    "M10-BOLT": (7, 500, [("宁波紧固件有限公司", 0.80), ("上海铸锻厂", 0.86)]),
    "SEAL-RING": (10, 100, [("宁波紧固件有限公司", 3.50), ("上海铸锻厂", 3.68)]),
    "BRG-6204": (15, 50, [("江苏轴承制造有限公司", 8.20), ("上海铸锻厂", 8.60)]),
}


def api(path: str, method: str = "GET", body=None):
    url = f"{BASE}/api/resource/{urllib.parse.quote(path, safe='/?=&[]')}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", AUTH)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "replace")[:300]}


def main():
    print("=== 补录供应商业务测试数据（已获用户批准） ===\n")

    for item_code, (lead, moq, supplier_prices) in SUPPLIER_DATA.items():
        print(f"--- {item_code} ---")

        # 1. 读当前 Item
        code, resp = api(f"Item/{item_code}")
        if code != 200:
            print(f"    SKIP: 读取 Item 失败 HTTP {code}")
            continue
        doc = resp["data"]

        # 2. 更新 lead_time_days / min_order_qty
        need_update = doc.get("lead_time_days", 0) != lead or float(doc.get("min_order_qty", 0) or 0) != moq
        if need_update:
            code, resp = api(f"Item/{item_code}", "PUT", {"lead_time_days": lead, "min_order_qty": moq})
            print(f"    lead_time_days={lead}, min_order_qty={moq}: {'OK' if code == 200 else f'HTTP {code}'}")
        else:
            print(f"    lead_time_days/min_order_qty 已配置，跳过")

        # 3. Item Supplier 关系（幂等：按供应商名去重）
        existing = {r.get("supplier") for r in (doc.get("supplier_items") or [])}
        to_add = [{"supplier": s} for s, _ in supplier_prices if s not in existing]
        if to_add:
            new_list = (doc.get("supplier_items") or []) + to_add
            code, resp = api(f"Item/{item_code}", "PUT", {"supplier_items": new_list})
            print(f"    supplier_items +{[r['supplier'] for r in to_add]}: {'OK' if code == 200 else f'HTTP {code}: {resp}'}")
        else:
            print(f"    supplier_items 已存在，跳过")

        # 4. 供应商特定 Item Price（幂等：查重）
        for supplier, price in supplier_prices:
            code, resp = api(
                "Item Price?filters=" + urllib.parse.quote(
                    json.dumps([["item_code", "=", item_code], ["supplier", "=", supplier]]), safe=""
                ) + '&limit_page_length=5'
            )
            found = resp.get("data", []) if code == 200 else []
            if found:
                print(f"    Item Price {supplier} @{price}: 已存在({found[0]['name']})，跳过")
                continue
            code, resp = api("Item Price", "POST", {
                "item_code": item_code,
                "price_list": "Standard Buying",
                "price_list_rate": price,
                "currency": "CNY",
                "supplier": supplier,
                "valid_from": TODAY,
            })
            if code in (200, 201):
                print(f"    Item Price {supplier} @{price}: OK -> {resp.get('data', {}).get('name')}")
            else:
                print(f"    Item Price {supplier} @{price}: HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:150]}")

    # 5. 回读验证
    print("\n=== 回读验证 ===")
    for item_code, (_, _, _) in SUPPLIER_DATA.items():
        code, resp = api(f"Item/{item_code}")
        d = resp.get("data", {}) if code == 200 else {}
        sups = [r.get("supplier") for r in (d.get("supplier_items") or [])]
        print(f"  {item_code}: lead={d.get('lead_time_days')} moq={d.get('min_order_qty')} suppliers={sups}")
        code, resp = api(
            "Item Price?filters=" + urllib.parse.quote(
                json.dumps([["item_code", "=", item_code], ["supplier", "!=", ""]]), safe=""
            ) + '&fields=["name","supplier","price_list_rate"]&limit_page_length=10'
        )
        if code == 200:
            for r in resp.get("data", []):
                print(f"      供应商价: {r.get('supplier')} = {r.get('price_list_rate')} ({r.get('name')})")

    print("\nDone.")


if __name__ == "__main__":
    main()
