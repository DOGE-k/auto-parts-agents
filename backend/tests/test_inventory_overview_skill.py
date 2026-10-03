"""全厂库存总览技能测试（2026-10-02 用户需求：问答支持"给我看看所有库存"）。

覆盖：
- procurement.inventory_overview 技能已声明（read_only=True、requires_approval=False）
  且已注册（declared ∩ registered 目录含该技能）；
- 服务函数返回 ERPNext Bin 实时余量（假适配器打桩，不触真实系统）；
- ERP 不可达时如实返回 ERP_UNREACHABLE，不伪造数字。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import real_order
from app.services.capability_catalog import build_capability_catalog
from app.services.coordinator import REAL_SKILL_TOOLS

SKILL_ID = "procurement.inventory_overview"

# 按目录声明口径构造的注册技能 ID 集（与 REAL_SKILL_TOOLS 一一对应）
REGISTERED_IDS = [tool["skill_id"] for tool in REAL_SKILL_TOOLS]


class InventoryOverviewDeclarationTests(unittest.TestCase):
    def test_skill_declared_as_read_only_no_approval(self):
        tool = next((t for t in REAL_SKILL_TOOLS if t["skill_id"] == SKILL_ID), None)
        self.assertIsNotNone(tool, "REAL_SKILL_TOOLS 缺少 inventory_overview 声明")
        self.assertIs(tool["read_only"], True)
        self.assertIs(tool["requires_approval"], False)
        self.assertEqual(tool["aip_agent"], "procurement")
        self.assertEqual(tool["parameters"]["type"], "object")

    def test_catalog_contains_skill_when_registered(self):
        from collections import defaultdict

        registered_by_agent: dict[str, set[str]] = defaultdict(set)
        for tool in REAL_SKILL_TOOLS:
            registered_by_agent[tool["aip_agent"]].add(tool["skill_id"])
        catalog = build_capability_catalog(REAL_SKILL_TOOLS, dict(registered_by_agent))
        entries = catalog.get("entries") or catalog.get("skills") or []
        self.assertTrue(
            any(entry["skill_id"] == SKILL_ID for entry in entries),
            "目录未包含已声明且已注册的库存总览技能",
        )


class _FakeErpAdapter:
    """按 ERPAdapter 协议提供 Bin 总览的假适配器。"""

    def __init__(self, rows=None, error: Exception | None = None):
        self._rows = rows or []
        self._error = error

    async def list_inventory_overview(self, limit: int = 50):
        if self._error:
            raise self._error
        return self._rows[: max(1, min(int(limit), 200))]


class InventoryOverviewServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_real_bin_rows_with_authority(self):
        rows = [
            {"item_id": "CI-RAW", "warehouse": "Stores - APM", "actual_qty": "800",
             "reserved_qty": "0", "ordered_qty": "2533", "projected_qty": "3333",
             "authority": "ERPNext"},
        ]
        with patch.object(real_order, "get_erp_adapter", return_value=_FakeErpAdapter(rows)):
            result = await real_order.inventory_overview(limit=10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["bins"][0]["item_id"], "CI-RAW")
        self.assertEqual(result["authority"], "ERPNext")

    async def test_erp_unreachable_reports_honestly(self):
        with patch.object(real_order, "get_erp_adapter", return_value=_FakeErpAdapter(error=RuntimeError("连接失败"))):
            result = await real_order.inventory_overview()
        self.assertEqual(result["status"], "ERP_UNREACHABLE")
        self.assertEqual(result["bins"], [])
        self.assertIn("连接失败", result["error"])


if __name__ == "__main__":
    unittest.main()
