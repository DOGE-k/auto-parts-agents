"""统一能力目录（运行时构建 + 校验）测试。

对应 AI_HANDOFF_PLAN P0-5 遗留收口（docs/handoff_2026-10-02_dynamic_capability_catalog.md）：
- 目录条目 = 声明元数据（REAL_SKILL_TOOLS）∩ AIP 服务运行时注册；
- 权限属性必须显式声明（read_only=True / requires_approval=False），缺失即拒绝；
- 声明未注册 / 注册未声明、重复 ID、未知智能体、缺参数 schema 均拒绝并留可读原因；
- 校验失败不吞成空成功；本地声明绝不冒充外部注册结果。
"""
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.capability_catalog import (
    DECLARED_SKILL_VERSION,
    build_capability_catalog,
    catalog_or_declared,
    declared_only_catalog,
    reset_runtime_catalog,
    store_runtime_catalog,
)
from app.services.coordinator import REAL_SKILL_TOOLS


def _registered_from_services() -> dict[str, set[str]]:
    from app.aip.agents.procurement_aip import create_procurement_aip_service
    from app.aip.agents.quality_document_aip import create_quality_document_aip_service
    from app.aip.agents.quotation_aip import create_quotation_aip_service
    from app.aip.agents.tracking_aip import create_tracking_aip_service

    services = {
        "quotation": create_quotation_aip_service(),
        "procurement": create_procurement_aip_service(),
        "tracking": create_tracking_aip_service(),
        "quality-document": create_quality_document_aip_service(),
    }
    return {agent: set(svc.registered_skill_ids()) for agent, svc in services.items()}


class CapabilityCatalogTests(unittest.TestCase):
    def test_verified_catalog_from_real_services(self):
        catalog = build_capability_catalog(REAL_SKILL_TOOLS, _registered_from_services())
        # 声明的技能全部通过校验（Mock 技能的 registered_not_declared 报告不算声明失败）
        self.assertEqual(
            [r for r in catalog["rejections"] if r["kind"] == "declared_not_registered"], [])
        self.assertEqual(
            [r for r in catalog["rejections"] if r["kind"] == "invalid_declaration"], [])
        entries = catalog["entries"]
        self.assertEqual(len(entries), len(REAL_SKILL_TOOLS))
        by_id = {e["skill_id"]: e for e in entries}
        for tool in REAL_SKILL_TOOLS:
            entry = by_id[tool["skill_id"]]
            self.assertEqual(entry["agent_type"], tool["agent_type"])
            self.assertEqual(entry["aip_agent"], tool["aip_agent"])
            self.assertEqual(entry["description"], tool["description"])
            self.assertEqual(entry["parameters"], tool["parameters"])
            # 权限属性是目录层强制显式声明，不是装饰字段
            self.assertIs(entry["read_only"], True)
            self.assertIs(entry["requires_approval"], False)
            # 来源与端点可追溯；端点来自已知的 AIP 路由约定，不拼接猜测
            self.assertEqual(entry["source"], "aip_runtime+declared_local")
            self.assertEqual(entry["endpoint"], f"/aip/{tool['aip_agent']}/rpc")
            self.assertEqual(entry["version"], DECLARED_SKILL_VERSION)
            self.assertTrue(entry["built_at"])
            # 输出 schema 无注册来源，如实标记缺口而不是编造
            self.assertIsNone(entry["output_schema"])

    def test_declared_but_not_registered_rejected(self):
        registered = _registered_from_services()
        registered["tracking"].discard("tracking.track_real")
        catalog = build_capability_catalog(REAL_SKILL_TOOLS, registered)
        ids = {e["skill_id"] for e in catalog["entries"]}
        self.assertNotIn("tracking.track_real", ids)
        reasons = " | ".join(r["reason"] for r in catalog["rejections"])
        self.assertIn("tracking.track_real", reasons)
        self.assertIn("未注册", reasons)

    def test_registered_but_not_declared_reported_and_excluded(self):
        registered = _registered_from_services()
        registered["tracking"].add("tracking.detect_risk")  # Mock 模拟技能
        catalog = build_capability_catalog(REAL_SKILL_TOOLS, registered)
        ids = {e["skill_id"] for e in catalog["entries"]}
        self.assertNotIn("tracking.detect_risk", ids)
        reasons = " | ".join(r["reason"] for r in catalog["rejections"])
        self.assertIn("tracking.detect_risk", reasons)
        self.assertIn("未声明", reasons)

    def test_duplicate_skill_id_rejected(self):
        duplicated = [dict(REAL_SKILL_TOOLS[0]), dict(REAL_SKILL_TOOLS[0])]
        catalog = build_capability_catalog(duplicated, None)
        ids = [e["skill_id"] for e in catalog["entries"]]
        # 首条合法声明保留，重复条目被拒绝
        self.assertEqual(ids.count(REAL_SKILL_TOOLS[0]["skill_id"]), 1)
        reasons = " | ".join(r["reason"] for r in catalog["rejections"])
        self.assertIn("重复", reasons)

    def test_unknown_aip_agent_rejected(self):
        bad = dict(REAL_SKILL_TOOLS[0])
        bad["skill_id"] = "unknown.probe"
        bad["aip_agent"] = "unknown-agent"
        catalog = build_capability_catalog([bad], None)
        self.assertEqual(catalog["entries"], [])
        self.assertIn("未知智能体", catalog["rejections"][0]["reason"])

    def test_missing_parameters_schema_rejected(self):
        bad = dict(REAL_SKILL_TOOLS[0])
        bad["skill_id"] = "tracking.no_schema"
        bad.pop("parameters")
        catalog = build_capability_catalog([bad], None)
        self.assertEqual(catalog["entries"], [])
        self.assertIn("parameters", catalog["rejections"][0]["reason"])

        bad2 = dict(bad)
        bad2["parameters"] = {"type": "string"}  # 必须是 object schema
        catalog2 = build_capability_catalog([bad2], None)
        self.assertEqual(catalog2["entries"], [])

    def test_permission_attributes_required(self):
        base = dict(REAL_SKILL_TOOLS[0])
        base["skill_id"] = "tracking.perms_probe"

        no_flag = dict(base)
        no_flag.pop("read_only")
        catalog = build_capability_catalog([no_flag], None)
        self.assertEqual(catalog["entries"], [])

        not_readonly = dict(base)
        not_readonly["read_only"] = False
        catalog2 = build_capability_catalog([not_readonly], None)
        self.assertEqual(catalog2["entries"], [])

        needs_approval = dict(base)
        needs_approval["requires_approval"] = True
        catalog3 = build_capability_catalog([needs_approval], None)
        self.assertEqual(catalog3["entries"], [])
        # 三种情况都必须留下可读拒绝原因，而不是静默消失
        for c in (catalog, catalog2, catalog3):
            self.assertTrue(c["rejections"])

    def test_declared_only_fallback_marked_local(self):
        catalog = declared_only_catalog()
        self.assertEqual(len(catalog["entries"]), len(REAL_SKILL_TOOLS))
        for entry in catalog["entries"]:
            self.assertEqual(entry["source"], "declared_local")
            # 即使无运行时上下文，也绝不冒充外部注册结果
            self.assertNotIn("registry", entry["source"])
            self.assertNotIn("external", entry["source"])

    def test_all_invalid_declarations_visible_failure(self):
        bad = {"skill_id": "x.y", "aip_agent": "unknown", "description": "",
               "parameters": {}, "read_only": True, "requires_approval": False}
        catalog = build_capability_catalog([bad], None)
        self.assertEqual(catalog["entries"], [])
        self.assertEqual(len(catalog["rejections"]), 1)
        self.assertEqual(catalog["rejections"][0]["kind"], "invalid_declaration")
        self.assertTrue(catalog["rejections"][0]["reason"])
        self.assertTrue(catalog["meta"]["built_at"])


