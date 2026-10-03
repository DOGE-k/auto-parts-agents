from __future__ import annotations

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
        if not api_key.strip():
            raise IntegrationNotConfigured("DeepSeek", ["DEEPSEEK_API_KEY"])
        if not model.strip():
            raise ValueError("DeepSeek 模型名称不能为空")
        self.model = model.strip()
        self._http = JsonHttpClient(
            base_url,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Accept": "application/json"},
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
