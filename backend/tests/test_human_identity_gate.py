"""项目账号统一身份门禁测试。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.main import require_real_human_identity


class HumanIdentityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_header_rejected_without_fallback(self):
        with self.assertRaises(HTTPException) as ctx:
            await require_real_human_identity(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail["code"], "project_login_required")

    async def test_blank_header_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await require_real_human_identity(authorization="   ")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_non_bearer_header_rejected(self):
        with self.assertRaises(HTTPException):
            await require_real_human_identity(authorization="Basic abc")

    async def test_project_bearer_session_is_accepted(self):
        fake_identity = SimpleNamespace(actor_id="admin")
        with patch("app.main.resolve_project_token", return_value=fake_identity) as resolve:
            identity = await require_real_human_identity(authorization="Bearer project-session-token")
        resolve.assert_called_once_with("project-session-token")
        self.assertEqual(identity.actor_id, "admin")

    async def test_openmes_bearer_is_not_an_approval_identity(self):
        with patch("app.main.resolve_project_token", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                await require_real_human_identity(authorization="Bearer openmes-token")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail["code"], "project_login_required")

    def test_lenient_dependency_still_used_by_read_routes(self):
        """只读身份路由仍可回读上游服务连接状态。"""
        from app.main import app as fastapi_app

        strict = {"require_real_human_identity"}
        checks = {
            "/api/real-orders/identity/me": False,
            "/api/real-orders/quality/todo": False,
            "/api/real-orders/quality/workflow-states": False,
            "/api/real-orders/quotations/{quotation_id}/approve": True,
            "/api/real-orders/erp/draft/from-quotation": True,
        }
        for route in fastapi_app.routes:
            path = getattr(route, "path", "")
            if path in checks:
                dep_names = {
                    getattr(getattr(d, "call", None), "__name__", "")
                    for d in getattr(route, "dependant", None).dependencies or []
                }
                uses_strict = bool(dep_names & strict)
                self.assertEqual(
                    uses_strict,
                    checks[path],
                    f"路由 {path} 的身份依赖与预期不符（实际 {'严格' if uses_strict else '宽松'}）",
                )

    def test_major_erp_mes_write_routes_require_project_identity(self):
        """ERP/MES 业务写入不能回退到服务端集成账号。"""
        from app.main import app as fastapi_app

        expected = {
            "/api/real-orders/quality/issues/{issue_id}/resolution-request",
            "/api/real-orders/quality/resolution-approvals/{approval_id}/approve",
            "/api/real-orders/work-orders/dispatch-request",
            "/api/real-orders/work-orders/dispatch-approvals/{approval_id}/approve",
            "/api/real-orders/work-orders/dispatch",
            "/api/real-orders/production-reports/request",
            "/api/real-orders/production-reports/approvals/{approval_id}/approve",
            "/api/real-orders/production-reports/execute",
            "/api/real-orders/quality-issues/registration-request",
            "/api/real-orders/quality-issues/approvals/{approval_id}/approve",
            "/api/real-orders/quality-issues/execute",
            "/api/real-orders/quality/issues/{issue_id}/resolve",
            "/api/real-orders/quality/issues/{issue_id}/disposition-request",
            "/api/real-orders/quality/disposition-approvals/{approval_id}/approve",
            "/api/real-orders/quality/issues/{issue_id}/disposition",
            "/api/real-orders/quality/issues/{issue_id}/close-request",
            "/api/real-orders/quality/close-approvals/{approval_id}/approve",
            "/api/real-orders/quality/issues/{issue_id}/close",
            "/api/real-orders/procurement/plans/{plan_id}/approve",
            "/api/real-orders/erp/draft/po-from-plan",
            "/api/real-orders/quotations/{quotation_id}/approve",
            "/api/real-orders/erp/draft/from-quotation",
        }
        routes = {
            getattr(route, "path", ""): route
            for route in fastapi_app.routes
            if "POST" in (getattr(route, "methods", None) or set())
        }
        self.assertEqual(expected - routes.keys(), set())
        for path in expected:
            route = routes[path]
            dep_names = {
                getattr(getattr(dep, "call", None), "__name__", "")
                for dep in getattr(route, "dependant", None).dependencies or []
            }
            self.assertIn(
                "require_real_human_identity",
                dep_names,
                f"路由 {path} 未使用项目登录身份门禁",
            )


if __name__ == "__main__":
    unittest.main()
