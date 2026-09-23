from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.integrations.errors import IntegrationNotConfigured, IntegrationPermissionDenied
from app.integrations.http import JsonHttpClient


WORK_ORDER_FILTERS = frozenset(
    {"status", "line_id", "due_before", "week_number", "month_number", "production_year", "search", "per_page", "page"}
)


class OpenMESClient:
    """Read-only client for endpoints documented by the OpenMES repository."""

    def __init__(
        self,
        base_url: str,
        *,
        user_token: str | None = None,
        erp_api_key: str | None = None,
        timeout_seconds: float = 15.0,
        client=None,
    ) -> None:
        if not base_url.strip():
            raise IntegrationNotConfigured("OpenMES", ["OPENMES_BASE_URL"])
        self._user_token = user_token.strip() if user_token else ""
        self._erp_api_key = erp_api_key.strip() if erp_api_key else ""
        self._http = JsonHttpClient(
            base_url,
            timeout_seconds=timeout_seconds,
            client=client,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self) -> dict[str, Any]:
        result = await self._http.request_json("GET", "api/health")
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise ValueError("OpenMES 健康检查返回格式不符合其 API 文档")
        return result

    async def current_user(self) -> dict[str, Any]:
        self._require_user_token()
        result = await self._http.request_json(
            "GET", "api/auth/me", headers={"Authorization": f"Bearer {self._user_token}"}
        )
        if not isinstance(result, dict):
            raise ValueError("OpenMES 用户接口返回格式不符合其 API 文档")
        return result

    async def list_work_orders(self, filters: dict[str, str | int] | None = None) -> dict[str, Any]:
        self._require_user_token()
        filters = filters or {}
        unknown = set(filters) - WORK_ORDER_FILTERS
        if unknown:
            raise ValueError(f"OpenMES 工单筛选项未在 API 文档确认：{', '.join(sorted(unknown))}")
        if "per_page" in filters and not 1 <= int(filters["per_page"]) <= 100:
            raise ValueError("OpenMES per_page 必须在 1-100 之间")
        result = await self._http.request_json(
            "GET",
            "api/v1/work-orders",
            params=filters,
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("OpenMES 工单列表返回格式不符合其 API 文档")
        return result

    async def get_work_order(self, work_order_id: str | int) -> dict[str, Any]:
        self._require_user_token()
        value = str(work_order_id).strip()
        if not value:
            raise ValueError("OpenMES work_order_id 不能为空")
        result = await self._http.request_json(
            "GET",
            f"api/v1/work-orders/{quote(value, safe='')}",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ValueError("OpenMES 工单详情返回格式不符合其 API 文档")
        return result

    async def list_event_logs(self, entity_type: str, entity_id: str | int) -> dict[str, Any]:
        self._require_user_token()
        if not entity_type.strip() or not str(entity_id).strip():
            raise ValueError("查询 OpenMES 事件日志必须提供 entity_type 和 entity_id")
        result = await self._http.request_json(
            "GET",
            "api/v1/event-logs/entity",
            params={"entity_type": entity_type, "entity_id": str(entity_id)},
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict):
            raise ValueError("OpenMES 事件日志返回格式不符合其 API 文档")
        return result

    async def list_erp_production_completions(self, *, since: str | None = None, cursor: str | None = None) -> dict[str, Any]:
        self._require_erp_api_key()
        params: dict[str, str] = {}
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        result = await self._http.request_json(
            "GET",
            "api/v1/erp/production/completions",
            params=params,
            headers={"X-Api-Key": self._erp_api_key},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("OpenMES 生产完工接口返回格式不符合其 API 文档")
        return result

    async def list_erp_quality_issues(self, *, since: str | None = None, cursor: str | None = None) -> dict[str, Any]:
        self._require_erp_api_key()
        params: dict[str, str] = {}
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        result = await self._http.request_json(
            "GET",
            "api/v1/erp/quality/issues",
            params=params,
            headers={"X-Api-Key": self._erp_api_key},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("OpenMES 质量问题接口返回格式不符合其 API 文档")
        return result

    async def write_request(self, *_args, **_kwargs) -> None:
        raise IntegrationPermissionDenied(
            "OpenMES 写操作尚未开放；需先确认人工审批身份、目标实例权限和具体业务映射"
        )

    def _require_user_token(self) -> None:
        if not self._user_token:
            raise IntegrationNotConfigured("OpenMES 用户 API", ["OPENMES_TOKEN"])

    def _require_erp_api_key(self) -> None:
        if not self._erp_api_key:
            raise IntegrationNotConfigured("OpenMES ERP 集成 API", ["OPENMES_ERP_API_KEY"])
