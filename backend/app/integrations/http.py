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
                # OpenAI 兼容错误体 {"error": {"message": ...}}（DeepSeek 等）
                if not candidate and isinstance(payload.get("error"), dict):
                    candidate = payload["error"].get("message")
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

    async def request_multipart(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send a bounded multipart request without following redirects."""
        if not path or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("API 路径必须是固定的站内相对路径")
        request_headers = {**self._default_headers, **dict(headers or {})}
        try:
            response = await self._client.request(
                method,
                path.lstrip("/"),
                data=data,
                files=files,
                headers=request_headers,
            )
        except httpx.TimeoutException as exc:
            raise IntegrationError("连接超时", code="timeout", retryable=True) from exc
        except httpx.NetworkError as exc:
            raise IntegrationError("网络连接失败", code="network_error", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError("HTTP 请求失败", code="http_error", retryable=False) from exc
        if response.status_code >= 400:
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
                retryable=response.status_code in {408, 425, 429} or response.status_code >= 500,
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError("远端返回的内容不是有效 JSON", code="invalid_json", retryable=False) from exc

    async def stream_sse(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ):
        """POST 并以 SSE 流消费响应，逐条 yield "data:" 载荷字符串（已去 [DONE]）。

        供 OpenAI 兼容的 stream=true 端点用：读超时按每次 read 计算，
        长回答不会因总时长超过 timeout_seconds 被掐断。错误语义与
        request_json 一致（4xx/5xx → IntegrationError）。
        """
        if not path or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("API 路径必须是固定的站内相对路径")
        request_headers = {**self._default_headers, **dict(headers or {})}
        try:
            async with self._client.stream(
                "POST", path.lstrip("/"), json=json_body, headers=request_headers
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    detail = "远端服务拒绝请求"
                    try:
                        payload = response.json()
                        candidate = payload.get("message") or payload.get("detail")
                        if not candidate and isinstance(payload.get("error"), dict):
                            candidate = payload["error"].get("message")
                        if isinstance(candidate, str) and candidate.strip():
                            detail = candidate.strip()[:300]
                    except (ValueError, AttributeError):
                        pass
                    raise IntegrationError(
                        detail, code="remote_http_error",
                        retryable=response.status_code in {408, 425, 429} or response.status_code >= 500,
                        status_code=response.status_code,
                    )
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data:"):
                            data = line[len("data:"):].strip()
                            if data and data != "[DONE]":
                                yield data
        except IntegrationError:
            raise
        except httpx.TimeoutException as exc:
            raise IntegrationError("连接超时", code="timeout", retryable=True) from exc
        except httpx.NetworkError as exc:
            raise IntegrationError("网络连接失败", code="network_error", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError("HTTP 请求失败", code="http_error", retryable=False) from exc
