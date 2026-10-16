"""Public AIP Leader: request-scoped coordination through authenticated Partners."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Callable

from app.aip.aip_agent_client import AipAgentClient
from app.aip.aip_agent_service import AipAgentService
from app.aip.natural_language import question_handler, real_skill_ids


PARTNER_ROLES = ("quotation", "procurement", "tracking", "quality-document")


def create_leader_aip_service(
    aic: str,
    *,
    outbound_clients: Mapping[str, AipAgentClient],
    coordinator_factory: Callable | None = None,
) -> AipAgentService:
    """Build a Leader using caller-provided URL, mTLS context and peer AICs.

    Clients belong to the runtime and must be closed by its shutdown lifecycle.
    They contain no shared business conversation state. Coordinators are closed
    after every question, including failure or cancellation.
    """
    missing = set(PARTNER_ROLES).difference(outbound_clients)
    if missing:
        raise ValueError(f"Leader 缺少 Partner 客户端配置: {', '.join(sorted(missing))}")
    service = AipAgentService(
        agent_name="汽车零部件协同智能体", agent_type="leader", aic=aic,
    )
    allowed_by_role = {role: real_skill_ids(role) for role in PARTNER_ROLES}
    allowed = frozenset().union(*allowed_by_role.values())

    def invoker_factory(namespace):
        async def invoke(role, skill_id, inputs):
            if skill_id not in allowed_by_role.get(role, ()):
                raise PermissionError("协同问答只能调用目录中的真实只读技能")
            return await outbound_clients[role].invoke_skill(
                skill_id, inputs, session_id=namespace, timeout=60.0,
            )

        return invoke

    service.set_natural_language_handler(
        question_handler(
            service,
            invoker_factory=invoker_factory,
            allowed_skill_ids=allowed,
            coordinator_factory=coordinator_factory,
        ),
        skill_id="leader.ask",
        skill_name="汽车零部件协同问答",
    )
    return service
