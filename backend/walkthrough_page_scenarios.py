"""页面级双场景走查辅助脚本（测试数据，全部 TEST_ 标记）。

模式:
  create <erp_draft_id>  : 通过官方 POST /api/v1/work-orders 创建与 ERP 草稿
                           精确关联(customer_order_no)的测试工单并 accept，回读确认。
  produce <work_order_id> <qty> : 创建批次并按官方批次工序接口完成指定数量，
                           回读 produced_qty。

凭据从项目 .env 读取，不打印任何凭据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import urllib.request
import urllib.error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

import os  # noqa: E402

BASE = os.getenv("OPENMES_BASE_URL", "http://localhost").rstrip("/")
TOKEN = os.getenv("OPENMES_TOKEN", "")

if not TOKEN:
    raise SystemExit("OPENMES_TOKEN 未配置，拒绝写入")


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/json")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "replace")[:400]}


def create(erp_draft_id: str) -> None:
    order_no = f"TEST_WO_PAGE_{erp_draft_id.split('-')[-1]}"
    payload = {
        "order_no": order_no,
        "customer_order_no": erp_draft_id,
        "line_id": 1,
        "product_type_id": 2,  # BD-2401（与已发布 SOP/Control Plan 同产品）
        "planned_qty": 2000,
        "priority": 3,
        "due_date": "2026-10-31",
        "description": f"TEST 页面走查工单（关联 {erp_draft_id}），非生产数据",
    }
    code, resp = api("POST", "/api/v1/work-orders", payload)
    print(f"[create] POST /api/v1/work-orders -> {code}")
    if code != 201:
        print(json.dumps(resp, ensure_ascii=False)[:500])
        raise SystemExit(1)
    wo = resp["data"]
    wo_id = wo["id"]
    print(f"[create] work_order id={wo_id} order_no={wo['order_no']} "
          f"customer_order_no={wo.get('customer_order_no')} status={wo.get('status')}")

    code, resp = api("POST", f"/api/v1/work-orders/{wo_id}/accept", {})
    print(f"[accept] POST /api/v1/work-orders/{wo_id}/accept -> {code}")

    code, resp = api("GET", f"/api/v1/work-orders/{wo_id}")
    wo = resp["data"]
    print(f"[read-back] status={wo.get('status')} customer_order_no={wo.get('customer_order_no')} "
          f"planned_qty={wo.get('planned_qty')}")
    docs = wo.get("process_snapshot", {}).get("engineering_documents", [])
    print(f"[read-back] frozen engineering_documents = {len(docs)} 条: "
          f"{[d.get('document_id') for d in docs]}")


def produce(work_order_id: int, qty: float) -> None:
    code, resp = api("GET", f"/api/v1/work-orders/{work_order_id}")
    wo = resp["data"]
    print(f"[produce] work_order {wo['order_no']} planned={wo.get('planned_qty')} "
          f"produced={wo.get('produced_qty')} status={wo.get('status')}")
    if str(wo.get("status")).upper() != "ACCEPTED":
        print("[produce] 工单不是 ACCEPTED 状态，拒绝补产")
        raise SystemExit(1)

    lot = f"TEST_LOT_PAGE_{work_order_id}"
    code, resp = api("POST", f"/api/v1/work-orders/{work_order_id}/batches",
                     {"target_qty": qty, "lot_number": lot})
    print(f"[batch] POST /work-orders/{work_order_id}/batches -> {code}")
    if code not in (200, 201):
        print(json.dumps(resp, ensure_ascii=False)[:500])
        raise SystemExit(1)
    batch_id = resp["data"]["id"]

    code, resp = api("GET", f"/api/v1/batches/{batch_id}")
    steps = resp["data"].get("steps", [])
    print(f"[batch] batch id={batch_id} steps={[(s.get('id'), s.get('step_name')) for s in steps]}")
    if not steps:
        raise SystemExit("批次无工序步骤（无工艺模板），不能伪造完工")

    for s in steps:
        sid = s["id"]
        code, resp = api("POST", f"/api/v1/batch-steps/{sid}/start", {})
        print(f"[step] start {sid} -> {code}")
        if code not in (200, 201):
            print(json.dumps(resp, ensure_ascii=False)[:300])
            raise SystemExit(1)
        code, resp = api("POST", f"/api/v1/batch-steps/{sid}/complete",
                         {"produced_qty": qty, "actual_elapsed_minutes": 30})
        print(f"[step] complete {sid} -> {code} produced_qty={qty}")
        if code not in (200, 201):
            print(json.dumps(resp, ensure_ascii=False)[:300])
            raise SystemExit(1)

    code, resp = api("GET", f"/api/v1/work-orders/{work_order_id}")
    wo = resp["data"]
    print(f"[read-back] produced_qty={wo.get('produced_qty')} status={wo.get('status')}")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "create":
        create(sys.argv[2])
    elif mode == "produce":
        produce(int(sys.argv[2]), float(sys.argv[3]))
    else:
        raise SystemExit("usage: create <erp_draft_id> | produce <wo_id> <qty>")
