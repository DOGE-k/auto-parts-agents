from app.agents.base import BaseAgent
from app.domain.models import AgentType


class TrackingAgent(BaseAgent):
    agent_type = AgentType.TRACKING
    tool_allowlist = frozenset(
        {
            "tracking.calculate_eta",
            "tracking.read_milestones",
            "tracking.detect_risk",
            "tracking.request_procurement_plan",
            "tracking.request_cost_assessment",
            "tracking.check_ship_gate",
        }
    )
    forbidden_actions = frozenset(
        {"change_customer_commitment", "schedule_overtime_or_subcontracting", "change_production_priority", "pause_or_cancel_order", "release_shipment", "bypass_ship_gates"}
    )
    event_capabilities = {
        "SALES_ORDER_RELEASED": "tracking.read_milestones",
        "SUPPLIER_DELAYED": "tracking.detect_risk",
        "QUALITY_HOLD": "tracking.detect_risk",
        "DELIVERY_RISK": "tracking.detect_risk",
    }
    approval_capabilities = frozenset({"tracking.check_ship_gate"})
