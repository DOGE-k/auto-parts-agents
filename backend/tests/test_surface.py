from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.aip.aip_agent_service import AipAgentService
from app.runtime.surface import mock_demo_enabled, public_surface


class SurfaceTests(unittest.TestCase):
    def test_real_adapter_defaults_to_isolated_mock_surface(self):
        with patch.dict(os.environ, {"APP_ADAPTER_MODE": "real"}, clear=False):
            os.environ.pop("MOCK_DEMO_ENABLED", None)
            self.assertFalse(mock_demo_enabled())
            self.assertFalse(public_surface()["aip_mock_skills_enabled"])

    def test_explicit_override_can_enable_demo_surface(self):
        with patch.dict(os.environ, {"APP_ADAPTER_MODE": "real", "MOCK_DEMO_ENABLED": "true"}, clear=False):
            self.assertTrue(mock_demo_enabled())

    def test_skill_restriction_removes_unlisted_handlers(self):
        service = AipAgentService("test", "test")

        async def handler(_inputs):
            return {}

        service.register_skill("real.read", "real", handler)
        service.register_skill("mock.demo", "mock", handler)
        service.restrict_skills({"real.read"})
        self.assertEqual([item["id"] for item in service.list_skills()], ["real.read"])


if __name__ == "__main__":
    unittest.main()
