from app.agents.base import BaseAgent
from app.domain.models import AgentType


class QualityDocumentAgent(BaseAgent):
    agent_type = AgentType.QUALITY_DOCUMENT
    tool_allowlist = frozenset(
        {
            "quality.build_checklist",
            "quality.collect_evidence",
            "quality.check_completeness",
            "quality.build_package_draft",
            "quality.archive_package",
        }
    )
    forbidden_actions = frozenset(
        {"invent_inspection_data", "create_inspection_result", "confirm_root_cause", "make_mrb_decision", "publish_quality_hold", "publish_quality_release"}
    )
    event_capabilities = {
        "QUALITY_RELEASED": "quality.collect_evidence",
        "QUALITY_HOLD": "quality.collect_evidence",
        "NCR_CREATED": "quality.collect_evidence",
    }
    approval_capabilities = frozenset({"quality.build_package_draft", "quality.archive_package"})
