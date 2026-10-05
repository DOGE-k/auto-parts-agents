from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import require_real_write_access


class RealWriteGateTests(unittest.TestCase):
    def test_project_login_is_required_even_when_legacy_token_is_configured(self):
        with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": "configured-token"}, clear=False):
            with self.assertRaisesRegex(HTTPException, "项目账号登录"):
                require_real_write_access("configured-token")

    def test_invalid_token_is_rejected(self):
        with patch("app.main.resolve_project_token", return_value=object()):
            with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": "configured-token"}, clear=False):
                with self.assertRaisesRegex(HTTPException, "真实系统写入令牌无效"):
                    require_real_write_access("wrong-token", "project-session")

    def test_authenticated_project_user_can_write_without_legacy_token(self):
        with patch("app.main.resolve_project_token", return_value=object()):
            with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": "configured-token"}, clear=False):
                self.assertTrue(require_real_write_access(None, "project-session"))

    def test_authenticated_project_user_can_write_when_legacy_gate_is_disabled(self):
        with patch("app.main.resolve_project_token", return_value=object()):
            with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": ""}, clear=False):
                self.assertTrue(require_real_write_access(None, "project-session"))
