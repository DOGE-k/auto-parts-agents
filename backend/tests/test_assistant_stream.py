"""协调智能体流式问答（SSE）测试。

覆盖：
- BusinessCoordinator.ask 的 on_step 回调：每完成一次工具调用回调当前步骤（同步端点不传时行为不变）
- POST /api/real-orders/assistant/ask/stream：step 事件逐条推送 → done 事件携带完整结果；
  协调者关闭；content-type 为 text/event-stream
- 协调者抛 IntegrationError 时推送 error 事件（不是静默挂死）
- DEEPSEEK 未配置（build_coordinator 抛 IntegrationNotConfigured）时返回 503

全部使用隔离测试数据库（见 conftest.py）与假协调者，不触真实系统。
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from app.integrations.errors import IntegrationError, IntegrationNotConfigured
from app.services import coordinator as coord_module
from app.services.coordinator import BusinessCoordinator


class _FakeLLM:
    """按脚本依次返回 chat_completion 结果的假 DeepSeek 客户端。"""

    def __init__(self, script: list[dict]):
        self.script = script

    async def chat_completion(self, messages, *, tools=None, tool_choice="auto", max_tokens=2048):
        return self.script.pop(0)

    async def aclose(self) -> None:
        return None


class OnStepCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_step_receives_each_call_chain_entry(self):
        llm = _FakeLLM([
            {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "tracking__lookup_order_link", "arguments": '{"erp_order_id": "SAL-ORD-2026-00023"}'},
                }],
            }}]},
            {"choices": [{"message": {"role": "assistant", "content": "已完成。"}}]},
        ])

        async def fake_invoker(agent_type, skill_id, inputs):
            return {"found": "LINKED", "work_order_id": "9", "authority": "OpenMES"}

        coordinator = BusinessCoordinator(llm, skill_invoker=fake_invoker)
        steps: list[dict] = []
        try:
            result = await coordinator.ask(
                "SAL-ORD-2026-00023 什么时候能做完？", on_step=steps.append
            )
        finally:
            await coordinator.aclose()

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["seq"], 1)
        self.assertEqual(steps[0]["skill_id"], "tracking.lookup_order_link")
        self.assertEqual(steps[0]["status"], "ok")
        # 回调的步骤与最终 call_chain 一致（同一份数据）
        self.assertEqual(result["call_chain"], steps)

    async def test_ask_without_on_step_keeps_sync_behavior(self):
        llm = _FakeLLM([
            {"choices": [{"message": {"role": "assistant", "content": "直接回答。"}}]},
        ])
        coordinator = BusinessCoordinator(llm, skill_invoker=None)
        try:
            result = await coordinator.ask("你好")
        finally:
            await coordinator.aclose()
        self.assertEqual(result["answer"], "直接回答。")
        self.assertEqual(result["call_chain"], [])


class _FakeStreamCoordinator:
    """流式端点测试用假协调者：回调两步后返回固定结果。"""

    def __init__(self, steps: list[dict], result: dict, error: Exception | None = None):
        self._steps = steps
        self._result = result
        self._error = error
        self.closed = False

    async def ask(self, question, context, on_step=None):
        if self._error is not None:
            raise self._error
        if on_step is not None:
            for step in self._steps:
                on_step(step)
        return self._result

    async def aclose(self):
        self.closed = True


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        lines = [l for l in block.split("\n") if l and not l.startswith(":")]
        kind = ""
        data = ""
        for line in lines:
            if line.startswith("event: "):
                kind = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if kind:
            events.append((kind, json.loads(data)))
    return events


class AssistantStreamEndpointTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        from app.main import app

        return app

    async def _collect(self, payload: dict) -> tuple[int, str, httpx.Response]:
        transport = httpx.ASGITransport(app=self._app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("POST", "/api/real-orders/assistant/ask/stream", json=payload) as resp:
                raw = ""
                async for chunk in resp.aiter_bytes():
                    raw += chunk.decode("utf-8")
                return resp.status_code, raw, resp

    async def test_stream_emits_steps_then_done(self):
        steps = [
            {"seq": 1, "callee": "tracking", "skill_id": "tracking.lookup_order_link", "status": "ok", "elapsed_ms": 12},
            {"seq": 2, "callee": "tracking", "skill_id": "tracking.track_real", "status": "ok", "elapsed_ms": 34},
        ]
        fake = _FakeStreamCoordinator(steps, {
            "question": "测试问题",
            "answer": "完成率 90%。",
            "call_chain": steps,
            "rounds": 2,
            "tool_count": 2,
            "authority": "ERPNext + OpenMES",
            "coordination_run_id": "RUN-COORD-TEST",
        })
        with patch.object(coord_module, "build_coordinator", return_value=fake):
            status, raw, resp = await self._collect({"question": "测试问题"})

        self.assertEqual(status, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        events = _parse_sse(raw)
        self.assertEqual([kind for kind, _ in events], ["step", "step", "done"])
        self.assertEqual(events[0][1]["seq"], 1)
        self.assertEqual(events[2][1]["answer"], "完成率 90%。")
        self.assertEqual(events[2][1]["coordination_run_id"], "RUN-COORD-TEST")
        # 协调者已正常关闭
        self.assertTrue(fake.closed)

    async def test_stream_emits_error_event_when_coordinator_fails(self):
        fake = _FakeStreamCoordinator([], {}, error=IntegrationError("OpenMES 不可达"))
        with patch.object(coord_module, "build_coordinator", return_value=fake):
            status, raw, _ = await self._collect({"question": "测试问题"})

        self.assertEqual(status, 200)
        events = _parse_sse(raw)
        self.assertEqual([kind for kind, _ in events], ["error"])
        self.assertIn("OpenMES 不可达", events[0][1]["message"])

    async def test_stream_returns_503_when_llm_not_configured(self):
        with patch.object(
            coord_module,
            "build_coordinator",
            side_effect=IntegrationNotConfigured("协调智能体（DeepSeek LLM）", ["DEEPSEEK_API_KEY"]),
        ):
            transport = httpx.ASGITransport(app=self._app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/real-orders/assistant/ask/stream", json={"question": "测试"}
                )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["code"], "llm_not_configured")

    async def test_stream_rejects_empty_question(self):
        transport = httpx.ASGITransport(app=self._app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/real-orders/assistant/ask/stream", json={"question": "   "})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
