from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.schemas import ApprovalDecision, RequestData, ScenarioRunRequest
from app.domain.models import AGENT_CATALOG, AgentType
from app.persistence.database import SessionLocal, get_session, init_database
from app.persistence.models import (
    AgentCaseRow,
    ApprovalRow,
    AuditRecordRow,
    BusinessEventRow,
    PlanRow,
    ProjectRow,
    ReplayRunRow,
)
from app.adapters.erp.erpnext import ERPNextClient
from app.adapters.mes.openmes import OpenMESClient
from app.integrations.deepseek import DeepSeekClient
from app.integrations.errors import IntegrationError
from app.integrations.settings import IntegrationSettings
from app.runtime.scenarios import decide_approval, get_snapshot, reset_project, run_scenario


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title="汽车零部件四智能体动态协作平台",
    version="0.2.0",
    description="四智能体协作平台；已提供真实系统连接层，固定场景回放仍为明确标注的测试功能。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)


def _row_dict(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@app.get("/api/health", tags=["system"])
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "mode": "local-runtime", "database": "connected"}


def _integration_status_error(exc: IntegrationError) -> HTTPException:
    status_code = exc.status_code if exc.status_code in {401, 403, 404, 409, 422} else 502
    if exc.code == "not_configured":
        status_code = 409
    return HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)})


@app.get("/api/integrations/status", tags=["integrations"])
def integration_status() -> dict:
    """Report which providers are configured without revealing credentials."""
    return IntegrationSettings.from_environment().public_status()


