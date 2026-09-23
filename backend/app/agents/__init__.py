"""The four independently identified business agents."""

from app.domain.models import AgentType
from app.agents.quotation.agent import QuotationAgent
from app.agents.procurement.agent import ProcurementAgent
from app.agents.tracking.agent import TrackingAgent
from app.agents.quality_document.agent import QualityDocumentAgent

AGENTS = {
    AgentType.QUOTATION: QuotationAgent(),
    AgentType.PROCUREMENT: ProcurementAgent(),
    AgentType.TRACKING: TrackingAgent(),
    AgentType.QUALITY_DOCUMENT: QualityDocumentAgent(),
}
