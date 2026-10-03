"""在每台本地真实 OpenMES 上导入可复现的 TEST_ 演示工单和质量问题。

这个脚本面向 ``APP_ADAPTER_MODE=real`` 的本地 ERPNext/OpenMES 环境，
不是 Mock 数据入口，也不复制任何现有数据库或凭据。它只使用 OpenMES
公开的用户 API 与 ERP 集成导入 API：

1. 用本机 ``services/OpenMes/.env`` 的管理员账号登录；
2. 用根目录 ``.env`` 的 OpenMES ERP 集成 Key 导入成品；
3. 动态查询产品类型、产线和问题类型，拒绝使用固定数字 ID；
4. 按稳定的 ``TEST_`` 工单号和问题描述标记查重后创建记录；
5. 回读并打印创建/复用的业务编号。

当前 OpenMES 没有经本脚本确认的工艺模板、检验记录和历史批次导入契约，
因此本脚本不会猜测这些字段，也不会调用固定容器名的 SQL。ETA/检验故事线
仍需另行按目标 OpenMES 版本核对后处理。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw[:500]}
        return exc.code, payload
    except (OSError, urllib.error.URLError) as exc:
        return 0, {"error": str(exc)[:500]}


def data_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    return data if isinstance(data, list) else []


def require(value: str, name: str) -> str:
    if not value.strip():
        raise RuntimeError(f"缺少本机配置 {name}；请填写后再运行。")
    return value.strip()


def fail_response(label: str, code: int, payload: dict[str, Any]) -> None:
    detail = payload.get("message") or payload.get("error") or payload.get("errors") or payload
    raise RuntimeError(f"{label}失败（HTTP {code}）：{str(detail)[:500]}")


def ensure_products(base_url: str, erp_key: str) -> None:
    products = {
        "external_system": "erpnext",
        "only_categories": ["FINISHED"],
        "products": [
            {"code": "BD-2401", "name": "制动盘-前轮 Brake Disc Front", "category": "FINISHED", "unit_of_measure": "pcs"},
            {"code": "SK-3401", "name": "转向节 Steering Knuckle", "category": "FINISHED", "unit_of_measure": "pcs"},
            {"code": "TS-4501", "name": "传动轴总成 Transmission Shaft Assembly", "category": "FINISHED", "unit_of_measure": "pcs"},
        ],
    }
    code, payload = json_request(
        base_url,
        "/api/v1/erp/products/import",
        method="POST",
        body=products,
        headers={"X-Api-Key": erp_key},
    )
    if code not in (200, 201, 207):
        fail_response("OpenMES 产品导入", code, payload)
    result = payload.get("data", payload)
    print(f"产品导入完成：imported={result.get('imported', 0)} updated={result.get('updated', 0)} skipped={result.get('skipped', 0)}")


def fetch_catalog(base_url: str, bearer: dict[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    code, products = json_request(base_url, "/api/v1/product-types?per_page=100", headers=bearer)
    if code != 200:
        fail_response("读取 OpenMES 产品类型", code, products)
    product_map = {str(row.get("code")): row for row in data_rows(products) if row.get("code")}

    code, lines = json_request(base_url, "/api/v1/lines?per_page=100", headers=bearer)
    if code != 200:
        fail_response("读取 OpenMES 产线", code, lines)
    line_map = {str(row.get("code")): row for row in data_rows(lines) if row.get("code")}

    code, issue_types = json_request(base_url, "/api/v1/issue-types?per_page=100", headers=bearer)
    if code != 200:
        fail_response("读取 OpenMES 问题类型", code, issue_types)
    issue_map = {str(row.get("name")): row for row in data_rows(issue_types) if row.get("name")}
    return product_map, line_map, issue_map


def ensure_work_order(base_url: str, bearer: dict[str, str], row: dict[str, Any], product: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode({"search": row["order_no"], "per_page": 100})
    code, payload = json_request(base_url, f"/api/v1/work-orders?{query}", headers=bearer)
    if code != 200:
        fail_response(f"查找工单 {row['order_no']}", code, payload)
    for existing in data_rows(payload):
        if existing.get("order_no") == row["order_no"]:
            print(f"工单已存在，复用：{row['order_no']}（id={existing.get('id')}）")
            return existing

    body = {
        "order_no": row["order_no"],
        "product_name": row["product_name"],
        "quantity": row["quantity"],
        "line_id": line.get("id"),
        "product_type_id": product.get("id"),
        "priority": row["priority"],
        "due_date": row["due_date"],
    }
    code, payload = json_request(base_url, "/api/v1/work-orders", method="POST", body=body, headers=bearer)
    if code not in (200, 201):
        fail_response(f"创建工单 {row['order_no']}", code, payload)
    created = payload.get("data", payload)
    print(f"工单已创建：{row['order_no']}（id={created.get('id')}）")
    return created


def ensure_issue(base_url: str, bearer: dict[str, str], work_order: dict[str, Any], issue_type: dict[str, Any], marker: str, description: str) -> None:
    query = urllib.parse.urlencode({"work_order_id": work_order.get("id"), "per_page": 100})
    code, payload = json_request(base_url, f"/api/v1/issues?{query}", headers=bearer)
    if code != 200:
        fail_response(f"查找工单 {work_order.get('order_no')} 的质量问题", code, payload)
    if any(marker in str(row.get("description", "")) for row in data_rows(payload)):
        print(f"质量问题已存在，跳过：{marker}")
        return
    body = {"work_order_id": work_order.get("id"), "issue_type_id": issue_type.get("id"), "description": f"{marker} {description}"}
    code, payload = json_request(base_url, "/api/v1/issues", method="POST", body=body, headers=bearer)
    if code not in (200, 201):
        fail_response(f"创建质量问题 {marker}", code, payload)
    print(f"质量问题已创建：{marker}（id={payload.get('data', payload).get('id')}）")


def main() -> int:
    root_env = load_env(ROOT / ".env")
    openmes_env = load_env(ROOT / "services" / "OpenMes" / ".env")
    base_url = root_env.get("OPENMES_BASE_URL") or openmes_env.get("APP_URL") or "http://127.0.0.1"
    username = require(openmes_env.get("ADMIN_USERNAME", ""), "services/OpenMes/.env: ADMIN_USERNAME")
    password = require(openmes_env.get("ADMIN_PASSWORD", ""), "services/OpenMes/.env: ADMIN_PASSWORD")
    erp_key = require(root_env.get("OPENMES_ERP_API_KEY", ""), ".env: OPENMES_ERP_API_KEY")
    issue_type_name = root_env.get("TEST_ISSUE_TYPE_NAME", "Measurement / Dimension Error").strip()

    code, payload = json_request(base_url, "/api/auth/login", method="POST", body={"username": username, "password": password})
    if code != 200:
        fail_response("OpenMES 登录", code, payload)
    token = str(payload.get("data", payload).get("token", ""))
    if not token:
        raise RuntimeError("OpenMES 登录响应没有 token；请提供该版本接口响应供核对。")
    bearer = {"Authorization": f"Bearer {token}"}

    ensure_products(base_url, erp_key)
    product_map, line_map, issue_map = fetch_catalog(base_url, bearer)
    required_products = {"BD-2401", "SK-3401", "TS-4501"}
    missing_products = sorted(required_products - product_map.keys())
    if missing_products:
        raise RuntimeError(f"OpenMES 产品类型仍缺少：{', '.join(missing_products)}；未猜测创建方式。")
    line = line_map.get("DEMO_LINE_01")
    if not line:
        raise RuntimeError(f"OpenMES 缺少产线 DEMO_LINE_01；当前可见产线：{', '.join(sorted(line_map)) or '无'}。请先提供该版本的建线方式。")
    issue_type = issue_map.get(issue_type_name)
    if not issue_type:
        raise RuntimeError(f"OpenMES 缺少问题类型 {issue_type_name!r}；当前可见类型：{', '.join(sorted(issue_map)) or '无'}。可在 .env 设置 TEST_ISSUE_TYPE_NAME。")

    orders = [
        {"order_no": "TEST_WO_PAGE_00023", "product_code": "BD-2401", "product_name": "制动盘-前轮 Brake Disc Front", "quantity": 2000, "priority": 5, "due_date": "2026-10-31"},
        {"order_no": "TEST_WO_PAGE_00024", "product_code": "SK-3401", "product_name": "转向节 Steering Knuckle", "quantity": 500, "priority": 3, "due_date": "2026-10-25"},
        {"order_no": "TEST_WO_PAGE_00025", "product_code": "TS-4501", "product_name": "传动轴总成 Transmission Shaft Assembly", "quantity": 300, "priority": 4, "due_date": "2026-11-05"},
    ]
    created: dict[str, dict[str, Any]] = {}
    for row in orders:
        created[row["order_no"]] = ensure_work_order(base_url, bearer, row, product_map[row["product_code"]], line)

    ensure_issue(
        base_url,
        bearer,
        created["TEST_WO_PAGE_00023"],
        issue_type,
        "TEST_SEED_QUALITY_00023",
        "制动盘外径尺寸超差，需人工走质量处置审批。",
    )
    print("完成：真实 OpenMES 已准备可复现 TEST_ 工单与质量问题；未写入 ERPNext，未处理固定批次 ETA/检验 SQL。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise SystemExit(1)

