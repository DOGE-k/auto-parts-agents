from __future__ import annotations

import json
from typing import Any

from app.integrations.errors import IntegrationNotConfigured
from app.integrations.http import JsonHttpClient


class DeepSeekClient:
    """OpenAI-compatible DeepSeek Chat Completions transport."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
        timeout_seconds: float = 45.0,
        client=None,
    ) -> None:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise IntegrationNotConfigured("DeepSeek", ["DEEPSEEK_API_KEY"])
        if not normalized_key.isascii():
            raise ValueError("DEEPSEEK_API_KEY 包含非 ASCII 字符；请检查服务器上的密钥配置")
        if not model.strip():
            raise ValueError("DeepSeek 模型名称不能为空")
        self.model = model.strip()
        self._http = JsonHttpClient(
            base_url,
            headers={"Authorization": f"Bearer {normalized_key}", "Accept": "application/json"},
            timeout_seconds=timeout_seconds,
            client=client,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        if not messages:
            raise ValueError("DeepSeek 请求必须包含至少一条消息")
        if not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens 必须在 1-8192 之间")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        result = await self._http.request_json(
            "POST", "chat/completions", json_body=payload
        )
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise ValueError("DeepSeek Chat Completions 返回格式无效")
        choice = result["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("DeepSeek 未返回有效 assistant 消息")
        return result

    async def chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        max_tokens: int = 2048,
        on_delta=None,
    ) -> dict[str, Any]:
        """流式 chat completion：逐 delta 到达即回调，返回与非流式同构的结果。

        on_delta(kind, fragment)：kind ∈ {"reasoning", "content"}；fragment 为该次
        增量文本（打字机效果用）。tool_calls 分片按 index 在本地拼装，最终
        message 结构与非流式完全一致（含 reasoning_content），调用方按既有
        约定处理（展示透传 / 回传前剥离）。
        """
        if not messages:
            raise ValueError("DeepSeek 请求必须包含至少一条消息")
        if not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens 必须在 1-8192 之间")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        finish_reason = ""

        async for data in self._http.stream_sse("chat/completions", json_body=payload):
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
                if on_delta is not None:
                    on_delta("reasoning", reasoning)
            text = delta.get("content")
            if isinstance(text, str) and text:
                content_parts.append(text)
                if on_delta is not None:
                    on_delta("content", text)
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index") or 0)
                slot = tool_calls_by_index.setdefault(
                    index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if call.get("id"):
                    slot["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    slot["function"]["name"] += function["name"]
                if function.get("arguments"):
                    slot["function"]["arguments"] += function["arguments"]
            if choices[0].get("finish_reason"):
                finish_reason = choices[0]["finish_reason"]

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls_by_index:
            message["tool_calls"] = [
                tool_calls_by_index[i] for i in sorted(tool_calls_by_index)
            ]
        return {
            "id": "stream",
            "model": self.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason or "stop"}],
        }