@app.post("/api/integrations/deepseek/check", tags=["integrations"])
async def check_deepseek() -> dict:
    """Send a tiny explicit connectivity check; DeepSeek may bill this request."""
    settings = IntegrationSettings.from_environment()
    try:
        client = DeepSeekClient(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
        try:
            result = await client.chat_completion(
                [{"role": "user", "content": "Reply with OK."}], max_tokens=8
            )
        finally:
            await client.aclose()
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    return {"connected": True, "model": result.get("model", settings.deepseek_model)}


@app.post("/api/integrations/erpnext/check", tags=["integrations"])
async def check_erpnext() -> dict:
    settings = IntegrationSettings.from_environment()
    try:
        client = ERPNextClient(
            settings.erpnext_base_url,
            settings.erpnext_api_key,
            settings.erpnext_api_secret,
            draft_writes_enabled=settings.erpnext_draft_writes_enabled,
        )
        try:
            result = await client.get_logged_user()
        finally:
            await client.aclose()
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    return {"connected": True, "authenticated_user": result["user"]}


@app.post("/api/integrations/openmes/check", tags=["integrations"])
async def check_openmes() -> dict:
    settings = IntegrationSettings.from_environment()
    try:
        client = OpenMESClient(
            settings.openmes_base_url,
            user_token=settings.openmes_user_token or None,
            erp_api_key=settings.openmes_erp_api_key or None,
        )
        try:
            health_result = await client.health()
            user = None
            if settings.openmes_user_token:
                user = await client.current_user()
        finally:
            await client.aclose()
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    return {
        "connected": True,
        "health": health_result.get("status"),
        "authenticated_user": user.get("username") if user else None,
        "erp_read_api_configured": bool(settings.openmes_erp_api_key),
    }


@app.get("/api/integrations/openmes/work-orders", tags=["integrations"])
async def openmes_work_orders(
    status: str | None = None,
    line_id: int | None = None,
    due_before: str | None = None,
    search: str | None = None,
    per_page: int = Query(default=15, ge=1, le=100),
    page: int = Query(default=1, ge=1),
) -> dict:
    """Read actual OpenMES work orders using documented, allowlisted filters."""
    settings = IntegrationSettings.from_environment()
    try:
        client = OpenMESClient(
            settings.openmes_base_url,
            user_token=settings.openmes_user_token or None,
        )
        try:
            filters = {
                key: value
                for key, value in {
                    "status": status,
                    "line_id": line_id,
                    "due_before": due_before,
                    "search": search,
                    "per_page": per_page,
                    "page": page,
                }.items()
                if value is not None
            }
            return await client.list_work_orders(filters)
        finally:
            await client.aclose()
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc


@app.get("/api/integrations/openmes/work-orders/{work_order_id}", tags=["integrations"])
async def openmes_work_order(work_order_id: str) -> dict:
    settings = IntegrationSettings.from_environment()
    try:
        client = OpenMESClient(
            settings.openmes_base_url,
            user_token=settings.openmes_user_token or None,
        )
        try:
            return await client.get_work_order(work_order_id)
        finally:
            await client.aclose()
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc


@app.get("/api/projects", tags=["projects"])
def list_projects(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(ProjectRow).order_by(ProjectRow.created_at.desc())).all()
    return [
        {
            "project_id": row.project_id,
            "correlation_id": row.correlation_id,
            "customer_id": row.customer_id,
            "product_id": row.product_id,
            "current_version": row.current_version,
            "lifecycle_state": row.lifecycle_state,
            "data_source": row.data_source,
            "scenario": row.scenario,
        }
        for row in rows
    ]


@app.get("/api/projects/{project_id}/snapshot", tags=["projects"])
def project_snapshot(project_id: str, session: Session = Depends(get_session)) -> dict:
    return get_snapshot(session, project_id)


@app.get("/api/projects/{project_id}/timeline", tags=["projects"])
def project_timeline(
    project_id: str,
    correlation_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    project = session.get(ProjectRow, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if correlation_id and correlation_id != project.correlation_id:
        raise HTTPException(status_code=404, detail="correlation_id 与项目不匹配")
    rows = session.scalars(
        select(BusinessEventRow)
        .where(BusinessEventRow.project_id == project_id)
        .order_by(BusinessEventRow.occurred_at, BusinessEventRow.event_id)
    ).all()
    return [
        {
            "event_id": row.event_id,
            "event_type": row.event_type,
            "project_id": row.project_id,
            "correlation_id": row.correlation_id,
            "causation_id": row.causation_id,
            "source_agent": row.source_agent,
            "target_agent": row.target_agent,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "object_version": row.object_version,
            "occurred_at": row.occurred_at.isoformat(),
            "payload": row.payload_json,
            "evidence_ids": row.evidence_ids_json,
        }
        for row in rows
    ]


@app.post("/api/projects/{project_id}/reset", tags=["projects"])
def project_reset(
    project_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    return reset_project(session, project_id, idempotency_key)


@app.get("/api/agents", tags=["agents"])
def list_agents() -> list[dict]:
    return [agent.model_dump(mode="json") for agent in AGENT_CATALOG]


@app.get("/api/agents/{agent_type}/cases", tags=["agents"])
def agent_cases(agent_type: AgentType, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(
        select(AgentCaseRow).where(AgentCaseRow.agent_type == agent_type.value).order_by(AgentCaseRow.case_id)
    ).all()
    return [_row_dict(row) for row in rows]


@app.get("/api/approvals", tags=["approvals"])
def approvals(
    status: str = Query(default="pending"), session: Session = Depends(get_session)
) -> list[dict]:
    rows = session.scalars(
        select(ApprovalRow).where(ApprovalRow.status == status).order_by(ApprovalRow.created_at)
    ).all()
    return [_row_dict(row) for row in rows]


@app.post("/api/approvals/{approval_id}/approve", tags=["approvals"])
def approve(
    approval_id: str,
    body: ApprovalDecision,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    return decide_approval(
        session,
        approval_id,
        "approve",
        selected_option_id=body.selected_option_id,
        idempotency_key=idempotency_key,
    )


@app.post("/api/approvals/{approval_id}/reject", tags=["approvals"])
def reject(
    approval_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    return decide_approval(session, approval_id, "reject", idempotency_key=idempotency_key)


@app.post("/api/approvals/{approval_id}/request-data", tags=["approvals"])
def request_data(
    approval_id: str,
    body: RequestData,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    return decide_approval(
        session,
        approval_id,
        "request_data",
        requested_fields=body.requested_fields,
        message=body.message,
        idempotency_key=idempotency_key,
    )


@app.get("/api/plans/{plan_id}", tags=["plans"])
def plan_details(plan_id: str, session: Session = Depends(get_session)) -> dict:
    row = session.get(PlanRow, plan_id)
    if row is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    return _row_dict(row)


@app.get("/api/audit", tags=["audit"])
def audit_records(
    trace_id: str | None = None,
    correlation_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    statement = select(AuditRecordRow)
    if trace_id:
        statement = statement.where(AuditRecordRow.trace_id == trace_id)
    if correlation_id:
        statement = statement.where(AuditRecordRow.correlation_id == correlation_id)
    rows = session.scalars(statement.order_by(AuditRecordRow.created_at, AuditRecordRow.audit_id)).all()
    return [_row_dict(row) for row in rows]


@app.post("/api/scenarios/{name}/run", tags=["scenarios"])
def scenario_run(
    name: str,
    body: ScenarioRunRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    return run_scenario(session, name, body.seed, idempotency_key)


@app.post("/api/replay/{run_id}/start", tags=["replay"])
def replay_start(
    run_id: str,
    body: ScenarioRunRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    old_run = session.get(ReplayRunRow, run_id)
    if old_run is None:
        raise HTTPException(status_code=404, detail="回放记录不存在")
    return run_scenario(session, old_run.scenario, body.seed, idempotency_key)


@app.get("/api/replay/{run_id}", tags=["replay"])
def replay_details(run_id: str, session: Session = Depends(get_session)) -> dict:
    row = session.get(ReplayRunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回放记录不存在")
    return _row_dict(row)


@app.websocket("/api/projects/{project_id}/stream")
async def project_stream(websocket: WebSocket, project_id: str) -> None:
    await websocket.accept()
    last_event_id: str | None = None
    try:
        while True:
            with SessionLocal() as session:
                project = session.get(ProjectRow, project_id)
                if project is None:
                    await websocket.send_json({"error": "项目不存在"})
                    await websocket.close(code=1008)
                    return
                statement = select(BusinessEventRow).where(BusinessEventRow.project_id == project_id)
                if last_event_id:
                    statement = statement.where(BusinessEventRow.event_id > last_event_id)
                rows = session.scalars(statement.order_by(BusinessEventRow.event_id)).all()
                payload = [
                    {"event_id": row.event_id, "event_type": row.event_type, "occurred_at": row.occurred_at.isoformat(), "payload": row.payload_json}
                    for row in rows
                ]
            if payload:
                for event in payload:
                    await websocket.send_json(event)
                    last_event_id = event["event_id"]
            else:
                await websocket.send_json({"type": "heartbeat", "project_id": project_id})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
