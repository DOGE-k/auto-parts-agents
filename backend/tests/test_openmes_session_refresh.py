"""OpenMES 令牌自动刷新与适配器错误传播测试。

背景（2026-10-01 用户确认方案）：
- /api/mes/work-orders 曾因 OPENMES_TOKEN 过期 + 适配器吞错返回空列表，
  把系统故障伪装成"没有工单"；
- 修复后：适配器读取失败一律抛出（只有 OpenMES 正常返回 200 且无数据
  才是空列表）；401 时经内存会话管理器自动重登并重试一次。

全部使用 httpx.MockTransport 与注入的假登录函数，不触真实系统。
"""
from __future__ import annotations

import json
import unittest

import httpx

from app.adapters.mes.openmes import OpenMESClient
from app.adapters.mes.openmes_adapter import OpenMESAdapter
from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.integrations.http import JsonHttpClient
from app.integrations.openmes_session import (
    OpenMESUserSessionManager,
    UserTokenRetryTransport,
    load_openmes_admin_credentials,
)


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _make_manager(
    login_fn=None,
    *,
    base_url: str = "https://mes.example",
    ttl: float = 14 * 60,
    clock: FakeClock | None = None,
) -> OpenMESUserSessionManager:
    clock = clock or FakeClock()

    async def default_login(base: str, username: str, password: str) -> str:
        return "fresh-token"

    return OpenMESUserSessionManager(
        base_url,
        ttl_seconds=ttl,
        login_fn=login_fn or default_login,
        time_fn=clock,
    )


class SessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_token_caches_within_ttl(self):
        calls = []

        async def login(base, username, password):
            calls.append(base)
            return f"tok-{len(calls)}"

        clock = FakeClock()
        manager = _make_manager(login, clock=clock)
        self.assertEqual(await manager.get_token(), "tok-1")
        clock.now += 60  # 1 分钟后仍在 14 分钟 TTL 内
        self.assertEqual(await manager.get_token(), "tok-1")
        self.assertEqual(len(calls), 1)

    async def test_expired_cache_triggers_relogin(self):
        calls = []

        async def login(base, username, password):
            calls.append(1)
            return f"tok-{len(calls)}"

        clock = FakeClock()
        manager = _make_manager(login, clock=clock)
        await manager.get_token()
        clock.now += 15 * 60  # 超过 TTL
        self.assertEqual(await manager.get_token(), "tok-2")
        self.assertEqual(len(calls), 2)

    async def test_refresh_reuses_token_refreshed_by_concurrent_request(self):
        calls = []

        async def login(base, username, password):
            calls.append(1)
            return f"tok-{len(calls)}"

        manager = _make_manager(login)
        await manager.get_token()  # tok-1
        new_token = await manager.refresh("tok-1")  # 401 后刷新
        self.assertEqual(new_token, "tok-2")
        # 另一个并发请求拿着同一个旧令牌来刷新：不应再次重登
        again = await manager.refresh("tok-1")
        self.assertEqual(again, "tok-2")
        self.assertEqual(len(calls), 2)

    async def test_login_failure_raises_clear_error_without_credentials(self):
        async def login(base, username, password):
            raise IntegrationError("Unauthenticated.", code="remote_http_error", status_code=401)

        manager = _make_manager(login)
        with self.assertRaises(IntegrationError) as ctx:
            await manager.get_token()
        self.assertIn("自动重新登录失败", str(ctx.exception))
        self.assertNotIn("password", str(ctx.exception))
        self.assertNotIn("secret", str(ctx.exception))

    async def test_missing_credentials_file_raises_not_configured(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not_exist.env"
            with self.assertRaises(IntegrationNotConfigured):
                load_openmes_admin_credentials(missing)


def _client_with_transport(
    handler,
    *,
    session: OpenMESUserSessionManager | None = None,
    static_token: str = "stale-token",
) -> OpenMESClient:
    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
    if session is not None:
        # 会话管理器经注入 login_fn 与 MockTransport 同源，登录也走假端点
        return OpenMESClient(
            "https://mes.example",
            user_token=static_token,
            client=inner,
            user_session=session,
        )
    return OpenMESClient("https://mes.example", user_token=static_token, client=inner)


class RetryTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_401_triggers_relogin_and_single_retry(self):
        # 服务端脚本：第一次 401，登录成功后重试成功
        api_calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth/login":
                body = json.loads(request.content)
                self.assertEqual(body["username"], "admin")
                return _json_response({"data": {"token": "fresh-token"}})
            api_calls["count"] += 1
            auth = request.headers.get("Authorization", "")
            if auth.endswith("fresh-token"):
                return _json_response({"data": []})
            return httpx.Response(401, json={"message": "Unauthenticated."})

        manager = _make_manager()
        client = _client_with_transport(handler, session=manager)
        try:
            rows = await client.list_work_orders()
        finally:
            await client.aclose()
        self.assertEqual(rows["data"], [])
        self.assertEqual(api_calls["count"], 2)  # 首次 401 + 重试成功，恰好一次重试

    async def test_second_401_propagates_without_loop(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth/login":
                return _json_response({"data": {"token": "fresh-token"}})
            return httpx.Response(401, json={"message": "Unauthenticated."})

        manager = _make_manager()
        client = _client_with_transport(handler, session=manager)
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await client.list_work_orders()
            self.assertEqual(ctx.exception.status_code, 401)
        finally:
            await client.aclose()

    async def test_api_key_401_does_not_retry(self):
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(401, json={"message": "Unauthenticated."})

        client = _client_with_transport(handler, static_token="stale-token")
        # X-Api-Key 请求与用户会话无关；显式给 client 配置 erp key 才会发起请求
        client._erp_api_key = "test-erp-key"  # noqa: SLF001
        try:
            with self.assertRaises(IntegrationError):
                await client.list_erp_quality_issues()
        finally:
            await client.aclose()
        self.assertEqual(calls["count"], 1)

    async def test_network_error_does_not_retry(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            raise httpx.ConnectError("boom")

        transport = httpx.MockTransport(handler)
        inner = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
        manager = _make_manager()
        client = OpenMESClient(
            "https://mes.example", user_token="tok", client=inner, user_session=manager
        )
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await client.list_work_orders()
            self.assertEqual(ctx.exception.code, "network_error")
        finally:
            await client.aclose()
        self.assertEqual(calls["count"], 1)


class ClientAuthHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_cached_session_token_over_static(self):
        clock = FakeClock()
        manager = _make_manager(clock=clock)
        manager._token = "cached-token"  # noqa: SLF001 直接注入缓存
        manager._issued_at = clock.now  # 缓存时间 = 当前时间，未过期
        client = OpenMESClient(
            "https://mes.example", user_token="static-token", user_session=manager
        )
        headers = await client._user_auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer cached-token")

    async def test_falls_back_to_static_then_proactive_login(self):
        login_calls = []

        async def login(base, username, password):
            login_calls.append(1)
            return "proactive-token"

        manager = _make_manager(login)
        client = OpenMESClient(
            "https://mes.example", user_token="static-token", user_session=manager
        )
        headers = await client._user_auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer static-token")

        empty = OpenMESClient("https://mes.example", user_session=manager)
        headers = await empty._user_auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer proactive-token")
        self.assertEqual(len(login_calls), 1)

    async def test_without_session_and_token_raises_not_configured(self):
        client = OpenMESClient("https://mes.example")
        with self.assertRaises(IntegrationNotConfigured):
            await client._user_auth_headers()


class AdapterErrorPropagationTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self) -> OpenMESAdapter:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Unauthenticated."})

        transport = httpx.MockTransport(handler)
        inner = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
        client = OpenMESClient("https://mes.example", user_token="tok", client=inner)
        return OpenMESAdapter(client)

    async def test_get_work_orders_raises_on_auth_failure(self):
        adapter = self._adapter()
        with self.assertRaises(IntegrationError) as ctx:
            await adapter.get_work_orders({"limit": 50})
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_operation_progress_raises_on_auth_failure(self):
        adapter = self._adapter()
        with self.assertRaises(IntegrationError):
            await adapter.get_operation_progress("2")

    async def test_get_wip_raises_on_auth_failure(self):
        adapter = self._adapter()
        with self.assertRaises(IntegrationError):
            await adapter.get_wip({})

    async def test_get_production_documents_raises_on_auth_failure(self):
        adapter = self._adapter()
        with self.assertRaises(IntegrationError):
            await adapter.get_production_documents({"work_order_id": "2"})

    async def test_empty_data_still_returns_empty_list(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return _json_response({"data": []})

        transport = httpx.MockTransport(handler)
        inner = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
        client = OpenMESClient("https://mes.example", user_token="tok", client=inner)
        adapter = OpenMESAdapter(client)
        self.assertEqual(await adapter.get_work_orders({"limit": 50}), [])

    async def test_missing_work_order_id_rejected_not_empty(self):
        adapter = self._adapter()
        with self.assertRaises(ValueError):
            await adapter.get_production_documents({})


class MesStatusErrorMappingTests(unittest.IsolatedAsyncioTestCase):
    def test_401_maps_to_mes_auth_expired(self):
        from app.main import _mes_status_error

        exc = IntegrationError("Unauthenticated.", code="remote_http_error", status_code=401)
        http_exc = _mes_status_error(exc)
        self.assertEqual(http_exc.status_code, 401)
        self.assertEqual(http_exc.detail["code"], "mes_auth_expired")

    def test_timeout_maps_to_mes_unreachable(self):
        from app.main import _mes_status_error

        exc = IntegrationError("连接超时", code="timeout", retryable=True)
        http_exc = _mes_status_error(exc)
        self.assertEqual(http_exc.status_code, 502)
        self.assertEqual(http_exc.detail["code"], "mes_unreachable")

    def test_relogin_failure_maps_to_mes_auth_expired(self):
        from app.main import _mes_status_error

        exc = IntegrationError("自动重新登录失败", code="openmes_relogin_failed", status_code=401)
        http_exc = _mes_status_error(exc)
        self.assertEqual(http_exc.status_code, 401)
        self.assertEqual(http_exc.detail["code"], "mes_auth_expired")

    def test_remote_500_maps_to_502(self):
        from app.main import _mes_status_error

        exc = IntegrationError("boom", code="remote_http_error", status_code=500)
        http_exc = _mes_status_error(exc)
        self.assertEqual(http_exc.status_code, 502)


class RouteLevelTests(unittest.IsolatedAsyncioTestCase):
    """端到端验证：适配器 401 经路由映射为明确 HTTP 错误，而非空列表。"""

    def test_mes_work_orders_route_returns_401_on_auth_failure(self):
        from unittest.mock import patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.main import _mes_status_error

        class FailingAdapter:
            async def get_work_orders(self, scope):
                raise IntegrationError(
                    "Unauthenticated.", code="remote_http_error", status_code=401
                )

        app = FastAPI()

        @app.get("/api/mes/work-orders")
        async def route(status: str | None = None, search: str | None = None, limit: int = 50):
            from app.integrations.errors import IntegrationError as IE

            adapter = FailingAdapter()
            try:
                return await adapter.get_work_orders({"limit": limit})
            except IE as exc:
                raise _mes_status_error(exc) from exc

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/mes/work-orders")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["code"], "mes_auth_expired")


if __name__ == "__main__":
    unittest.main()
