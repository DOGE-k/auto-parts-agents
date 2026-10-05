from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.integrations.settings import IntegrationSettings
from app.main import ProjectLoginRequest, project_auth_login
from app.services.session_auth import login_openmes_session
from app.services.identity import RealIdentity
from app.services.project_auth import ProjectAuthError


def settings(**overrides) -> IntegrationSettings:
    values = dict(
        deepseek_base_url="https://deepseek.example",
        deepseek_model="deepseek-flash",
        deepseek_api_key="",
        erpnext_base_url="https://erp.example",
        erpnext_api_key="key",
        erpnext_api_secret="secret",
        erpnext_draft_writes_enabled=False,
        openmes_base_url="https://mes.example",
        openmes_user_token="server-integration-token",
        openmes_erp_api_key="",
    )
    values.update(overrides)
    return IntegrationSettings(**values)


def _login_handler(seen: list[httpx.Request]):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={
                    "message": "Login successful",
                    "data": {
                        "user": {"username": "operator1"},
                        "token": "session-token-1",
                        "force_password_change": False,
                    },
                },
            )
        return httpx.Response(404)

    return handler


class SessionAuthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_exchanges_credentials_for_short_lived_session(self):
        seen: list[httpx.Request] = []
        identity_tokens: list[str] = []

        class IdentityReadBack:
            def __init__(self, *args, **kwargs):
                identity_tokens.append(kwargs.get("user_token", ""))

            async def current_user(self):
                return {"data": {"username": "operator1", "roles": [{"role": "Quality Manager"}]}}

            async def aclose(self):
                pass

        transport = httpx.AsyncClient(
            base_url="https://mes.example/",
            transport=httpx.MockTransport(_login_handler(seen)),
        )
        try:
            with patch("app.services.identity.OpenMESClient", IdentityReadBack):
                result = await login_openmes_session(
                    settings(), username="operator1", password="secret", client=transport
                )
        finally:
            await transport.aclose()

        self.assertEqual(result["provider"], "openmes")
        self.assertEqual(result["token_type"], "bearer")
        self.assertEqual(result["access_token"], "session-token-1")
        self.assertFalse(result["force_password_change"])
        self.assertEqual(result["identity"]["subject"], "operator1")
        self.assertIn("Quality Manager", result["identity"]["roles"])

        login_request = seen[0]
        self.assertEqual(login_request.url.path, "/api/auth/login")
        forwarded = login_request.read()
        self.assertIn(b"operator1", forwarded)
        self.assertIn(b"secret", forwarded)
        # 身份回读使用的正是登录返回的会话令牌，而不是服务端集成令牌
        self.assertEqual(identity_tokens, ["session-token-1"])

    async def test_wrong_credentials_raise_upstream_error(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "The provided credentials are incorrect."})

        transport = httpx.AsyncClient(base_url="https://mes.example/", transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await login_openmes_session(settings(), username="operator1", password="bad", client=transport)
        finally:
            await transport.aclose()
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_login_requires_configured_openmes(self):
        with self.assertRaises(IntegrationNotConfigured):
            await login_openmes_session(settings(openmes_base_url=""), username="u", password="p")


class ProjectAuthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_login_sets_http_only_cookie(self):
        identity = RealIdentity(
            subject="admin",
            display_name="项目管理员",
            roles=("sales_manager",),
            authority="Project",
            provider="project",
        )
        from fastapi import Response

        with patch("app.main.login_project_user", return_value=("session-token", identity)):
            response = Response()
            result = project_auth_login(ProjectLoginRequest(username="u", password="p"), response)
        self.assertEqual(result["identity"]["provider"], "project")
        self.assertIn("project_session=session-token", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    async def test_project_login_invalid_credentials_maps_to_401(self):
        from fastapi import Response

        with patch(
            "app.main.login_project_user",
            side_effect=ProjectAuthError("invalid_credentials", "项目用户名或密码错误", 401),
        ):
            with self.assertRaises(HTTPException) as ctx:
                project_auth_login(ProjectLoginRequest(username="u", password="p"), Response())
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail["code"], "invalid_credentials")

    async def test_project_login_blank_credentials_maps_to_422(self):
        from fastapi import Response

        with patch(
            "app.main.login_project_user",
            side_effect=ProjectAuthError("invalid_login_input", "用户名和密码不能为空", 422),
        ):
            with self.assertRaises(HTTPException) as ctx:
                project_auth_login(ProjectLoginRequest(username=" ", password="p"), Response())
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["code"], "invalid_login_input")


if __name__ == "__main__":
    unittest.main()
