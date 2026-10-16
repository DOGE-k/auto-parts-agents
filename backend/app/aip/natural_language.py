"""Request-scoped natural-language routing over the real read-only skill catalog.

This gateway does not create project sessions or accept approval credentials.
The AIP service supplies the original command after transport identity checks.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from acps_sdk.aip.aip_base_model import TaskCommand

from app.aip.aip_agent_service import AipAgentService
from app.services.capability_catalog import declared_only_catalog
from app.services.coordinator import build_coordinator


def request_namespace(aic: str, command: TaskCommand) -> str:
    """Avoid shared raw session IDs across callers, roles and claimed users.

    userId is only a partition label, never a project login or approver identity.
    Without a session, use the command ID rather than a shared default session.
    """
    partition = {
        "service": aic,
        "caller": command.senderId,
        "session": command.sessionId or command.id,
        "group": command.groupId,
        "user_partition": (command.commandParams or {}).get("userId"),
    }
    digest = hashlib.sha256(
        json.dumps(partition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"aip-{digest}"


def real_skill_ids(role: str) -> frozenset[str]:
    """Return validated local declarations; never mutate the shared catalog."""
    return frozenset(
        entry["skill_id"] for entry in declared_only_catalog()["entries"]
        if entry["aip_agent"] == role
    )


def question_handler(
    service: AipAgentService,
    *,
    invoker_factory: Callable,
    allowed_skill_ids: frozenset[str],
    coordinator_factory: Callable | None = None,
):
    """One coordinator per question, with no cross-caller conversation cache."""
    async def handle(text: str, command: TaskCommand) -> dict:
        question = text.strip()
        if not question:
            raise ValueError("问题不能为空")
        namespace = request_namespace(service.aic, command)
        factory = coordinator_factory or build_coordinator
        coordinator = factory(
            skill_invoker=invoker_factory(namespace),
            allowed_skill_ids=set(allowed_skill_ids),
        )
        try:
            return await coordinator.ask(
                question,
                run_meta={"session_id": namespace, "business_task_id": command.taskId},
            )
        finally:
            await coordinator.aclose()

    return handle


def attach_natural_language(
    service: AipAgentService,
    *,
    role: str | None = None,
    coordinator_factory: Callable | None = None,
) -> AipAgentService:
    """Enable plain questions on one Partner without exposing another role.

    Keep existing structured skill calls. Only the validated real skills already
    registered on this service may be selected by its question interpreter.
    """
    role = role or service.agent_type.replace("_", "-")
    if role == "quality":
        role = "quality-document"
    allowed = real_skill_ids(role).intersection(service.registered_skill_ids())
    if not allowed:
        raise ValueError(f"智能体 {role} 没有已注册的真实只读技能")
    service.restrict_skills(set(allowed))
    handlers = {skill_id: service._skill_handlers[skill_id] for skill_id in allowed}

    def invoker_factory(_namespace):
        async def invoke(agent_type, skill_id, inputs):
            if agent_type != role or skill_id not in handlers:
                raise PermissionError("该问题只能使用当前岗位的真实只读技能")
            if not isinstance(inputs, dict):
                raise ValueError("技能输入必须是 JSON 对象")
            return await handlers[skill_id](inputs)

        return invoke

    service.set_natural_language_handler(question_handler(
        service,
        invoker_factory=invoker_factory,
        allowed_skill_ids=frozenset(allowed),
        coordinator_factory=coordinator_factory,
    ))
    return service
