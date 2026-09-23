from app.agents.base import BaseAgent
from app.domain.models import AgentType


class QuotationAgent(BaseAgent):
    agent_type = AgentType.QUOTATION
    tool_allowlist = frozenset(
        {
            "quotation.extract_rfq",
            "quotation.validate_fields",
            "quotation.calculate_cost",
            "quotation.request_material_price",
            "quotation.request_capacity_eta",
            "quotation.create_draft",
            "quotation.create_cost_assessment",
        }
    )
    forbidden_actions = frozenset(
        {"auto_change_price", "promise_customer_delivery", "publish_sales_order", "place_purchase_order", "change_quality_state"}
    )
    event_capabilities = {
        "RFQ_CREATED": "quotation.extract_rfq",
        "MATERIAL_SHORTAGE": "quotation.create_cost_assessment",
    }
    approval_capabilities = frozenset({"quotation.create_draft"})
