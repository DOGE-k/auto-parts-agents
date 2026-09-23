from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.adapters.erp.mock import MockERP
from app.adapters.mes.mock import MockMES
from app.agents import AGENTS
from app.domain.models import (
    AgentType,
    BusinessEventType,
    CostAssessment,
    OperationalState,
    ensure_cost_assessment_does_not_replace_quote,
)
from app.persistence.models import (
    AgentCaseRow,
    AgentTaskRow,
    ApprovalRow,
    AuditRecordRow,
    BusinessEventRow,
    EvidenceRow,
    IdempotencyRow,
    InboxRow,
    OutboxRow,
    PlanRow,
    ProjectRow,
    ReplayRunRow,
    ToolCallRow,
)
from app.runtime.identifiers import canonical_hash, stable_id
from app.runtime.state_machine import transition
from app.tools.demo_rules import RULE_VERSION
from app.tools.registry import tool_registry


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = {
    "normal_order": REPO_ROOT / "scenarios" / "normal_order" / "fixture.json",
    "material_shortage": REPO_ROOT / "scenarios" / "material_shortage" / "fixture.json",
}
SCENARIO_SEED = 20260923
MOCK_ERP = MockERP()
MOCK_MES = MockMES()


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES.get(name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"未知演示场景：{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _project(session: Session, project_id: str) -> ProjectRow:
    row = session.get(ProjectRow, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return row


def _snapshot_update(project: ProjectRow, **updates: Any) -> None:
    project.snapshot_json = {**project.snapshot_json, **updates}


def _new_case(
    session: Session,
    project_id: str,
    agent_type: AgentType,
    business_state: str,
    objective: str,
) -> AgentCaseRow:
    case_id = f"CASE-{project_id}-{agent_type.value}"
    row = session.get(AgentCaseRow, case_id)
    if row:
        return row
    row = AgentCaseRow(
        case_id=case_id,
        project_id=project_id,
        agent_type=agent_type.value,
        operational_state=OperationalState.READY.value,
        business_state=business_state,
        version=1,
        objective=objective,
    )
    session.add(row)
    session.flush()
    return row


def _set_case(
    case: AgentCaseRow,
    operational_state: OperationalState | None = None,
    business_state: str | None = None,
) -> None:
    if operational_state and case.operational_state != operational_state.value:
        current = OperationalState(case.operational_state)
        transition(current, operational_state)
        case.operational_state = operational_state.value
        case.version += 1
    if business_state and case.business_state != business_state:
        case.business_state = business_state
        case.version += 1


def _append_event(
    session: Session,
    project: ProjectRow,
    event_type: BusinessEventType,
    *,
    object_type: str,
    object_id: str,
    payload: dict[str, Any],
    source_agent: AgentType | None = None,
    target_agent: AgentType | None = None,
    evidence_ids: list[str] | None = None,
    causation_id: str | None = None,
) -> BusinessEventRow:
    existing_events = session.scalars(
        select(BusinessEventRow).where(BusinessEventRow.project_id == project.project_id)
    ).all()
    sequence = len(existing_events) + 1
    event_id = f"EVT-{project.project_id}-{sequence:03d}"
    idempotency_key = f"{project.project_id}:{sequence}:{event_type.value}:{object_id}"
    existing = session.scalar(select(BusinessEventRow).where(BusinessEventRow.idempotency_key == idempotency_key))
    if existing:
        return existing

    project.current_version += 1
    _snapshot_update(project, current_version=project.current_version)
    row = BusinessEventRow(
        event_id=event_id,
        event_type=event_type.value,
        project_id=project.project_id,
        correlation_id=project.correlation_id,
        causation_id=causation_id,
        source_agent=source_agent.value if source_agent else None,
        target_agent=target_agent.value if target_agent else None,
        object_type=object_type,
        object_id=object_id,
        object_version=project.current_version,
        occurred_at=_utc_now(),
        payload_json=payload,
        evidence_ids_json=evidence_ids or [],
        idempotency_key=idempotency_key,
    )
    session.add(row)
    session.flush()
    session.add(
        InboxRow(event_id=event_id, consumer="local-runtime", status="completed", completed_at=_utc_now())
    )
    session.add(
        OutboxRow(
            outbox_id=f"OUT-{event_id}",
            event_id=event_id,
            topic=event_type.value,
            payload_json={"event_id": event_id, "event_type": event_type.value, "project_id": project.project_id},
        )
    )
    return row


def _record_audit(
    session: Session,
    project: ProjectRow,
    action: str,
    inputs: Any,
    output: Any,
    *,
    result: str = "success",
    actor: str = "local-demo-runtime",
    details: dict[str, Any] | None = None,
) -> None:
    existing = session.scalars(
        select(AuditRecordRow).where(AuditRecordRow.project_id == project.project_id)
    ).all()
    trace_id = project.snapshot_json["trace_id"]
    session.add(
        AuditRecordRow(
            audit_id=f"AUD-{project.project_id}-{len(existing) + 1:03d}",
            project_id=project.project_id,
            trace_id=trace_id,
            correlation_id=project.correlation_id,
            actor=actor,
            action=action,
            input_hash=canonical_hash(inputs),
            output_hash=canonical_hash(output),
            result=result,
            details_json=details or {},
        )
    )


def _create_plan(
    session: Session,
    project: ProjectRow,
    case: AgentCaseRow,
    action_type: str,
    details: dict[str, Any],
    *,
    assumptions: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    requires_approval: bool,
) -> PlanRow:
    plan_id = f"PLAN-{project.project_id}-{action_type.replace('.', '-').replace('_', '-')}-{case.version}"
    row = PlanRow(
        plan_id=plan_id,
        case_id=case.case_id,
        action_type=action_type,
        input_hash=canonical_hash(details),
        assumptions_json=assumptions or [],
        evidence_json=evidence_ids or [],
        requires_approval=requires_approval,
        details_json=details,
    )
    session.add(row)
    session.flush()
    return row


def _create_approval(
    session: Session,
    project: ProjectRow,
    case: AgentCaseRow,
    action_type: str,
    object_id: str,
    plan: PlanRow,
    action_payload: dict[str, Any],
) -> ApprovalRow:
    _set_case(case, OperationalState.AWAITING_APPROVAL)
    approval_id = f"APR-{project.project_id}-{action_type.replace('_', '-')}-{case.version}"
    snapshot_hash = canonical_hash(
        {"input_hash": plan.input_hash, "object_version": case.version, "rule_version": RULE_VERSION}
    )
    row = ApprovalRow(
        approval_id=approval_id,
        project_id=project.project_id,
        case_id=case.case_id,
        action_type=action_type,
        object_id=object_id,
        object_version=case.version,
        snapshot_hash=snapshot_hash,
        rule_version=RULE_VERSION,
        status="pending",
        action_payload_json={**action_payload, "plan_id": plan.plan_id, "input_hash": plan.input_hash},
    )
    session.add(row)
    session.flush()
    return row


def _create_tool_call(
    session: Session,
    project: ProjectRow,
    output: dict[str, Any],
    tool_id: str,
    plan_id: str | None,
) -> None:
    # Tool IDs are project-scoped so repeated fixture data in separate runs cannot collide.
    tool_call_id = f"{project.project_id}-{output['tool_call_id']}"
    output = {**output, "tool_call_id": tool_call_id}
    output["output_hash"] = canonical_hash({key: value for key, value in output.items() if key != "output_hash"})
    session.add(
        ToolCallRow(
            tool_call_id=tool_call_id,
            project_id=project.project_id,
            plan_id=plan_id,
            tool_id=tool_id,
            version=RULE_VERSION,
            input_hash=output["input_hash"],
            output_hash=output["output_hash"],
            status="success",
            latency_ms=0,
            output_json=output,
        )
    )


def _add_evidence(
    session: Session, project: ProjectRow, evidence_id: str, system: str, source_type: str
) -> None:
    if session.get(EvidenceRow, evidence_id):
        return
    session.add(
        EvidenceRow(
            evidence_id=evidence_id,
            project_id=project.project_id,
            source_system=system,
            source_type=source_type,
            source_id=evidence_id,
            source_version="synthetic-demo-v1",
            content_hash=canonical_hash({"evidence_id": evidence_id, "source": system}),
            uri=None,
        )
    )


def _add_agent_task(
    session: Session,
    project: ProjectRow,
    case: AgentCaseRow,
    capability_id: str,
    role: str,
    details: Any,
    depth: int = 0,
) -> None:
    AGENTS[AgentType(case.agent_type)].assert_capability(capability_id)
    task_id = stable_id("TASK", f"{case.case_id}:{capability_id}:{role}:{case.version}")
    session.add(
        AgentTaskRow(
            task_id=task_id,
            case_id=case.case_id,
            capability_id=capability_id,
            protocol_role=role,
            input_snapshot_hash=canonical_hash(details),
            status="completed",
            depth=depth,
            trace_id=project.snapshot_json["trace_id"],
        )
    )


def _base_project(
    session: Session,
    scenario: str,
    fixture: dict[str, Any],
    seed: int,
    idempotency_key: str,
    forced_project_id: str | None = None,
) -> ProjectRow:
    project_id = forced_project_id or stable_id(
        "PRJ", f"{scenario}:{seed}:{idempotency_key}", length=10
    )
    row = ProjectRow(
        project_id=project_id,
        correlation_id=f"CORR-{project_id}",
        customer_id=fixture["project"]["customer_id"],
        product_id=fixture["project"]["product_id"],
        current_version=1,
        lifecycle_state="DEMO_RUNNING",
        data_source="synthetic_demo_only",
        scenario=scenario,
        snapshot_json={
            "project_id": project_id,
            "correlation_id": f"CORR-{project_id}",
            "customer_id": fixture["project"]["customer_id"],
            "product_id": fixture["project"]["product_id"],
            "product_name": fixture["project"].get("product_name"),
            "current_version": 1,
            "lifecycle_state": "DEMO_RUNNING",
            "data_source": "synthetic_demo_only",
            "scenario": scenario,
            "fixture_id": fixture["fixture_id"],
            "trace_id": f"TRACE-{project_id}",
            "demo_notice": fixture["notice"],
            "collaboration_rounds": [],
            "milestones": [],
            "gates": {"quality_released": False, "document_package_approved": False},
        },
    )
    session.add(row)
    session.flush()
    return row


def _run_normal_order(
    session: Session, project: ProjectRow, fixture: dict[str, Any], seed: int
) -> dict[str, Any]:
    quote_inputs = {"currency": fixture["project"]["currency"], **fixture["project"], **fixture["quotation"]}
    quote = tool_registry.invoke(AgentType.QUOTATION, "quotation.calculate_cost", quote_inputs)
    eta = tool_registry.invoke(AgentType.TRACKING, "tracking.calculate_eta", fixture["delivery"])
    quote_draft = tool_registry.invoke(
        AgentType.QUOTATION,
        "quotation.create_draft",
        {"quote": quote, "eta": eta, "fixture_id": fixture["fixture_id"], "status": "DRAFT"},
    )
    quote_case = _new_case(
        session,
        project.project_id,
        AgentType.QUOTATION,
        "RFQ_RECEIVED",
        "根据演示 RFQ、BOM 和固定成本规则形成报价草稿。",
    )
    _set_case(quote_case, OperationalState.VALIDATING, "CALCULATING")
    _set_case(quote_case, OperationalState.EXECUTING)
    plan = _create_plan(
        session,
        project,
        quote_case,
        "quotation.create_draft",
        quote_draft,
        assumptions=["价格、成本和交期均来自标注为合成数据的场景文件。"],
        evidence_ids=["ev-mock-erp-bom", "ev-mock-erp-item-price", "ev-mock-mes-capacity"],
        requires_approval=True,
    )
    _create_tool_call(session, project, quote, "quotation.calculate_cost", plan.plan_id)
    _create_tool_call(session, project, eta, "tracking.calculate_eta", plan.plan_id)
    _create_tool_call(session, project, quote_draft, "quotation.create_draft", plan.plan_id)
    for evidence_id, system, source_type in (
        ("ev-mock-erp-bom", "MockERP", "BOM"),
        ("ev-mock-erp-item-price", "MockERP", "Item Price"),
        ("ev-mock-mes-capacity", "MockMES", "Capacity"),
    ):
        _add_evidence(session, project, evidence_id, system, source_type)

    rfq = _append_event(
        session,
        project,
        BusinessEventType.RFQ_CREATED,
        object_type="rfq",
        object_id=f"RFQ-{project.project_id}",
        payload={"quantity": fixture["project"]["order_quantity"], "product_id": project.product_id},
    )
    draft = _append_event(
        session,
        project,
        BusinessEventType.QUOTE_DRAFT_READY,
        object_type="quotation",
        object_id=f"QUOTE-{project.project_id}",
        payload=quote_draft,
        source_agent=AgentType.QUOTATION,
        evidence_ids=plan.evidence_json,
        causation_id=rfq.event_id,
    )
    _add_agent_task(
        session,
        project,
        quote_case,
        "quotation.extract_rfq",
        "leader",
        {"rfq": rfq.event_id, "project_id": project.project_id},
    )
    _add_agent_task(
        session,
        project,
        quote_case,
        "quotation.create_draft",
        "leader",
        {"rfq": rfq.event_id, "quote": quote},
    )
    _set_case(quote_case, OperationalState.AWAITING_APPROVAL, "AWAITING_APPROVAL")
    approval = _create_approval(
        session,
        project,
        quote_case,
        "quote_approval",
        f"QUOTE-{project.project_id}",
        plan,
        {"next_action": "release_sales_order", "draft_event_id": draft.event_id},
    )
    _snapshot_update(
        project,
        quote=quote,
        eta=eta,
        milestones=[{"name": "报价草稿", "status": "等待审批"}],
        normal_fixture=fixture,
    )
    return {"project_id": project.project_id, "approval_id": approval.approval_id}


def _run_shortage(
    session: Session, project: ProjectRow, fixture: dict[str, Any], seed: int
) -> dict[str, Any]:
    material = fixture["material_demand"]
    net = tool_registry.invoke(AgentType.PROCUREMENT, "procurement.calculate_net_requirement", material)
    required_date = date_from_string(material["required_date"])
    option_results: list[dict[str, Any]] = []
    shortage = Decimal(net["net_requirement"])
    cost_assessment_id = f"CA-{project.project_id}"
    cost_assessment = tool_registry.invoke(
        AgentType.QUOTATION,
        "quotation.create_cost_assessment",
        {
            "cost_assessment_id": cost_assessment_id,
            "parent_order_id": f"SO-{project.project_id}",
            "accepted_quote_id": fixture["accepted_quote_reference"]["quote_id"],
            "accepted_quote_status": fixture["accepted_quote_reference"]["status"],
            "baseline_unit_price": fixture["accepted_quote_reference"]["baseline_material_unit_price"],
            "shortage_quantity": str(shortage),
            "options": fixture["supplier_options"],
        },
    )
    deltas = {item["option_id"]: item for item in cost_assessment["option_deltas"]}
    for option in fixture["supplier_options"]:
        eta = tool_registry.invoke(
            AgentType.TRACKING,
            "tracking.calculate_eta",
            {
                "material_available_date": option["confirmed_delivery_date"],
                "capacity_completion_date": "2026-10-02",
                "quality_wait_days": 2,
                "buffer_days": 3,
            }
        )
        option_results.append(
            {
                **option,
                "eta": eta,
                "cost_assessment": {
                    "baseline_material_unit_price": fixture["accepted_quote_reference"]["baseline_material_unit_price"],
                    "option_material_unit_price": option["unit_price"],
                    "delta_for_shortage": deltas[option["option_id"]]["delta_for_shortage"],
                    "status": "READY",
                    "accepted_quote_status": fixture["accepted_quote_reference"]["status"],
                },
                "meets_material_required_date": date_from_string(option["confirmed_delivery_date"]) <= required_date,
            }
        )

    quotation_case = _new_case(
        session,
        project.project_id,
        AgentType.QUOTATION,
        "COST_ASSESSMENT_OPEN",
        "独立评估订单执行期缺料方案的成本影响，不改写已接受报价。",
    )
    _set_case(quotation_case, OperationalState.VALIDATING, "COST_ASSESSMENT_OPEN")
    _set_case(quotation_case, OperationalState.EXECUTING)
    cost_assessment_plan = _create_plan(
        session,
        project,
        quotation_case,
        "quotation.create_cost_assessment",
        {
            "cost_assessment_id": cost_assessment_id,
            "parent_order_id": f"SO-{project.project_id}",
            "accepted_quote_id": fixture["accepted_quote_reference"]["quote_id"],
            "accepted_quote_status": fixture["accepted_quote_reference"]["status"],
            "assessment": cost_assessment,
        },
        assumptions=["此订单期评估独立于已接受报价，不修改原报价金额或状态。"],
        evidence_ids=["ev-mock-erp-demand", "ev-mock-erp-supplier-quotes"],
        requires_approval=False,
    )
    _set_case(quotation_case, OperationalState.WAITING_EVENT)
    _create_tool_call(session, project, cost_assessment, "quotation.create_cost_assessment", cost_assessment_plan.plan_id)

    tracking = _new_case(
        session,
        project.project_id,
        AgentType.TRACKING,
        "RISK_DETECTED",
        "监视已发布订单，在收到权威需求和库存后识别缺料。",
    )
    procurement = _new_case(
        session,
        project.project_id,
        AgentType.PROCUREMENT,
        "DEMAND_RECEIVED",
        "只处理 MockERP 正式发布的物料需求并比较供应方案。",
    )
    _set_case(tracking, OperationalState.VALIDATING, "RISK_DETECTED")
    _set_case(tracking, OperationalState.EXECUTING)
    _set_case(procurement, OperationalState.VALIDATING, "ANALYZING")
    _set_case(procurement, OperationalState.EXECUTING)
    comparison = tool_registry.invoke(
        AgentType.PROCUREMENT,
        "procurement.compare_supply_plans",
        {"net_requirement": net, "options": option_results, "required_date": material["required_date"]},
    )
    selection_plan = _create_plan(
        session,
        project,
        procurement,
        "procurement.compare_supply_plans",
        comparison,
        assumptions=["供应商、单价、质量风险和到货日为合成演示数据。"],
        evidence_ids=["ev-mock-erp-demand", "ev-mock-erp-inventory", "ev-mock-supplier-quotes"],
        requires_approval=True,
    )
    _create_tool_call(session, project, net, "procurement.calculate_net_requirement", selection_plan.plan_id)
    _create_tool_call(session, project, comparison, "procurement.compare_supply_plans", selection_plan.plan_id)
    for option in option_results:
        _create_tool_call(session, project, option["eta"], "tracking.calculate_eta", selection_plan.plan_id)
    for evidence_id, system, source_type in (
        ("ev-mock-erp-demand", "MockERP", "Material Demand"),
        ("ev-mock-erp-inventory", "MockERP", "Inventory"),
        ("ev-mock-supplier-quotes", "MockERP", "Supplier Quote"),
    ):
        _add_evidence(session, project, evidence_id, system, source_type)

    released = _append_event(
        session,
        project,
        BusinessEventType.SALES_ORDER_RELEASED,
        object_type="sales_order",
        object_id=f"SO-{project.project_id}",
        payload=MOCK_ERP.sales_order_release(
            f"SO-{project.project_id}", fixture["project"]["order_quantity"]
        ),
        evidence_ids=["ev-mock-erp-demand"],
    )
    demand_event = _append_event(
        session,
        project,
        BusinessEventType.MATERIAL_DEMAND_CREATED,
        object_type="material_demand",
        object_id=material["demand_id"],
        payload=MOCK_ERP.material_demand(
            material["demand_id"], material["material_id"], material["required_quantity"]
        ),
        evidence_ids=["ev-mock-erp-demand"],
        causation_id=released.event_id,
    )
    shortage_event = _append_event(
        session,
        project,
        BusinessEventType.MATERIAL_SHORTAGE,
        object_type="material_demand",
        object_id=material["demand_id"],
        payload={"material_id": material["material_id"], "shortage_quantity": net["net_requirement"], "required_date": material["required_date"]},
        source_agent=AgentType.TRACKING,
        target_agent=AgentType.PROCUREMENT,
        evidence_ids=net["evidence_ids"],
        causation_id=demand_event.event_id,
    )
    _append_event(
        session,
        project,
        BusinessEventType.SUPPLY_PLAN_READY,
        object_type="supply_plan_set",
        object_id=f"SUPPLY-SET-{project.project_id}",
        payload={"required_quantity": net["net_requirement"], "options": option_results},
        source_agent=AgentType.PROCUREMENT,
        evidence_ids=selection_plan.evidence_json,
        causation_id=shortage_event.event_id,
    )
    assessment_id = cost_assessment_id
    ensure_cost_assessment_does_not_replace_quote(
        CostAssessment(
            cost_assessment_id=assessment_id,
            parent_order_id=f"SO-{project.project_id}",
            reason="订单执行期缺料方案成本差异",
            status="READY",
        ),
        fixture["accepted_quote_reference"]["status"],
    )
    _append_event(
        session,
        project,
        BusinessEventType.COST_ASSESSMENT_READY,
        object_type="cost_assessment",
        object_id=assessment_id,
        payload={"parent_order_id": f"SO-{project.project_id}", "options": option_results, "quote_status": "ACCEPTED"},
        source_agent=AgentType.QUOTATION,
        target_agent=AgentType.PROCUREMENT,
        evidence_ids=selection_plan.evidence_json,
        causation_id=shortage_event.event_id,
    )
    _add_agent_task(
        session, project, tracking, "tracking.detect_risk", "leader", {"event_id": demand_event.event_id}
    )
    _add_agent_task(
        session,
        project,
        procurement,
        "procurement.compare_supply_plans",
        "partner",
        {"event_id": shortage_event.event_id, "options": option_results},
        depth=1,
    )
    _add_agent_task(
        session,
        project,
        quotation_case,
        "quotation.create_cost_assessment",
        "partner",
        {"cost_assessment_id": assessment_id, "supplier_option_deltas": [item["cost_assessment"] for item in option_results]},
        depth=3,
    )
    _snapshot_update(
        project,
        material_demand=material,
        net_requirement=net,
        supply_options=option_results,
        cost_assessment={
            "cost_assessment_id": assessment_id,
            "parent_order_id": f"SO-{project.project_id}",
            "quote_id": fixture["accepted_quote_reference"]["quote_id"],
            "quote_status": "ACCEPTED",
            "status": "READY",
        },
        collaboration_rounds=[
            {"round": 1, "leader": "tracking", "partner": "procurement", "capability": "procurement.compare_supply_plans"},
            {"round": 2, "leader": "procurement", "partner": "quotation", "capability": "quotation.create_cost_assessment"},
        ],
        milestones=[{"name": "正式物料需求", "status": "已发布"}, {"name": "缺料方案", "status": "等待人工选择"}],
        shortage_fixture=fixture,
    )
    _set_case(procurement, OperationalState.AWAITING_APPROVAL, "AWAITING_SELECTION")
    approval = _create_approval(
        session,
        project,
        procurement,
        "supplier_selection",
        f"SUPPLY-SET-{project.project_id}",
        selection_plan,
        {"next_action": "create_po_draft", "available_option_ids": [item["option_id"] for item in option_results]},
    )
    _set_case(tracking, OperationalState.WAITING_EVENT)
    return {"project_id": project.project_id, "approval_id": approval.approval_id}


def date_from_string(value: str):
    from datetime import date

    return date.fromisoformat(value)


def run_scenario(
    session: Session,
    scenario: str,
    seed: int,
    idempotency_key: str,
    *,
    forced_project_id: str | None = None,
) -> dict[str, Any]:
    if scenario not in FIXTURES:
        raise HTTPException(status_code=404, detail=f"未知演示场景：{scenario}")
    if not forced_project_id:
        cached = session.get(IdempotencyRow, idempotency_key)
        if cached:
            if cached.operation != f"scenario:{scenario}":
                raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他操作")
            return cached.response_json

    fixture = _load_fixture(scenario)
    project = _base_project(session, scenario, fixture, seed, idempotency_key, forced_project_id)
    if scenario == "normal_order":
        result = _run_normal_order(session, project, fixture, seed)
    else:
        result = _run_shortage(session, project, fixture, seed)
    run_id = f"RUN-{project.project_id}"
    session.add(
        ReplayRunRow(
            run_id=run_id,
            scenario=scenario,
            seed=seed,
            mode="fixed_replay",
            finished_at=_utc_now(),
            result="waiting_approval",
        )
    )
    result = {**result, "scenario": scenario, "run_id": run_id, "seed": seed, "data_source": "synthetic_demo_only"}
    if not forced_project_id:
        session.add(IdempotencyRow(idempotency_key=idempotency_key, operation=f"scenario:{scenario}", response_json=result))
    _record_audit(
        session,
        project,
        "scenario.run",
        {"scenario": scenario, "seed": seed},
        result,
        details={"data_source": "synthetic_demo_only", "rule_version": RULE_VERSION},
    )
    session.commit()
    return result


def _create_next_approval(
    session: Session,
    project: ProjectRow,
    case: AgentCaseRow,
    action_type: str,
    object_id: str,
    details: dict[str, Any],
    *,
    assumptions: list[str],
    evidence_ids: list[str],
    action_payload: dict[str, Any],
) -> ApprovalRow:
    if case.operational_state == OperationalState.READY.value:
        _set_case(case, OperationalState.VALIDATING)
    plan = _create_plan(
        session,
        project,
        case,
        action_type,
        details,
        assumptions=assumptions,
        evidence_ids=evidence_ids,
        requires_approval=True,
    )
    _set_case(case, OperationalState.AWAITING_APPROVAL)
    return _create_approval(session, project, case, action_type, object_id, plan, action_payload)


def _approve_quote(session: Session, project: ProjectRow, approval: ApprovalRow, case: AgentCaseRow) -> None:
    _set_case(case, OperationalState.EXECUTING, "ACCEPTED")
    _set_case(case, OperationalState.DONE)
    event = _append_event(
        session,
        project,
        BusinessEventType.QUOTE_APPROVED,
        object_type="quotation",
        object_id=approval.object_id,
        payload={"status": "ACCEPTED", "quote": project.snapshot_json["quote"]},
        evidence_ids=["ev-mock-erp-bom", "ev-mock-erp-item-price"],
    )
    _snapshot_update(project, quote_status="ACCEPTED", milestones=[{"name": "报价", "status": "已批准"}])
    tracking = _new_case(
        session,
        project.project_id,
        AgentType.TRACKING,
        "ORDER_NOT_RELEASED",
        "销售订单发布后维护订单里程碑和发运门禁。",
    )
    details = {"quotation_event_id": event.event_id, "quote_id": approval.object_id}
    _add_agent_task(session, project, tracking, "tracking.read_milestones", "leader", details)
    _create_next_approval(
        session,
        project,
        tracking,
        "sales_order_release",
        f"SO-{project.project_id}",
        details,
        assumptions=["此操作在演示 MockERP 中发布销售订单事实。"],
        evidence_ids=["ev-mock-erp-order"],
        action_payload={"next_action": "start_normal_parallel_work"},
    )


def _start_normal_parallel_work(
    session: Session, project: ProjectRow, case: AgentCaseRow
) -> None:
    fixture = project.snapshot_json["normal_fixture"]
    _set_case(case, OperationalState.EXECUTING, "MONITORING")
    so_event = _append_event(
        session,
        project,
        BusinessEventType.SALES_ORDER_RELEASED,
        object_type="sales_order",
        object_id=f"SO-{project.project_id}",
        payload=MOCK_ERP.sales_order_release(
            f"SO-{project.project_id}", fixture["project"]["order_quantity"]
        ),
        evidence_ids=["ev-mock-erp-order"],
    )
    material = fixture["material_demand"]
    _add_evidence(session, project, "ev-mock-erp-order", "MockERP", "Sales Order")
    _add_evidence(session, project, "ev-mock-erp-demand", "MockERP", "Material Demand")
    demand = _append_event(
        session,
        project,
        BusinessEventType.MATERIAL_DEMAND_CREATED,
        object_type="material_demand",
        object_id=material["demand_id"],
        payload=MOCK_ERP.material_demand(
            material["demand_id"], material["material_id"], material["required_quantity"]
        ),
        evidence_ids=["ev-mock-erp-demand"],
        causation_id=so_event.event_id,
    )
    _append_event(
        session,
        project,
        BusinessEventType.IQC_REQUESTED,
        object_type="inspection_request",
        object_id=f"IQC-{project.project_id}",
        payload={"status": "requested", "authority": "MockERP"},
        evidence_ids=["ev-mock-erp-demand"],
        causation_id=demand.event_id,
    )
    net = tool_registry.invoke(AgentType.PROCUREMENT, "procurement.calculate_net_requirement", material)
    procurement = _new_case(
        session,
        project.project_id,
        AgentType.PROCUREMENT,
        "DEMAND_RECEIVED",
        "处理 MockERP 正式发布的物料需求。",
    )
    _set_case(procurement, OperationalState.VALIDATING, "ANALYZING")
    _set_case(procurement, OperationalState.EXECUTING)
    _set_case(procurement, OperationalState.DONE, "COMPLETED" if net["net_requirement"] == "0" else "BLOCKED")
    _add_agent_task(
        session,
        project,
        procurement,
        "procurement.calculate_net_requirement",
        "partner",
        {"event_id": demand.event_id, "net_requirement": net},
    )
    _create_tool_call(session, project, net, "procurement.calculate_net_requirement", None)
    _add_evidence(session, project, "ev-mock-mes-quality", "MockMES", "Quality Release")
    mes_quality = MOCK_MES.quality_status(project.project_id, fixture["delivery"]["mock_mes_quality_released"])
    _append_event(
        session,
        project,
        BusinessEventType[mes_quality["event_type"]],
        object_type="quality_status",
        object_id=mes_quality["object_id"],
        payload=mes_quality,
        target_agent=AgentType.TRACKING,
        evidence_ids=[mes_quality["evidence_id"]],
        causation_id=so_event.event_id,
    )
    if not fixture["delivery"]["mock_mes_quality_released"]:
        suspended_quality = _new_case(
            session,
            project.project_id,
            AgentType.QUALITY_DOCUMENT,
            "SUSPENDED",
            "遇到 MockMES 质量冻结时挂起资料包，不发布质量状态。",
        )
        _set_case(suspended_quality, OperationalState.VALIDATING, "SUSPENDED")
        _set_case(suspended_quality, OperationalState.WAITING_EVENT)
        _set_case(case, OperationalState.WAITING_EVENT, "SHIPMENT_BLOCKED")
        _snapshot_update(project, gates={"quality_released": False, "document_package_approved": False})
        return
    quality = _new_case(
        session,
        project.project_id,
        AgentType.QUALITY_DOCUMENT,
        "CHECKLIST_OPEN",
        "收集 MockERP/MockMES 证据并生成受控资料包草稿。",
    )
    _set_case(quality, OperationalState.VALIDATING, "COLLECTING_EVIDENCE")
    _set_case(quality, OperationalState.EXECUTING, "PACKAGE_DRAFT")
    doc_inputs = {
        "required": ["inspection_report", "material_certificate", "process_record"],
        "evidence": ["inspection_report", "material_certificate", "process_record"]
        if fixture["delivery"]["mock_document_evidence_complete"]
        else ["inspection_report"],
    }
    completeness = tool_registry.invoke(AgentType.QUALITY_DOCUMENT, "quality.check_completeness", doc_inputs)
    _add_agent_task(session, project, quality, "quality.collect_evidence", "partner", doc_inputs)
    _add_agent_task(session, project, quality, "quality.check_completeness", "leader", doc_inputs)
    if not completeness["complete"]:
        _set_case(quality, OperationalState.WAITING_EVENT, "INCOMPLETE")
        _set_case(case, OperationalState.WAITING_EVENT, "MONITORING")
        _snapshot_update(project, document_completeness=completeness, gates={"quality_released": True, "document_package_approved": False})
        return
    package_draft = tool_registry.invoke(
        AgentType.QUALITY_DOCUMENT,
        "quality.build_package_draft",
        {"checklist": doc_inputs, "completeness": completeness},
    )
    package_plan = _create_plan(
        session,
        project,
        quality,
        "quality.build_package_draft",
        package_draft,
        assumptions=["文件清单与资料均为 Mock 演示证据。"],
        evidence_ids=["ev-mock-mes-quality", "ev-mock-document-package"],
        requires_approval=True,
    )
    _add_evidence(session, project, "ev-mock-document-package", "MockMES", "Production Documents")
    _create_tool_call(session, project, completeness, "quality.check_completeness", package_plan.plan_id)
    _create_tool_call(session, project, package_draft, "quality.build_package_draft", package_plan.plan_id)
    _add_agent_task(session, project, quality, "quality.build_package_draft", "leader", package_draft)
    _set_case(quality, OperationalState.AWAITING_APPROVAL, "AWAITING_REVIEW")
    package_approval = _create_approval(
        session,
        project,
        quality,
        "document_package_approval",
        f"DOC-PACKAGE-{project.project_id}",
        package_plan,
        {"next_action": "check_ship_gate", "completeness": completeness},
    )
    _set_case(case, OperationalState.WAITING_EVENT, "MONITORING")
    _snapshot_update(
        project,
        net_requirement=net,
        gates={"quality_released": True, "document_package_approved": False},
        milestones=[
            {"name": "报价", "status": "已批准"},
            {"name": "销售订单", "status": "已发布"},
            {"name": "物料需求", "status": "库存覆盖" if net["net_requirement"] == "0" else "待处理"},
            {"name": "质量放行", "status": "MockMES 权威事件已记录"},
            {"name": "资料包", "status": "等待审核"},
        ],
        collaboration_rounds=[
            {"round": 1, "leader": "quotation", "partner": "tracking", "capability": "tracking.read_milestones"},
            {"round": 2, "leader": "tracking", "partner": "procurement", "capability": "procurement.calculate_net_requirement"},
            {"round": 3, "leader": "tracking", "partner": "quality_document", "capability": "quality.build_package_draft"},
        ],
    )
    _record_audit(session, project, "normal_order.release", {"sales_order": so_event.event_id}, {"approval_id": package_approval.approval_id})


def _finish_package_approval(
    session: Session, project: ProjectRow, approval: ApprovalRow, case: AgentCaseRow
) -> None:
    quality_released = any(
        row.event_type == BusinessEventType.QUALITY_RELEASED.value
        for row in session.scalars(select(BusinessEventRow).where(BusinessEventRow.project_id == project.project_id)).all()
    )
    _set_case(case, OperationalState.EXECUTING, "APPROVED")
    if not quality_released:
        raise HTTPException(status_code=409, detail="没有 MockMES 权威 QUALITY_RELEASED 事件，不能批准发运资料包")
    package_event = _append_event(
        session,
        project,
        BusinessEventType.DOCUMENT_PACKAGE_APPROVED,
        object_type="document_package",
        object_id=approval.object_id,
        payload={"status": "APPROVED", "approved_by": "local-demo-user", "does_not_mean_quality_release": True},
        target_agent=AgentType.TRACKING,
        evidence_ids=["ev-mock-mes-quality", "ev-mock-document-package"],
    )
    gate = tool_registry.invoke(
        AgentType.TRACKING,
        "tracking.check_ship_gate",
        {"quality_released": True, "document_package_approved": True},
    )
    _create_tool_call(session, project, gate, "tracking.check_ship_gate", None)
    if not gate["ready_to_request_shipment_approval"]:
        raise HTTPException(status_code=409, detail="双门禁未通过，不能申请发运审批")
    tracking = session.scalar(
        select(AgentCaseRow).where(
            AgentCaseRow.project_id == project.project_id,
            AgentCaseRow.agent_type == AgentType.TRACKING.value,
        )
    )
    if tracking is None:
        raise HTTPException(status_code=409, detail="跟单 Agent Case 缺失")
    _set_case(case, OperationalState.WAITING_EVENT, "APPROVED")
    details = {"gate": gate, "package_event_id": package_event.event_id}
    _set_case(tracking, OperationalState.VALIDATING, "READY_TO_SHIP")
    _set_case(tracking, OperationalState.EXECUTING)
    ship_required = _append_event(
        session,
        project,
        BusinessEventType.SHIPMENT_APPROVAL_REQUIRED,
        object_type="shipment",
        object_id=f"SHIP-{project.project_id}",
        payload={"gate": gate},
        source_agent=AgentType.TRACKING,
        evidence_ids=["ev-mock-mes-quality", "ev-mock-document-package"],
        causation_id=package_event.event_id,
    )
    _set_case(tracking, OperationalState.AWAITING_APPROVAL, "WAITING_APPROVAL")
    plan = _create_plan(
        session,
        project,
        tracking,
        "tracking.check_ship_gate",
        details,
        assumptions=["发运授权仍需人工批准；双门禁只允许提交审批。"],
        evidence_ids=["ev-mock-mes-quality", "ev-mock-document-package"],
        requires_approval=True,
    )
    _create_approval(
        session,
        project,
        tracking,
        "shipment_release",
        f"SHIP-{project.project_id}",
        plan,
        {"next_action": "mock_delivery", "required_event_id": ship_required.event_id},
    )
    _snapshot_update(
        project,
        gates={"quality_released": True, "document_package_approved": True},
        milestones=[
            {"name": "质量放行", "status": "已通过"},
            {"name": "资料包", "status": "已批准"},
            {"name": "发运", "status": "等待人工审批"},
        ],
    )


def _finish_shipment(
    session: Session, project: ProjectRow, approval: ApprovalRow, tracking: AgentCaseRow
) -> None:
    events = session.scalars(select(BusinessEventRow).where(BusinessEventRow.project_id == project.project_id)).all()
    has_quality = any(row.event_type == BusinessEventType.QUALITY_RELEASED.value for row in events)
    has_package = any(row.event_type == BusinessEventType.DOCUMENT_PACKAGE_APPROVED.value for row in events)
    gate = tool_registry.invoke(
        AgentType.TRACKING,
        "tracking.check_ship_gate",
        {"quality_released": has_quality, "document_package_approved": has_package},
    )
    if not gate["ready_to_request_shipment_approval"]:
        approval.status = "expired"
        raise HTTPException(status_code=409, detail="双门禁状态已变化，发运审批已失效")
    _set_case(tracking, OperationalState.EXECUTING, "READY_TO_SHIP")
    release = _append_event(
        session,
        project,
        BusinessEventType.SHIPMENT_RELEASED,
        object_type="shipment",
        object_id=approval.object_id,
        payload={"status": "RELEASED", "authority": "authorized_mock_operator"},
        evidence_ids=["ev-mock-mes-quality", "ev-mock-document-package"],
    )
    _append_event(
        session,
        project,
        BusinessEventType.DELIVERED,
        object_type="delivery",
        object_id=f"DEL-{project.project_id}",
        payload=MOCK_ERP.delivered(f"DEL-{project.project_id}", release.event_id),
        causation_id=release.event_id,
    )
    quality = session.scalar(
        select(AgentCaseRow).where(
            AgentCaseRow.project_id == project.project_id,
            AgentCaseRow.agent_type == AgentType.QUALITY_DOCUMENT.value,
        )
    )
    if quality:
        _set_case(quality, OperationalState.EXECUTING, "ARCHIVED")
        _set_case(quality, OperationalState.DONE)
    _set_case(tracking, OperationalState.DONE, "CLOSED")
    project.lifecycle_state = "DELIVERED"
    _snapshot_update(
        project,
        lifecycle_state="DELIVERED",
        milestones=[{"name": "发运", "status": "已批准"}, {"name": "签收", "status": "已完成"}, {"name": "资料归档", "status": "已归档"}],
    )


def _approve_supplier_selection(
    session: Session, project: ProjectRow, approval: ApprovalRow, case: AgentCaseRow, selected_option_id: str | None
) -> None:
    options = project.snapshot_json["supply_options"]
    selected = next((item for item in options if item["option_id"] == selected_option_id), None)
    if selected is None:
        raise HTTPException(status_code=422, detail="必须从方案列表选择一个供应方案")
    _set_case(case, OperationalState.EXECUTING, "PLAN_READY")
    _set_case(case, OperationalState.EXECUTING, "PO_DRAFTED")
    supply_event = _append_event(
        session,
        project,
        BusinessEventType.SUPPLY_PLAN_READY,
        object_type="supply_plan",
        object_id=selected_option_id,
        payload={"selected_option": selected},
        source_agent=AgentType.PROCUREMENT,
        evidence_ids=["ev-mock-erp-supplier-quotes"],
    )
    po_id = f"PO-DRAFT-{project.project_id}"
    po_details = tool_registry.invoke(
        AgentType.PROCUREMENT,
        "procurement.create_po_draft",
        MOCK_ERP.purchase_order_draft(po_id, selected),
    )
    po_event = _append_event(
        session,
        project,
        BusinessEventType.PO_DRAFT_READY,
        object_type="purchase_order_draft",
        object_id=po_id,
        payload=po_details,
        source_agent=AgentType.PROCUREMENT,
        evidence_ids=["ev-mock-erp-supplier-quotes"],
        causation_id=supply_event.event_id,
    )
    details = {"purchase_order_id": po_id, "selected_option": selected, "source_event_id": po_event.event_id}
    _add_agent_task(session, project, case, "procurement.create_po_draft", "leader", details, depth=3)
    _snapshot_update(
        project,
        selected_supply_option=selected,
        purchase_order={"purchase_order_id": po_id, "status": "DRAFT"},
        milestones=[{"name": "供应方案", "status": selected["supplier_name"] + " 已选择"}, {"name": "PO 草稿", "status": "等待审批"}],
    )
    plan = _create_plan(
        session,
        project,
        case,
        "procurement.create_po_draft",
        details,
        assumptions=["PO 仅为 MockERP 草稿，审批不等于供应商已确认。"],
        evidence_ids=["ev-mock-erp-supplier-quotes"],
        requires_approval=True,
    )
    _create_tool_call(session, project, po_details, "procurement.create_po_draft", plan.plan_id)
    _set_case(case, OperationalState.AWAITING_APPROVAL, "AWAITING_SELECTION")
    _create_approval(
        session,
        project,
        case,
        "po_draft_approval",
        po_id,
        plan,
        {"next_action": "refresh_eta", "selected_option_id": selected_option_id},
    )


def _approve_po_draft(
    session: Session, project: ProjectRow, approval: ApprovalRow, case: AgentCaseRow
) -> None:
    selected = project.snapshot_json["selected_supply_option"]
    fixture = project.snapshot_json["shortage_fixture"]
    _set_case(case, OperationalState.EXECUTING, "WAITING_SUPPLIER")
    required_date = date_from_string(fixture["material_demand"]["required_date"])
    supplier_date = date_from_string(selected["confirmed_delivery_date"])
    if supplier_date > required_date:
        _append_event(
            session,
            project,
            BusinessEventType.SUPPLIER_DELAYED,
            object_type="purchase_order",
            object_id=approval.object_id,
            payload={"confirmed_delivery_date": supplier_date.isoformat(), "required_material_date": required_date.isoformat()},
            source_agent=AgentType.PROCUREMENT,
            target_agent=AgentType.TRACKING,
            evidence_ids=["ev-mock-erp-supplier-quotes"],
        )
    eta = selected["eta"]
    customer_due = date_from_string(fixture["project"]["required_delivery_date"])
    has_delivery_risk = date_from_string(eta["eta_date"]) > customer_due
    if has_delivery_risk:
        _append_event(
            session,
            project,
            BusinessEventType.DELIVERY_RISK,
            object_type="sales_order",
            object_id=f"SO-{project.project_id}",
            payload={"eta": eta, "customer_due_date": customer_due.isoformat()},
            source_agent=AgentType.TRACKING,
        )
    else:
        _append_event(
            session,
            project,
            BusinessEventType.SUPPLY_PLAN_READY,
            object_type="eta_update",
            object_id=f"ETA-{project.project_id}",
            payload={"eta": eta, "within_customer_due_date": True},
            source_agent=AgentType.TRACKING,
            causation_id=approval.approval_id,
        )
    _set_case(case, OperationalState.WAITING_EVENT, "WAITING_SUPPLIER")
    _snapshot_update(
        project,
        eta=eta,
        purchase_order={"purchase_order_id": approval.object_id, "status": "APPROVED_DRAFT", "authority": "MockERP"},
        milestones=[{"name": "PO 草稿", "status": "已批准"}, {"name": "新 ETA", "status": eta["eta_date"]}],
        delivery_risk=has_delivery_risk,
    )


def decide_approval(
    session: Session,
    approval_id: str,
    decision: str,
    *,
    selected_option_id: str | None = None,
    requested_fields: list[str] | None = None,
    message: str | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    cached = session.get(IdempotencyRow, idempotency_key)
    operation = f"approval:{approval_id}:{decision}"
    if cached:
        if cached.operation != operation:
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他操作")
        return cached.response_json

    approval = session.get(ApprovalRow, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"审批当前状态为 {approval.status}，不能重复处理")
    project = _project(session, approval.project_id)
    case = session.get(AgentCaseRow, approval.case_id)
    if case is None:
        raise HTTPException(status_code=409, detail="审批绑定的 Agent Case 不存在")
    expected_snapshot = canonical_hash(
        {
            "input_hash": approval.action_payload_json["input_hash"],
            "object_version": approval.object_version,
            "rule_version": approval.rule_version,
        }
    )
    if case.version != approval.object_version or expected_snapshot != approval.snapshot_hash:
        approval.status = "expired"
        session.commit()
        raise HTTPException(status_code=409, detail="对象版本或审批快照已变化，审批已过期")

    if decision == "request_data":
        approval.action_payload_json = {
            **approval.action_payload_json,
            "data_request": {
                "requested_fields": requested_fields or [],
                "message": message,
                "requested_at": _utc_now().isoformat(),
            },
        }
        output = {"approval_id": approval_id, "status": approval.status, "data_request_recorded": True, "requested_fields": requested_fields or [], "message": message}
    elif decision == "reject":
        approval.status = "rejected"
        approval.approver = "local-demo-user"
        approval.decided_at = _utc_now()
        if approval.action_type == "quote_approval":
            _set_case(case, OperationalState.EXECUTING, "REJECTED")
            _set_case(case, OperationalState.DONE)
        else:
            _set_case(case, OperationalState.WAITING_EVENT, "BLOCKED")
        output = {"approval_id": approval_id, "status": approval.status}
    elif decision == "approve":
        approval.status = "approved"
        approval.approver = "local-demo-user"
        approval.decided_at = _utc_now()
        if approval.action_type == "quote_approval":
            _approve_quote(session, project, approval, case)
        elif approval.action_type == "sales_order_release":
            _start_normal_parallel_work(session, project, case)
        elif approval.action_type == "document_package_approval":
            _finish_package_approval(session, project, approval, case)
        elif approval.action_type == "shipment_release":
            _finish_shipment(session, project, approval, case)
        elif approval.action_type == "supplier_selection":
            _approve_supplier_selection(session, project, approval, case, selected_option_id)
        elif approval.action_type == "po_draft_approval":
            _approve_po_draft(session, project, approval, case)
        else:
            raise HTTPException(status_code=422, detail=f"暂不支持此审批动作：{approval.action_type}")
        output = {"approval_id": approval_id, "status": approval.status, "project_id": project.project_id}
    else:
        raise HTTPException(status_code=422, detail="decision 仅支持 approve、reject、request_data")

    _record_audit(
        session,
        project,
        f"approval.{decision}",
        {"approval_id": approval_id, "selected_option_id": selected_option_id, "requested_fields": requested_fields},
        output,
        actor="local-demo-user",
        details={"object_version": approval.object_version, "snapshot_hash": approval.snapshot_hash},
    )
    session.add(IdempotencyRow(idempotency_key=idempotency_key, operation=operation, response_json=output))
    session.commit()
    return output


def reset_project(session: Session, project_id: str, idempotency_key: str) -> dict[str, Any]:
    cached = session.get(IdempotencyRow, idempotency_key)
    if cached:
        if cached.operation != f"reset:{project_id}":
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于其他操作")
        return cached.response_json
    project = _project(session, project_id)
    scenario = project.scenario
    previous_run = session.get(ReplayRunRow, f"RUN-{project_id}")
    if previous_run is None:
        raise HTTPException(status_code=409, detail="找不到项目的回放记录，无法保证按原 seed 复位")
    seed = previous_run.seed
    event_ids = [row.event_id for row in session.scalars(select(BusinessEventRow).where(BusinessEventRow.project_id == project_id)).all()]
    case_ids = [row.case_id for row in session.scalars(select(AgentCaseRow).where(AgentCaseRow.project_id == project_id)).all()]
    plan_ids = [row.plan_id for row in session.scalars(select(PlanRow).where(PlanRow.case_id.in_(case_ids or [""]))).all()]
    session.execute(delete(ApprovalRow).where(ApprovalRow.project_id == project_id))
    session.execute(delete(AgentTaskRow).where(AgentTaskRow.case_id.in_(case_ids or [""])))
    session.execute(delete(ToolCallRow).where(ToolCallRow.project_id == project_id))
    session.execute(delete(PlanRow).where(PlanRow.plan_id.in_(plan_ids or [""])))
    session.execute(delete(AuditRecordRow).where(AuditRecordRow.project_id == project_id))
    session.execute(delete(EvidenceRow).where(EvidenceRow.project_id == project_id))
    session.execute(delete(InboxRow).where(InboxRow.event_id.in_(event_ids or [""])))
    session.execute(delete(OutboxRow).where(OutboxRow.event_id.in_(event_ids or [""])))
    session.execute(delete(BusinessEventRow).where(BusinessEventRow.project_id == project_id))
    session.execute(delete(AgentCaseRow).where(AgentCaseRow.project_id == project_id))
    session.execute(delete(ReplayRunRow).where(ReplayRunRow.run_id == f"RUN-{project_id}"))
    session.execute(delete(ProjectRow).where(ProjectRow.project_id == project_id))
    session.flush()
    result = run_scenario(session, scenario, seed, idempotency_key, forced_project_id=project_id)
    result["reset"] = True
    session.add(IdempotencyRow(idempotency_key=idempotency_key, operation=f"reset:{project_id}", response_json=result))
    session.commit()
    return result


def get_snapshot(session: Session, project_id: str) -> dict[str, Any]:
    project = _project(session, project_id)
    cases = session.scalars(select(AgentCaseRow).where(AgentCaseRow.project_id == project_id)).all()
    approvals = session.scalars(select(ApprovalRow).where(ApprovalRow.project_id == project_id)).all()
    return {
        **project.snapshot_json,
        "cases": [
            {
                "case_id": item.case_id,
                "agent_type": item.agent_type,
                "operational_state": item.operational_state,
                "business_state": item.business_state,
                "version": item.version,
                "objective": item.objective,
            }
            for item in cases
        ],
        "pending_approvals": [item.approval_id for item in approvals if item.status == "pending"],
    }
