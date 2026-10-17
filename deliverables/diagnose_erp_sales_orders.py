"""Read-only ERPNext order-list diagnostic for the deployed Wutong runtime.

Run from the server as:
    .venv/bin/python /home/ubuntu/diagnose_erp_sales_orders.py /home/ubuntu/apps/auto-parts-agents

Prints configuration presence and query outcomes only. Never prints credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from dotenv import load_dotenv


def report(label: str, **fields: object) -> None:
    print(json.dumps({"check": label, **fields}, ensure_ascii=False))


async def attempt(label: str, operation) -> object | None:
    try:
        value = await operation()
    except Exception as exc:
        report(
            label,
            ok=False,
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
            http_status=getattr(exc, "status_code", None),
        )
        return None
    report(label, ok=True, count=len(value) if isinstance(value, list) else None)
    return value


async def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not (root / "backend" / "app").is_dir():
        raise SystemExit("参数须为 auto-parts-agents 项目目录")
    sys.path.insert(0, str(root / "backend"))
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "backend" / ".env", override=False)

    from app.adapters.erp.erpnext import ERPNextClient
    from app.adapters.erp.erpnext_adapter import ERPNextAdapter
    from app.integrations.settings import IntegrationSettings

    settings = IntegrationSettings.from_environment()
    parsed = urlsplit(settings.erpnext_base_url)
    report(
        "config",
        adapter_mode=os.getenv("APP_ADAPTER_MODE", ""),
        erp_scheme=parsed.scheme,
        erp_host=parsed.hostname,
        erp_port=parsed.port,
        erp_path=parsed.path,
        key_configured=bool(settings.erpnext_api_key),
        secret_configured=bool(settings.erpnext_api_secret),
    )
    if os.getenv("APP_ADAPTER_MODE", "").strip().lower() != "real":
        raise SystemExit("当前不是 real 模式；本脚本不使用 Mock 数据")

    client = ERPNextClient(
        settings.erpnext_base_url,
        settings.erpnext_api_key,
        settings.erpnext_api_secret,
        timeout_seconds=8,
    )
    try:
        user = await attempt("erp_login", client.get_logged_user)
        if user is None:
            return
        report("erp_login_identity", is_guest=user.get("user") == "Guest")

        await attempt(
            "customer_name_first_page",
            lambda: client.list_documents("Customer", fields=["name"], limit=5),
        )
        await attempt(
            "sales_order_name_first_page",
            lambda: client.list_documents("Sales Order", fields=["name"], limit=5),
        )
        await attempt(
            "sales_order_full_fields_first_page",
            lambda: ERPNextAdapter(client).list_sales_orders(limit=5),
        )
        for order_no in ("SO-2026-001", "SO-2026-002", "SO-2026-003"):
            result = await attempt(
                f"exact_sales_order_{order_no}",
                lambda order_no=order_no: client.get_document("Sales Order", order_no),
            )
            if result is not None:
                report(f"exact_sales_order_{order_no}_exists", exists=True)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
