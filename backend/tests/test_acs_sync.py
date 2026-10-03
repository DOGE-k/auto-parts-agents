"""ACS 文件与当前真实 AIP 能力目录的一致性测试。"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.services.coordinator import REAL_SKILL_TOOLS


class AcsRealSkillSyncTests(unittest.TestCase):
    def test_generated_acs_contains_every_real_skill_once(self):
        acs_dir = Path(__file__).resolve().parents[1] / "acs"
        files = {
            "quotation": acs_dir / "quotation_acs.json",
            "procurement": acs_dir / "procurement_acs.json",
            "tracking": acs_dir / "tracking_acs.json",
            "quality-document": acs_dir / "quality_document_acs.json",
        }
        expected_by_agent: dict[str, set[str]] = {}
        for tool in REAL_SKILL_TOOLS:
            expected_by_agent.setdefault(tool["aip_agent"], set()).add(tool["skill_id"])

        for agent, path in files.items():
            self.assertTrue(path.exists(), f"ACS 文件不存在: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            ids = [skill["id"] for skill in payload["skills"]]
            self.assertEqual(len(ids), len(set(ids)), f"ACS 技能 ID 重复: {path}")
            self.assertEqual(
                expected_by_agent.get(agent, set()),
                {skill_id for skill_id in ids if skill_id in expected_by_agent.get(agent, set())},
                f"ACS 未同步真实技能: {path}",
            )


if __name__ == "__main__":
    unittest.main()
