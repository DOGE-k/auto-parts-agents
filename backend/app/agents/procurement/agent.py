from app.agents.base import BaseAgent
from app.domain.models import AgentType


class ProcurementAgent(BaseAgent):
    agent_type = AgentType.PROCUREMENT
    tool_allowlist = frozenset(
        {
            "procurement.calculate_net_requirement",
            "procurement.search_supplier",
            "procurement.compare_supply_plans",
            "procurement.create_po_draft",
            "procurement.read_supplier_status",
        }
    )
    forbidden_actions = frozenset(
        {"chat_triggered_purchase", "automatic_supplier_selection", "automatic_purchase_order", "execute_iqc", "make_mrb_decision", "quality_release"}
    )
    event_capabilities = {
        "MATERIAL_DEMAND_CREATED": "procurement.calculate_net_requirement",
        "MATERIAL_SHORTAGE": "procurement.compare_supply_plans",
    }
    approval_capabilities = frozenset({"procurement.create_po_draft"})
