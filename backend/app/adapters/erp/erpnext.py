from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from app.integrations.errors import IntegrationNotConfigured, IntegrationPermissionDenied
from app.integrations.http import JsonHttpClient


ERP_DRAFT_DOCTYPES = frozenset({"Quotation", "Sales Order", "Purchase Order"})


class ERPNextClient:
    """Frappe REST transport. Reads are generic; writes are draft-only and gated."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        draft_writes_enabled: bool = False,
        approval_verifier: Callable[[str, str], Awaitable[bool]] | None = None,
        timeout_seconds: float = 15.0,
        client=None,
    ) -> None:
        missing = [name for name, value in (("ERPNEXT_BASE_URL", base_url), ("ERPNEXT_API_KEY", api_key), ("ERPNEXT_API_SECRET", api_secret)) if not value.strip()]
        if missing:
            raise IntegrationNotConfigured("ERPNext", missing)
        self._http = JsonHttpClient(
            base_url,
            headers={"Authorization": f"token {api_key}:{api_secret}", "Accept": "application/json"},
            timeout_seconds=timeout_seconds,
            client=client,
        )
        self.draft_writes_enabled = draft_writes_enabled
        self._approval_verifier = approval_verifier

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _doctype_path(doctype: str) -> str:
        if not doctype.strip():
            raise ValueError("DocType 不能为空")
        return f"api/resource/{quote(doctype, safe='')}"

    async def get_logged_user(self) -> dict[str, Any]:
        result = await self._http.request_json("GET", "api/method/frappe.auth.get_logged_user")
        if not isinstance(result, dict) or "message" not in result:
            raise ValueError("ERPNext 登录用户接口返回格式不符合 Frappe REST 契约")
        return {"user": result["message"]}

    async def list_documents(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[list[Any]] | None = None,
        limit: int = 50,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        if not fields or any(not field.strip() for field in fields):
            raise ValueError("ERPNext 查询必须明确列出字段，不能默认读取全部字段")
        if limit < 1 or limit > 100 or start < 0:
            raise ValueError("limit 必须为 1-100，start 不能小于 0")
        result = await self._http.request_json(
            "GET",
            self._doctype_path(doctype),
            params={
                "fields": json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                "filters": json.dumps(filters or [], ensure_ascii=False, separators=(",", ":")),
                "limit_page_length": limit,
                "limit_start": start,
            },
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("ERPNext 列表接口返回格式不符合 Frappe REST 契约")
        return data

    async def get_document(self, doctype: str, name: str) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("ERPNext 单据 name 不能为空")
        result = await self._http.request_json(
            "GET", f"{self._doctype_path(doctype)}/{quote(name, safe='')}"
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            raise ValueError("ERPNext 单据接口返回格式不符合 Frappe REST 契约")
        return data

    async def create_draft(
        self,
        doctype: str,
        fields: dict[str, Any],
        *,
        approval_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        """Create an unsubmitted commercial draft after a recorded human approval.

        This primitive is disabled by default. It never invokes Frappe's submit
        endpoint and rejects attempts to supply a submitted document status.
        """
        if not self.draft_writes_enabled:
            raise IntegrationPermissionDenied("ERPNext 草稿写入当前关闭")
        if self._approval_verifier is None:
            raise IntegrationPermissionDenied("尚未接入可信的人工审批校验器，ERPNext 写入保持关闭")
        if doctype not in ERP_DRAFT_DOCTYPES:
            raise IntegrationPermissionDenied(f"不允许创建此 DocType 的草稿：{doctype}")
        if not approval_id.strip() or not approved_by.strip():
            raise IntegrationPermissionDenied("草稿写入必须绑定人工审批编号和审批人")
        if not await self._approval_verifier(approval_id, approved_by):
            raise IntegrationPermissionDenied("审批编号、审批人或审批状态校验失败")
        supplied_status = fields.get("docstatus", 0)
        if supplied_status != 0:
            raise IntegrationPermissionDenied("只能创建 docstatus=0 的草稿")
        payload = {**fields, "docstatus": 0}
        result = await self._http.request_json(
            "POST", self._doctype_path(doctype), json_body=payload
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ValueError("ERPNext 未返回新草稿编号，停止发布结果")
        read_back = await self.get_document(doctype, data["name"])
        if read_back.get("docstatus") != 0:
            raise ValueError("ERPNext 回读未确认 docstatus=0，停止发布结果")
        return {
            "data": read_back,
            "approval_id": approval_id,
            "approved_by": approved_by,
            "authority": "ERPNext",
        }
