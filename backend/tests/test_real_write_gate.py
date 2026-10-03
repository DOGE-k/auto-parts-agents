from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import require_real_write_access


class RealWriteGateTests(unittest.TestCase):
    def test_writes_are_disabled_when_token_is_not_configured(self):
        with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": ""}, clear=False):
            with self.assertRaisesRegex(HTTPException, "真实系统写入未启用"):
                require_real_write_access(None)

    def test_invalid_token_is_rejected(self):
        with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": "configured-token"}, clear=False):
            with self.assertRaisesRegex(HTTPException, "真实系统写入令牌无效"):
                require_real_write_access("wrong-token")

    def test_configured_token_allows_the_approval_flow_to_continue(self):
        with patch.dict(os.environ, {"REAL_WRITE_API_TOKEN": "configured-token"}, clear=False):
            self.assertTrue(require_real_write_access("configured-token"))
