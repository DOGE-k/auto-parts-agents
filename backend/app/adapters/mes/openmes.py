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

    async def list_product_types(self, query: str = "") -> list[dict[str, Any]]:
        self._require_user_token()
        result = await self._http.request_json(
            "GET",
            "api/v1/product-types",
            params={"q": query} if query else None,
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise ValueError("OpenMES 产品类型接口返回格式不符合其 API 文档")
        return data

    async def create_work_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_user_token()
        required = {"order_no", "planned_qty"}
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"创建 OpenMES 工单缺少字段：{', '.join(missing)}")
        result = await self._http.request_json(
            "POST", "api/v1/work-orders", json_body=payload,
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ValueError("OpenMES 工单创建接口返回格式不符合其 API 文档")
        return result

    async def accept_work_order(self, work_order_id: str | int) -> dict[str, Any]:
        self._require_user_token()
        value = str(work_order_id).strip()
        if not value:
            raise ValueError("接收 OpenMES 工单必须提供 work_order_id")
        result = await self._http.request_json(
            "POST", f"api/v1/work-orders/{quote(value, safe='')}/accept",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict):
            raise ValueError("OpenMES 工单接收接口返回格式不符合其 API 文档")
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

    async def list_work_order_engineering_documents(self, work_order_id: str | int) -> list[dict[str, Any]]:
        """工单关联工程文档（SOP/Control Plan 类载体，Bearer）。"""
        self._require_user_token()
        value = str(work_order_id).strip()
        if not value:
            raise ValueError("OpenMES work_order_id 不能为空")
        result = await self._http.request_json(
            "GET",
            f"api/v1/work-orders/{quote(value, safe='')}/engineering-documents",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise ValueError("OpenMES 工程文档接口返回格式不符合其 API 文档")
        return data

    async def get_engineering_document(self, document_id: str | int) -> dict[str, Any]:
        """读取工程文档详情，补齐工单冻结摘要可能省略的 document_type。"""
        self._require_user_token()
        value = str(document_id).strip()
        if not value:
            raise ValueError("OpenMES document_id 不能为空")
        result = await self._http.request_json(
            "GET",
            f"api/v1/engineering-documents/{quote(value, safe='')}",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            raise ValueError("OpenMES 工程文档详情接口返回格式不符合其 API 文档")
        return data

    async def list_work_order_batches(self, work_order_id: str | int) -> list[dict[str, Any]]:
        """工单生产批次列表（批次/SN 追溯维度，Bearer）。"""
        self._require_user_token()
        value = str(work_order_id).strip()
        if not value:
            raise ValueError("OpenMES work_order_id 不能为空")
        result = await self._http.request_json(
            "GET",
            f"api/v1/work-orders/{quote(value, safe='')}/batches",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        if not isinstance(data, list):
            raise ValueError("OpenMES 工单批次接口返回格式不符合其 API 文档")
        return data

    async def upload_engineering_document(
        self,
        *,
        filename: str,
        content: bytes,
        entity_type: str,
        entity_id: int,
        revision: str,
        document_type: str,
    ) -> dict[str, Any]:
        """Upload an engineering document through the documented multipart API."""
        self._require_user_token()
        if not filename.lower().endswith(".html"):
            raise ValueError("当前测试上传只允许 .html 工程文档")
        if not content:
            raise ValueError("工程文档内容不能为空")
        result = await self._http.request_multipart(
            "POST",
            "api/v1/engineering-documents",
            data={
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "revision": revision,
                "document_type": document_type,
            },
            files={"file": (filename, content, "text/html")},
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ValueError("OpenMES 工程文档上传接口返回格式不符合其 API 文档")
        return result

    async def release_engineering_document(self, document_id: str | int) -> dict[str, Any]:
        """Release an uploaded engineering document through OpenMES lifecycle API."""
        self._require_user_token()
        value = str(document_id).strip()
        if not value:
            raise ValueError("发布工程文档必须提供 document_id")
        result = await self._http.request_json(
            "POST",
            f"api/v1/engineering-documents/{quote(value, safe='')}/release",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ValueError("OpenMES 工程文档发布接口返回格式不符合其 API 文档")
        return result

    async def list_inspections(self, filters: dict[str, str | int] | None = None) -> list[dict[str, Any]]:
        """检验记录（来料/过程质量，Bearer）。当前真实数据可能为空列表。"""
        self._require_user_token()
        params = {k: str(v) for k, v in (filters or {}).items()}
        result = await self._http.request_json(
            "GET",
            "api/v1/inspections",
            params=params,
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        if not isinstance(data, list):
            raise ValueError("OpenMES 检验记录接口返回格式不符合其 API 文档")
        return data

    async def resolve_issue(self, issue_id: str | int, resolution_notes: str) -> dict[str, Any]:
        """Resolve a quality issue through the authenticated OpenMES API."""
        self._require_user_token()
        value = str(issue_id).strip()
        notes = resolution_notes.strip()
        if not value or not notes:
            raise ValueError("处理 OpenMES 质量问题必须提供 issue_id 和 resolution_notes")
        result = await self._http.request_json(
            "POST",
            f"api/v1/issues/{quote(value, safe='')}/resolve",
            json_body={"resolution_notes": notes},
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict):
            raise ValueError("OpenMES 质量问题处理接口返回格式不符合其 API 文档")
        return result

    async def close_issue(self, issue_id: str | int) -> dict[str, Any]:
        """Close a resolved quality issue through the authenticated OpenMES API."""
        self._require_user_token()
        value = str(issue_id).strip()
        if not value:
            raise ValueError("关闭 OpenMES 质量问题必须提供 issue_id")
        result = await self._http.request_json(
            "POST",
            f"api/v1/issues/{quote(value, safe='')}/close",
            headers={"Authorization": f"Bearer {self._user_token}"},
        )
        if not isinstance(result, dict):
            raise ValueError("OpenMES 质量问题关闭接口返回格式不符合其 API 文档")
        return result

    async def import_erp_work_orders(
        self, orders: list[dict[str, Any]], strategy: str = "update_or_create"
    ) -> dict[str, Any]:
        """ERP→OpenMES 官方工单导入/更新（X-Api-Key，scope erp:orders:import）。

        payload 契约见 ImportWorkOrdersRequest：order_no/line_code/
        product_type_code/planned_qty 必填，customer_order_no 等可选。
        strategy: update_or_create | skip_existing | error_on_duplicate。
        注意 update_or_create 会按提交值重写 line/product/planned_qty/
        priority/due_date，调用方必须先读当前值原样回传。
        """
        self._require_erp_api_key()
        if strategy not in ("update_or_create", "skip_existing", "error_on_duplicate"):
            raise ValueError("OpenMES 工单导入 strategy 必须是 update_or_create/skip_existing/error_on_duplicate")
        if not orders or not isinstance(orders, list):
            raise ValueError("OpenMES 工单导入必须提供非空 orders 列表")
        result = await self._http.request_json(
            "POST",
            "api/v1/erp/work-orders/import",
            headers={"X-Api-Key": self._erp_api_key},
            json_body={"strategy": strategy, "orders": orders},
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ValueError("OpenMES 工单导入接口返回格式不符合其 API 文档")
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
