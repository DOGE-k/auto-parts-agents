"""DeepSeek 客户端流式 chat completion 测试（harness 步骤 D）。

用 httpx.MockTransport 喂 OpenAI 兼容 SSE 分片，验证：
- content/reasoning_content 片段拼接、on_delta 增量回调顺序；
- tool_calls 分片按 index 拼装（name/arguments 跨片到达）；
- 最终返回与非流式 chat_completion 同构（choices[0].message）。
不触真实网络。
"""
from __future__ import annotations

import json
import unittest

import httpx

from app.integrations.deepseek import DeepSeekClient


def _sse(chunks: list[dict]) -> bytes:
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}" for c in chunks]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def _client_with(payload: bytes) -> DeepSeekClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"Content-Type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    injected = httpx.AsyncClient(transport=transport, base_url="https://api.deepseek.com/")
    return DeepSeekClient("test-key-123", client=injected)


class DeepSeekStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_and_reasoning_deltas_assemble(self):
        payload = _sse([
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": None, "reasoning_content": ""}}]},
            {"choices": [{"index": 0, "delta": {"reasoning_content": "先查订单。"}}]},
            {"choices": [{"index": 0, "delta": {"reasoning_content": "再查工单。"}}]},
            {"choices": [{"index": 0, "delta": {"content": "完成率 "}}]},
            {"choices": [{"index": 0, "delta": {"content": "90%。"}, "finish_reason": "stop"}]},
        ])
        client = _client_with(payload)
        deltas: list[tuple[str, str]] = []
        try:
            result = await client.chat_completion_stream(
                [{"role": "user", "content": "工单进度？"}],
                on_delta=lambda kind, frag: deltas.append((kind, frag)),
            )
        finally:
            await client.aclose()

        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "完成率 90%。")
        self.assertEqual(message["reasoning_content"], "先查订单。再查工单。")
        self.assertNotIn("tool_calls", message)
        self.assertEqual(
            deltas,
            [
                ("reasoning", "先查订单。"),
                ("reasoning", "再查工单。"),
                ("content", "完成率 "),
                ("content", "90%。"),
            ],
        )

    async def test_tool_call_fragments_assemble_by_index(self):
        payload = _sse([
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call-1", "type": "function", "function": {"name": "tracking__track", "arguments": ""}},
            ]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"work_order_'}},
            ]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'id": "7"}'}},
            ]}, "finish_reason": "tool_calls"}]},
        ])
        client = _client_with(payload)
        try:
            result = await client.chat_completion_stream(
                [{"role": "user", "content": "查工单"}],
                tools=[{"type": "function", "function": {"name": "tracking__track", "parameters": {"type": "object"}}}],
            )
        finally:
            await client.aclose()

        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], None)
        call = message["tool_calls"][0]
        self.assertEqual(call["id"], "call-1")
        self.assertEqual(call["function"]["name"], "tracking__track")
        self.assertEqual(call["function"]["arguments"], '{"work_order_id": "7"}')


if __name__ == "__main__":
    unittest.main()
