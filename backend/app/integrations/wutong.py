"""Optional ACPs/Wutong discovery client.

The vendored ACPs discovery contract is intentionally used as the wire
contract: ``POST /discover`` accepts a DiscoveryRequest and returns a common
response with ``result.acsMap``/``agents``/``routes`` or ``error``. This
module is read-only. It never registers or mutates a remote registry.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.integrations.http import JsonHttpClient


class WutongDiscoveryClient:
    """Small bounded client for an ACPs-compatible Wutong discovery service."""

    def __init__(self, discovery_url: str, *, tenant: str = "", client=None) -> None:
        value = discovery_url.strip().rstrip("/")
        if not value:
            raise IntegrationNotConfigured("Wutong discovery", ["WUTONG_DISCOVERY_URL"])
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("WUTONG_DISCOVERY_URL 必须是完整的 http:// 或 https:// URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("WUTONG_DISCOVERY_URL 不得包含账号、密码、查询参数或片段")
        root = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        self._discover_path = f"{path}/discover" if path and not path.endswith("/discover") else (path or "/discover")
        self._health_path = f"{path}/health" if path and not path.endswith("/health") else (path or "/health")
        self._tenant = tenant.strip()
        self._client = JsonHttpClient(root, client=client)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"X-Wutong-Tenant": self._tenant} if self._tenant else {}

    async def health(self) -> dict[str, Any]:
        payload = await self._client.request_json("GET", self._health_path, headers=self._headers())
        if not isinstance(payload, dict):
            raise IntegrationError("Wutong discovery 健康响应格式无效", code="discovery_invalid_response")
        return {"status": "ok", "payload": payload, "authority": "Wutong", "data_source": "wutong_discovery"}

    async def discover(
        self,
        query: str,
        *,
        limit: int = 5,
        discovery_type: str = "explicit",
        filter_obj: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = query.strip()
        if not text and discovery_type != "filtered":
            raise ValueError("Wutong discovery 查询不能为空（filtered 查询除外）")
        if not 1 <= limit <= 50:
            raise ValueError("Wutong discovery limit 必须在 1-50 之间")
        body: dict[str, Any] = {
            "type": discovery_type,
            "query": text or None,
            "limit": limit,
        }
        if filter_obj is not None:
            body["filter"] = filter_obj
        payload = await self._client.request_json(
            "POST", self._discover_path, json_body=body, headers=self._headers()
        )
        if not isinstance(payload, dict):
            raise IntegrationError("Wutong discovery 返回格式无效", code="discovery_invalid_response")
        if payload.get("error") is not None:
            error = payload.get("error")
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise IntegrationError(
                f"Wutong discovery 返回错误：{message or 'unknown'}",
                code="discovery_remote_error",
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise IntegrationError("Wutong discovery 缺少 result", code="discovery_invalid_response")
        agents = result.get("agents")
        acs_map = result.get("acsMap")
        if not isinstance(agents, list) or not isinstance(acs_map, dict):
            raise IntegrationError(
                "Wutong discovery result 缺少 agents 或 acsMap",
                code="discovery_invalid_response",
            )
        return {
            "status": "ok",
            "query": text,
            "agents": agents,
            "acs_map": acs_map,
            "routes": result.get("routes") if isinstance(result.get("routes"), list) else [],
            "alive_map": result.get("aliveMap") if isinstance(result.get("aliveMap"), dict) else {},
            "authority": "Wutong",
            "data_source": "wutong_discovery",
        }
