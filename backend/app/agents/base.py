from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.models import AgentType, BusinessEvent


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str


@dataclass(frozen=True)
class AgentDecision:
    accepted: bool
    capability_id: str | None
    requires_approval: bool
    reason: str


class BaseAgent:
    agent_type: AgentType
    tool_allowlist: frozenset[str]
    forbidden_actions: frozenset[str]
    event_capabilities: dict[str, str] = {}
    approval_capabilities: frozenset[str] = frozenset()

    def assert_capability(self, capability_id: str) -> None:
        if capability_id not in self.tool_allowlist:
            raise PermissionError(f"{self.agent_type.value} Agent 不允许调用 {capability_id}")

    async def consume_event(self, event: BusinessEvent, snapshot: dict[str, Any]) -> AgentDecision:
        validation = await self.validate(event, snapshot)
        if not validation.valid:
            return AgentDecision(False, None, False, validation.reason)
        capability_id = self.event_capabilities.get(event.event_type.value)
        if not capability_id:
            return AgentDecision(False, None, False, "该 Agent 未订阅此事件类型")
        self.assert_capability(capability_id)
        return AgentDecision(
            True,
            capability_id,
            capability_id in self.approval_capabilities,
            "事件已通过范围校验，可进入确定性计划阶段",
        )

    async def load_context(self, project_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        if snapshot.get("project_id") != project_id:
            raise ValueError("项目快照与请求项目不匹配")
        return {"project_id": project_id, "snapshot": snapshot, "agent_type": self.agent_type.value}

    async def validate(self, event: BusinessEvent, context: dict[str, Any]) -> ValidationResult:
        if event.project_id != context.get("project_id"):
            return ValidationResult(False, "事件项目作用域与上下文不匹配")
        if event.target_agent is not None and event.target_agent != self.agent_type:
            return ValidationResult(False, "事件目标 Agent 与当前 Agent 不匹配")
        return ValidationResult(True, "项目作用域与接收目标有效")

    async def plan(self, event: BusinessEvent, context: dict[str, Any]) -> AgentDecision:
        return await self.consume_event(event, context["snapshot"])

    async def request_approval(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {"agent_type": self.agent_type.value, "plan": plan, "status": "pending"}

    async def execute(self, capability_id: str, inputs: dict[str, Any], registry) -> dict[str, Any]:
        self.assert_capability(capability_id)
        return registry.invoke(self.agent_type, capability_id, inputs)

    async def publish(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        # Facts are published only after the runtime's adapter read-back step.
        return []
