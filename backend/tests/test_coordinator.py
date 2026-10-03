"""协调智能体（动态协同）测试。

覆盖：
- 假 LLM 驱动工具调用循环 → 调用链与回答组装、协调运行持久化
- 工具失败回喂 LLM（不中断、如实标注 error）
- 轮次上限保护
- 能力目录与 AIP 注册技能一致性（"发现能力"的依据必须真实可调）
- DEEPSEEK_API_KEY 未配置时明确报错
- 真实技能处理器确实调用 real_order 真实函数（薄封装接线验证）

全部使用隔离测试数据库（见 conftest.py），不触真实系统。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.persistence.database import SessionLocal
from app.persistence.models import RealAgentRunRow
from app.services import coordinator as coord_module
from app.services.coordinator import (
    REAL_SKILL_TOOLS,
    BusinessCoordinator,
    build_coordinator,
)


class _FakeLLM:
    """按脚本依次返回 chat_completion 结果的假 DeepSeek 客户端。"""

    def __init__(self, script: list[dict]):
        self.script = script
        self.calls: list[dict] = []

    async def chat_completion(self, messages, *, tools=None, tool_choice="auto", max_tokens=2048):
        self.calls.append({"messages": messages, "tools": tools})
        return self.script.pop(0)

    async def aclose(self) -> None:
        return None


def _tool_call(call_id: str, skill_id: str, arguments: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": skill_id, "arguments": arguments},
    }


class CoordinatorLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_work_order_number_bridge_precedes_quality_lookup(self):
        llm = _FakeLLM([
            {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call("c1", "tracking__find_real_by_no", '{"work_order_no": "WO-2026-001"}')],
            }}]},
            {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call("c2", "quality__assess_quality_impact", '{"work_order_id": "2"}')],
            }}]},
            {"choices": [{"message": {
                "role": "assistant",
                "content": "WO-2026-001 当前质量状态已完成核实。",
            }}]},
        ])
        invoked: list[tuple[str, str, dict]] = []

        async def fake_invoker(agent_type, skill_id, inputs):
            invoked.append((agent_type, skill_id, inputs))
            if skill_id == "tracking.find_real_by_no":
                return {"found": True, "work_order_id": "2", "work_order_no": "WO-2026-001", "authority": "OpenMES"}
            return {"status": "ok", "work_order_id": "2", "work_order_no": "WO-2026-001", "quality_gate_passed": False}

        coordinator = BusinessCoordinator(llm, skill_invoker=fake_invoker)
        try:
            result = await coordinator.ask("WO-2026-001 有质量异常吗？")
        finally:
            await coordinator.aclose()

        self.assertEqual(
            [(skill, args) for _, skill, args in invoked],
            [
                ("tracking.find_real_by_no", {"work_order_no": "WO-2026-001"}),
                ("quality.assess_quality_impact", {"work_order_id": "2"}),
            ],
        )
        self.assertEqual(result["answer"], "WO-2026-001 当前质量状态已完成核实。")

    async def test_tool_loop_builds_chain_and_persists_run(self):
        llm = _FakeLLM([
            {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call("c1", "tracking__track_real", '{"work_order_id": "9"}')],
            }}]},
            {"choices": [{"message": {
                "role": "assistant",
                "content": "工单 9 已完成 90%，预计明天完成。",
            }}]},
        ])
        invoked: list[tuple[str, str, dict]] = []

        async def fake_invoker(agent_type, skill_id, inputs):
            invoked.append((agent_type, skill_id, inputs))
            return {
                "work_order_no": "TEST_WO_PAGE_00023",
                "completion_rate": 90.0,
                "authority": "OpenMES",
                "data_source": "openmes_api",
            }

        coordinator = BusinessCoordinator(llm, skill_invoker=fake_invoker)
        try:
            result = await coordinator.ask("SAL-ORD-2026-00023 什么时候能做完？")
        finally:
            await coordinator.aclose()

        self.assertEqual(len(invoked), 1)
        self.assertEqual(invoked[0][0], "tracking")
        self.assertEqual(invoked[0][1], "tracking.track_real")
        self.assertEqual(invoked[0][2], {"work_order_id": "9"})
        self.assertEqual(result["answer"], "工单 9 已完成 90%，预计明天完成。")
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(result["tool_count"], 1)
        step = result["call_chain"][0]
        self.assertEqual(step["caller"], "coordinator")
        self.assertEqual(step["callee"], "tracking")
        self.assertEqual(step["status"], "ok")
        self.assertIn("completion_rate", step["result_summary"])

        # 协调运行记录已持久化，且含完整调用链
        with SessionLocal() as session:
            row = session.get(RealAgentRunRow, result["coordination_run_id"])
            self.assertIsNotNone(row)
            self.assertEqual(row.agent_type, "coordinator")
            self.assertEqual(row.result_json["call_chain"][0]["skill_id"], "tracking.track_real")

    async def test_tool_failure_is_fed_back_and_reported(self):
        llm = _FakeLLM([
            {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call("c1", "quality__get_real_package", '{"work_order_id": "5"}')],
            }}]},
            {"choices": [{"message": {
                "role": "assistant",
                "content": "质量数据查询失败，无法回答。",
            }}]},
        ])

        async def failing_invoker(agent_type, skill_id, inputs):
            raise RuntimeError("MES 连接失败")

        coordinator = BusinessCoordinator(llm, skill_invoker=failing_invoker)
        try:
            result = await coordinator.ask("这单质量有问题吗？")
        finally:
            await coordinator.aclose()

        self.assertEqual(result["call_chain"][0]["status"], "error")
        self.assertIn("MES 连接失败", result["call_chain"][0]["result_summary"])
        # 错误信息回喂给 LLM（第二轮请求的 messages 中包含 tool 结果）
        second_call_messages = llm.calls[1]["messages"]
        tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("MES 连接失败", tool_messages[0]["content"])

    async def test_round_limit_stops_the_loop(self):
        endless = {"choices": [{"message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool_call("c", "tracking__track_real", '{"work_order_id": "1"}')],
        }}]}

        class _EndlessLLM:
            async def chat_completion(self, messages, **kwargs):
                return endless

            async def aclose(self):
                return None

        async def fake_invoker(agent_type, skill_id, inputs):
            return {"ok": True}

        coordinator = BusinessCoordinator(_EndlessLLM(), skill_invoker=fake_invoker)
        try:
            result = await coordinator.ask("反复调用会怎样？")
        finally:
            await coordinator.aclose()
        self.assertEqual(result["tool_count"], coord_module.MAX_TOOL_ROUNDS)
        self.assertIn("上限", result["answer"])

    async def test_unknown_skill_is_reported_not_fatal(self):
        llm = _FakeLLM([
            {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call("c1", "ghost__skill", '{}')],
            }}]},
            {"choices": [{"message": {"role": "assistant", "content": "工具不存在。"}}]},
        ])

        async def fake_invoker(agent_type, skill_id, inputs):  # pragma: no cover
            raise AssertionError("未知技能不应被调用")

        coordinator = BusinessCoordinator(llm, skill_invoker=fake_invoker)
        try:
            result = await coordinator.ask("调用一个不存在的技能")
        finally:
            await coordinator.aclose()
        self.assertEqual(result["call_chain"][0]["status"], "error")
        self.assertIn("未知技能", result["call_chain"][0]["result_summary"])


class ToolCatalogConsistencyTests(unittest.TestCase):
    """能力目录里的每个技能必须真实注册在对应 AIP 服务上。"""

    def test_real_skills_are_registered_on_aip_services(self):
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
        registered = {
            agent: {s["id"] for s in svc.list_skills()}
            for agent, svc in services.items()
        }
        for tool in REAL_SKILL_TOOLS:
            self.assertIn(
                tool["skill_id"],
                registered[tool["aip_agent"]],
                f"能力目录技能 {tool['skill_id']} 未注册在 {tool['aip_agent']} 的 AIP 服务上",
            )

    def test_tool_specs_are_valid_openai_function_format(self):
        specs = coord_module._tool_specs()
        self.assertEqual(len(specs), len(REAL_SKILL_TOOLS))
        for spec, tool in zip(specs, REAL_SKILL_TOOLS):
            self.assertEqual(spec["type"], "function")
            self.assertEqual(spec["function"]["name"], coord_module._llm_tool_name(tool["skill_id"]))
            # DeepSeek 工具名约束：^[a-zA-Z0-9_-]+$（技能 ID 中的点映射为双下划线）
            import re
            self.assertRegex(spec["function"]["name"], r"^[a-zA-Z0-9_-]+$")
            self.assertTrue(spec["function"]["description"].strip())
            self.assertEqual(spec["function"]["parameters"]["type"], "object")


class BuildCoordinatorTests(unittest.TestCase):
    def test_missing_deepseek_key_raises_not_configured(self):
        from app.integrations.errors import IntegrationNotConfigured

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
            with self.assertRaises(IntegrationNotConfigured):
                build_coordinator()


class RealSkillWiringTests(unittest.IsolatedAsyncioTestCase):
    """AIP 真实技能处理器必须调用 real_order 的真实函数（薄封装）。"""

    async def test_tracking_find_by_no_calls_real_order(self):
        from app.aip.agents.tracking_aip import create_tracking_aip_service

        service = create_tracking_aip_service()
        captured = {}

        async def fake_find(work_order_no: str):
            captured["work_order_no"] = work_order_no
            return {"found": True, "work_order_id": "2", "work_order_no": work_order_no}

        with patch("app.services.real_order.find_work_order_by_no", side_effect=fake_find):
            handler = service._skill_handlers["tracking.find_real_by_no"]
            result = await handler({"work_order_no": "WO-2026-001"})

        self.assertEqual(captured["work_order_no"], "WO-2026-001")
        self.assertEqual(result["work_order_id"], "2")

    async def test_tracking_real_track_calls_real_order(self):
        from app.aip.agents.tracking_aip import create_tracking_aip_service

        service = create_tracking_aip_service()
        captured = {}

        async def fake_track(work_order_id: str):
            captured["work_order_id"] = work_order_id
            return {"work_order_id": work_order_id, "completion_rate": 90.0}

        with patch("app.services.real_order.track_order", side_effect=fake_track):
            handler = next(
                h for sid, h in service._skill_handlers.items()
                if sid == "tracking.track_real"
            )
            result = await handler({"work_order_id": "9"})

        self.assertEqual(captured["work_order_id"], "9")
        self.assertEqual(result["completion_rate"], 90.0)

    async def test_ship_gate_skill_passes_approval_flag(self):
        from app.aip.agents.tracking_aip import create_tracking_aip_service

        service = create_tracking_aip_service()
        captured = {}

        async def fake_gate(work_order_id: str, quotation_approved: bool = False):
            captured["approved"] = quotation_approved
            return {"can_ship": quotation_approved}

        with patch("app.services.real_order.ship_gate_check", side_effect=fake_gate):
            handler = next(
                h for sid, h in service._skill_handlers.items()
                if sid == "tracking.check_real_ship_gate"
            )
            await handler({"work_order_id": "9", "quotation_approved": True})

        self.assertTrue(captured["approved"])

    async def test_quotation_real_get_handles_missing(self):
        from app.aip.agents.quotation_aip import create_quotation_aip_service

        service = create_quotation_aip_service()
        handler = next(
            h for sid, h in service._skill_handlers.items()
            if sid == "quotation.get_real"
        )
        with patch("app.services.real_order.get_quotation", return_value=None):
            result = await handler({"quotation_id": "QUO-NOPE"})
        self.assertFalse(result["found"])


if __name__ == "__main__":
    unittest.main()