class RuntimeCatalogCacheTests(unittest.TestCase):
    def tearDown(self):
        reset_runtime_catalog()

    def test_store_get_reset_roundtrip(self):
        self.assertFalse(catalog_or_declared()["meta"]["runtime"])
        verified = build_capability_catalog(REAL_SKILL_TOOLS, _registered_from_services())
        store_runtime_catalog(verified)
        active = catalog_or_declared()
        self.assertTrue(active["meta"]["runtime"])
        self.assertEqual(active["meta"]["source"], "aip_runtime+declared_local")
        reset_runtime_catalog()
        fallback = catalog_or_declared()
        self.assertFalse(fallback["meta"]["runtime"])
        self.assertEqual(fallback["meta"]["source"], "declared_local")

    def test_coordinator_consumes_active_catalog(self):
        from app.services import coordinator as coord_module

        reset_runtime_catalog()
        specs_fallback = coord_module._tool_specs()
        self.assertEqual(len(specs_fallback), len(REAL_SKILL_TOOLS))

        verified = build_capability_catalog(REAL_SKILL_TOOLS, _registered_from_services())
        store_runtime_catalog(verified)
        specs_runtime = coord_module._tool_specs()
        self.assertEqual(
            [s["function"]["name"] for s in specs_runtime],
            [s["function"]["name"] for s in specs_fallback],
        )
        index = coord_module._tool_index()
        self.assertEqual(len(index), len(REAL_SKILL_TOOLS))
        name_to_skill = coord_module._llm_name_to_skill()
        self.assertEqual(name_to_skill["tracking__track_real"], "tracking.track_real")


class CapabilityCatalogEndpointTests(unittest.TestCase):
    def tearDown(self):
        reset_runtime_catalog()

    def test_catalog_endpoint_exposes_entries_and_rejections(self):
        from app.aip import init_aip_agents

        app = FastAPI()
        init_aip_agents(app)
        with TestClient(app) as client:
            resp = client.get("/aip/capability-catalog")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(len(payload["entries"]), len(REAL_SKILL_TOOLS))
        # 目录报告里唯一允许的"拒绝"是 Mock 技能的注册未声明（真实表面隔离证据）；
        # 声明本身必须零失败
        kinds = {r["kind"] for r in payload["rejections"]}
        self.assertEqual(kinds - {"registered_not_declared"}, set())
        reasons = " | ".join(r["reason"] for r in payload["rejections"])
        self.assertIn("tracking.detect_risk", reasons)
        self.assertEqual(payload["meta"]["source"], "aip_runtime+declared_local")
        self.assertTrue(payload["meta"]["built_at"])
        # 真实表面只暴露目录中的真实只读技能：Mock 技能不得进入目录条目
        ids = {e["skill_id"] for e in payload["entries"]}
        self.assertNotIn("tracking.detect_risk", ids)


if __name__ == "__main__":
    unittest.main()
