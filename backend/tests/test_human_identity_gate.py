"""审批/写入的人工身份门禁测试（2026-10-02 用户实测回归）。

用户发现：未登录也能走完报价审批并创建 ERP 草稿——根源是写路由依赖的
require_real_identity 在无 Authorization 头时回落到 ERPNext 服务端集成账号
（Administrator），审批以集成账号名义留痕。修复：24 个审批/写入 POST 路由
改用 require_real_human_identity——无浏览器登录会话直接 401，不回落；
只读 GET（identity/me、质量待办、工作流状态）保留宽松回落。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import require_real_human_identity


class HumanIdentityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_header_rejected_without_fallback(self):
        with self.assertRaises(HTTPException) as ctx:
            await require_real_human_identity(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail["code"], "approver_login_required")

    async def test_blank_header_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await require_real_human_identity(authorization="   ")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_non_bearer_header_rejected(self):
        with self.assertRaises(HTTPException):
            await require_real_human_identity(authorization="Basic abc")

    async def test_valid_header_delegates_to_existing_resolver(self):
        async def fake_resolve(request_bearer_token: str):
            self.assertEqual(request_bearer_token, "admin-session-token")
            return {"actor_id": "admin", "provider": "openmes"}

        with patch("app.services.identity.resolve_real_identity", fake_resolve):
            identity = await require_real_human_identity(authorization="Bearer admin-session-token")
        self.assertEqual(identity["actor_id"], "admin")

    def test_lenient_dependency_still_used_by_read_routes(self):
        """identity/me 等只读 GET 保留宽松回落（未登录显示数据连接账号，不算失败）。"""
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


if __name__ == "__main__":
    unittest.main()
