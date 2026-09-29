from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentType(StrEnum):
    QUOTATION = "quotation"
    PROCUREMENT = "procurement"
    TRACKING = "tracking"
    QUALITY_DOCUMENT = "quality_document"


class OperationalState(StrEnum):
    READY = "READY"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    WAITING_EVENT = "WAITING_EVENT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RETRY = "RETRY"
    EXCEPTION = "EXCEPTION"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class QuotationBusinessState(StrEnum):
    RFQ_RECEIVED = "RFQ_RECEIVED"
    NEEDS_DATA = "NEEDS_DATA"
    CALCULATING = "CALCULATING"
    DRAFT_READY = "DRAFT_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    COST_ASSESSMENT_OPEN = "COST_ASSESSMENT_OPEN"
    CLOSED = "CLOSED"


class ProcurementBusinessState(StrEnum):
    DEMAND_RECEIVED = "DEMAND_RECEIVED"
    ANALYZING = "ANALYZING"
    PLAN_READY = "PLAN_READY"
    AWAITING_SELECTION = "AWAITING_SELECTION"
    PO_DRAFTED = "PO_DRAFTED"
    WAITING_SUPPLIER = "WAITING_SUPPLIER"
    PARTIAL_RECEIPT = "PARTIAL_RECEIPT"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class TrackingBusinessState(StrEnum):
    ORDER_NOT_RELEASED = "ORDER_NOT_RELEASED"
    MONITORING = "MONITORING"
    RISK_DETECTED = "RISK_DETECTED"
    ETA_RECALCULATING = "ETA_RECALCULATING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SHIPMENT_BLOCKED = "SHIPMENT_BLOCKED"
    READY_TO_SHIP = "READY_TO_SHIP"
    CLOSED = "CLOSED"


class QualityDocumentBusinessState(StrEnum):
    CHECKLIST_OPEN = "CHECKLIST_OPEN"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    INCOMPLETE = "INCOMPLETE"
    PACKAGE_DRAFT = "PACKAGE_DRAFT"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    APPROVED = "APPROVED"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class BusinessEventType(StrEnum):
    RFQ_CREATED = "RFQ_CREATED"
    QUOTE_NEEDS_INFO = "QUOTE_NEEDS_INFO"
    QUOTE_DRAFT_READY = "QUOTE_DRAFT_READY"
    QUOTE_APPROVED = "QUOTE_APPROVED"
    SALES_ORDER_RELEASED = "SALES_ORDER_RELEASED"
    MATERIAL_DEMAND_CREATED = "MATERIAL_DEMAND_CREATED"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    SUPPLY_PLAN_READY = "SUPPLY_PLAN_READY"
    COST_ASSESSMENT_READY = "COST_ASSESSMENT_READY"
    PO_DRAFT_READY = "PO_DRAFT_READY"
    SUPPLIER_DELAYED = "SUPPLIER_DELAYED"
    IQC_REQUESTED = "IQC_REQUESTED"
    QUALITY_HOLD = "QUALITY_HOLD"
    NCR_CREATED = "NCR_CREATED"
    QUALITY_RELEASED = "QUALITY_RELEASED"
    DOCUMENT_PACKAGE_APPROVED = "DOCUMENT_PACKAGE_APPROVED"
    DELIVERY_RISK = "DELIVERY_RISK"
    ETA_RECALCULATED = "ETA_RECALCULATED"
    EXPEDITE_REQUESTED = "EXPEDITE_REQUESTED"
    EXPEDITE_OPTIONS_READY = "EXPEDITE_OPTIONS_READY"
    SHIPMENT_APPROVAL_REQUIRED = "SHIPMENT_APPROVAL_REQUIRED"
    SHIPMENT_RELEASED = "SHIPMENT_RELEASED"
    DELIVERED = "DELIVERED"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Project(DomainModel):
    project_id: str
    correlation_id: str
    customer_id: str
    product_id: str
    current_version: int = Field(ge=1)
    lifecycle_state: str
    data_source: str = "mock"


class AgentCase(DomainModel):
    case_id: str
    project_id: str
    agent_type: AgentType
    operational_state: OperationalState = OperationalState.READY
    business_state: str
    version: int = Field(ge=1)


