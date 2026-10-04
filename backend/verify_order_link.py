"""只读验证：核验 MES 工单 customer_order_no 与 ERP 销售订单的关联。

用途：用户在 OpenMES 界面手工填写工单的 customer_order_no 后，运行本脚本
核验关联是否正确建立。只读取，不写入，不打印任何凭据。

用法：
    cd backend
    ../.conda-env/python.exe verify_order_link.py            # 全量核验
    ../.conda-env/python.exe verify_order_link.py WO-2026-001 # 核验单个工单
"""
import json
import os
import sys
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


def http_json(url: str, headers: dict):
    req = urllib.request.Request(url)
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "replace")[:300]}


def fetch_mes_work_orders() -> list[dict]:
    url = f"{MES_BASE}/api/v1/work-orders?per_page=100"
    code, resp = http_json(url, {"Authorization": f"Bearer {MES_TOKEN}"})
    if code != 200:
        print(f"[失败] OpenMES 工单接口 HTTP {code}")
        sys.exit(2)
    payload = resp.get("data", resp)
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("data") or []
    return payload


def fetch_erp_sales_orders() -> dict[str, dict]:
    """返回 {订单号: 摘要}。"""
    q = urllib.parse.quote(
        'Sales Order?fields=["name","customer","delivery_date","total_qty","status"]&limit_page_length=100',
        safe="/?=&[]\",",
    )
    code, resp = http_json(f"{ERP_BASE}/api/resource/{q}", {"Authorization": ERP_AUTH})
    if code != 200:
        print(f"[失败] ERPNext 销售订单接口 HTTP {code}")
        sys.exit(2)
    out = {}
    for row in resp.get("data", []):
        out[row["name"]] = {
            "customer": row.get("customer", ""),
            "delivery_date": row.get("delivery_date", ""),
            "total_qty": row.get("total_qty", ""),
            "status": row.get("status", ""),
        }
    return out


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    is_demo = lambda no: str(no).startswith("DEMO_")

    work_orders = fetch_mes_work_orders()
    erp_orders = fetch_erp_sales_orders()

    print("=" * 72)
    print("只读验证：MES 工单 customer_order_no ↔ ERP 销售订单关联")
    print("=" * 72)
    print(f"OpenMES 工单: {len(work_orders)} 个 | ERPNext 销售订单: {len(erp_orders)} 张\n")

    linked, unlinked = [], []
    for wo in work_orders:
        order_no = wo.get("order_no", "")
        if target and order_no != target:
            continue
        if is_demo(order_no):
            tag = "（模拟记录，不参与关联验证）"
            print(f"[跳过] {order_no} {tag}")
            continue

        ref = (wo.get("customer_order_no") or "").strip()
        pt = wo.get("product_type") or {}
        wo_qty = str(wo.get("planned_qty", "")).rstrip("0").rstrip(".") or "0"
        due = (wo.get("due_date") or "")[:10]
        if not ref:
            unlinked.append(order_no)
            print(f"[未关联] {order_no}: customer_order_no 为空，产品 {pt.get('code')} × {wo_qty}，交期 {due}")
            continue
        if ref not in erp_orders:
            print(f"[异常] {order_no}: customer_order_no='{ref}' 在 ERPNext 中不存在对应销售订单！")
            continue
        so = erp_orders[ref]
        so_qty = str(so["total_qty"]).rstrip("0").rstrip(".") or "0"
        qty_match = wo_qty == so_qty
        due_match = due == (so["delivery_date"] or "")[:10]
        linked.append((order_no, ref))
        print(f"[已关联] {order_no} → {ref}")
        print(f"         ERP: 客户 {so['customer']}，数量 {so_qty}，交期 {so['delivery_date']}，状态 {so['status']}")
        print(f"         MES: 产品 {pt.get('code')} × {wo_qty}，交期 {due}")
        print(f"         数量{'一致' if qty_match else '不一致(需人工核对)'} | 交期{'一致' if due_match else '不一致(需人工核对)'}")

    print()
    print("-" * 72)
    if linked:
        print(f"已建立关联: {len(linked)} 个工单")
        for wo_no, so_no in linked:
            print(f"  {wo_no} → {so_no}")
    else:
        print("已建立关联: 0 个（全部 NOT_LINKED）")
    if unlinked:
        print(f"未建立关联: {len(unlinked)} 个工单: {', '.join(unlinked)}")
    print()
    print("提示: 后端接口验证可使用")
    print("  GET /api/real-orders/erp/sales-orders/{订单号}/mes-link")


if __name__ == "__main__":
    main()
