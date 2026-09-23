from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.integrations.errors import IntegrationError


def validate_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("服务地址必须是完整的 http:// 或 https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("服务地址不得包含账号、密码、查询参数或片段")
    return value


class JsonHttpClient:
    """Small JSON transport with bounded timeouts and no redirect credential leaks."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url)
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("timeout_seconds 必须在 0 到 120 秒之间")
        self._default_headers = dict(headers or {})
        self._client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        if not path or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("API 路径必须是固定的站内相对路径")
        request_headers = {**self._default_headers, **dict(headers or {})}
        try:
            response = await self._client.request(
                method,
                path.lstrip("/"),
                params=params,
                json=json_body,
                headers=request_headers,
            )
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                "连接超时", code="timeout", retryable=True
            ) from exc
        except httpx.NetworkError as exc:
            raise IntegrationError(
                "网络连接失败", code="network_error", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(
                "HTTP 请求失败", code="http_error", retryable=False
            ) from exc

        if response.status_code >= 400:
            retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
            detail = "远端服务拒绝请求"
            try:
                payload = response.json()
                candidate = payload.get("message") or payload.get("exception") or payload.get("detail")
                if isinstance(candidate, str) and candidate.strip():
                    detail = candidate.strip()[:300]
            except (ValueError, AttributeError):
                pass
            raise IntegrationError(
                detail,
                code="remote_http_error",
                retryable=retryable,
                status_code=response.status_code,
            )

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError(
                "远端返回的内容不是有效 JSON", code="invalid_json", retryable=False
            ) from exc