class BusinessEvent(DomainModel):
    event_id: str
    event_type: BusinessEventType
    schema_version: str = "1.0"
    project_id: str
    correlation_id: str
    causation_id: str | None = None
    source_agent: AgentType | None = None
    target_agent: AgentType | None = None
    object_type: str
    object_id: str
    object_version: int = Field(ge=1)
    occurred_at: datetime
    payload: dict[str, Any]
    evidence_ids: list[str] = Field(default_factory=list)
    idempotency_key: str


class AgentTask(DomainModel):
    task_id: str
    case_id: str
    capability_id: str
    protocol_role: Literal["leader", "partner"]
    input_snapshot_hash: str
    status: str
    aip_task_id: str | None = None


class Plan(DomainModel):
    plan_id: str
    action_type: str
    assumptions: list[str] = Field(default_factory=list)
    impact: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    required_approval: bool
    input_snapshot_hash: str


class Approval(DomainModel):
    approval_id: str
    action_type: str
    object_id: str
    object_version: int = Field(ge=1)
    snapshot_hash: str
    rule_version: str
    status: str
    approver: str | None = None


class Evidence(DomainModel):
    evidence_id: str
    source_system: str
    source_type: str
    source_id: str
    source_version: str
    content_hash: str
    uri: str | None = None


class AuditRecord(DomainModel):
    audit_id: str
    trace_id: str
    action: str
    actor: str
    input_hash: str
    output_hash: str
    result: str
    created_at: datetime


class CostAssessment(DomainModel):
    cost_assessment_id: str
    parent_order_id: str
    reason: str
    status: str


class AgentDescriptor(DomainModel):
    agent_type: AgentType
    agent_name: str
    capabilities: list[str]
    forbidden_actions: list[str]
    aic: str | None = None
    acs_version: str = "1.0.0"
    endpoint: str | None = None


AGENT_CATALOG: tuple[AgentDescriptor, ...] = (
    AgentDescriptor(
        agent_type=AgentType.QUOTATION,
        agent_name="quotation-agent",
        capabilities=[
            "quotation.extract_rfq",
            "quotation.validate_fields",
            "quotation.calculate_cost",
            "quotation.request_material_price",
            "quotation.request_capacity_eta",
            "quotation.create_draft",
            "quotation.create_cost_assessment",
        ],
        forbidden_actions=[
            "auto_change_price",
            "promise_customer_delivery",
            "publish_sales_order",
            "place_purchase_order",
            "change_quality_state",
        ],
    ),
    AgentDescriptor(
        agent_type=AgentType.PROCUREMENT,
        agent_name="procurement-agent",
        capabilities=[
            "procurement.calculate_net_requirement",
            "procurement.search_supplier",
            "procurement.compare_supply_plans",
            "procurement.create_po_draft",
            "procurement.read_supplier_status",
        ],
        forbidden_actions=[
            "chat_triggered_purchase",
            "automatic_supplier_selection",
            "automatic_purchase_order",
            "execute_iqc",
            "make_mrb_decision",
            "quality_release",
        ],
    ),
    AgentDescriptor(
        agent_type=AgentType.TRACKING,
        agent_name="tracking-agent",
        capabilities=[
            "tracking.calculate_eta",
            "tracking.read_milestones",
            "tracking.detect_risk",
            "tracking.request_procurement_plan",
            "tracking.request_cost_assessment",
            "tracking.check_ship_gate",
        ],
        forbidden_actions=[
            "change_customer_commitment",
            "schedule_overtime_or_subcontracting",
            "change_production_priority",
            "pause_or_cancel_order",
            "release_shipment",
            "bypass_ship_gates",
        ],
    ),
    AgentDescriptor(
        agent_type=AgentType.QUALITY_DOCUMENT,
        agent_name="quality-document-agent",
        capabilities=[
            "quality.build_checklist",
            "quality.collect_evidence",
            "quality.check_completeness",
            "quality.build_package_draft",
            "quality.archive_package",
        ],
        forbidden_actions=[
            "invent_inspection_data",
            "create_inspection_result",
            "confirm_root_cause",
            "make_mrb_decision",
            "publish_quality_hold",
            "publish_quality_release",
        ],
    ),
)


def ensure_cost_assessment_does_not_replace_quote(
    assessment: CostAssessment, accepted_quote_status: str
) -> None:
    """Protect an accepted quote when recording an order-period assessment."""
    if accepted_quote_status != "ACCEPTED":
        raise ValueError("订单期成本评估不得覆盖已接受报价的状态")
    if not assessment.cost_assessment_id or not assessment.parent_order_id:
        raise ValueError("订单期成本评估必须使用独立 ID 并关联原订单")
