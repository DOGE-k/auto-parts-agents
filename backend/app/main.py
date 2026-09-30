from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
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
from app.services.llm_quotation import (
    extract_rfq,
    extract_rfq_deterministic,
    compute_complexity_factor,
    compute_quantity_discount,
)
from app.integrations.errors import IntegrationError
from app.integrations.settings import IntegrationSettings
from app.runtime.scenarios import decide_approval, get_snapshot, reset_project, run_scenario
from app.aip import init_aip_agents


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    init_aip_agents(_app)
    # 初始化真实订单审批系统
    from app.services.real_order import init_approval_system
    init_approval_system()
    yield


app = FastAPI(
    title="汽车零部件四智能体动态协作平台",
    version="0.2.0",
    description="四智能体协作平台；已提供真实系统连接层，固定场景回放仍为明确标注的测试功能。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Real-Write-Token"],
)


@app.middleware("http")
async def isolate_mock_api(request, call_next):
    """真实表面拒绝旧 Mock 场景 API，避免误把演示数据当业务数据。"""
    from app.runtime.surface import mock_demo_enabled, public_surface

    if not mock_demo_enabled():
        path = request.url.path
        if any(path == prefix or path.startswith(prefix + "/") for prefix in public_surface()["mock_api_prefixes"]):
            return JSONResponse(
                status_code=404,
                content={
                    "code": "mock_surface_disabled",
                    "message": "Mock 演示 API 已隔离；请设置 MOCK_DEMO_ENABLED=true 或使用真实业务入口",
                },
            )
    return await call_next(request)


def _row_dict(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@app.get("/api/health", tags=["system"])
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "mode": "local-runtime", "database": "connected"}


@app.get("/api/runtime/surface", tags=["system"])
def runtime_surface() -> dict:
    """公开当前 API 表面，前端据此隐藏被隔离的 Mock 导航。"""
    from app.runtime.surface import public_surface
    return public_surface()


@app.get("/api/runtime/discovery", tags=["system"])
def runtime_discovery() -> dict:
    """Expose external discovery configuration without revealing credentials."""
    settings = IntegrationSettings.from_environment()
    return {
        "provider": "wutong",
        "configured": bool(settings.wutong_discovery_url),
        "registry_configured": bool(settings.wutong_registry_url),
        "tenant_configured": bool(settings.wutong_tenant),
        "read_only": True,
        "contract": "ACPs ADP /discover",
    }


@app.get("/api/runtime/registry", tags=["system"])
def runtime_registry() -> dict:
    """Expose the optional Registry read contract without credentials."""
    settings = IntegrationSettings.from_environment()
    return {
        "provider": "wutong",
        "configured": bool(settings.wutong_registry_url),
        "tenant_configured": bool(settings.wutong_tenant),
        "read_only": True,
        "contract": "vendored ACPs Registry /health + /api/v1/agent/public/recent",
        "writes_enabled": False,
    }


@app.post("/api/real-orders/discovery/search", tags=["real-orders"])
async def real_order_discovery_search(body: dict) -> dict:
    """Query an optional Wutong/ACPs discovery service (read-only)."""
    settings = IntegrationSettings.from_environment()
    if not settings.wutong_discovery_url:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "discovery_not_configured",
                "message": "Wutong discovery 未配置 WUTONG_DISCOVERY_URL；未使用本地静态目录冒充外部发现",
            },
        )
    from app.integrations.wutong import WutongDiscoveryClient

    query = str((body or {}).get("query", ""))
    discovery_type = str((body or {}).get("type", "explicit"))
    try:
        limit = int((body or {}).get("limit", 5))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="limit 必须是整数") from exc
    filter_obj = (body or {}).get("filter")
    client = WutongDiscoveryClient(
        settings.wutong_discovery_url,
        tenant=settings.wutong_tenant,
    )
    try:
        return await client.discover(
            query,
            limit=limit,
            discovery_type=discovery_type,
            filter_obj=filter_obj if isinstance(filter_obj, dict) else None,
        )
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    finally:
        await client.aclose()


@app.get("/api/real-orders/registry/agents", tags=["real-orders"])
async def real_order_registry_agents(limit: int = 5) -> dict:
    """Read recently approved external agents from the optional Registry."""
    settings = IntegrationSettings.from_environment()
    if not settings.wutong_registry_url:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "registry_not_configured",
                "message": "Wutong Registry 未配置 WUTONG_REGISTRY_URL；未使用本地 ACS 目录冒充外部注册结果",
            },
        )
    from app.integrations.wutong import WutongRegistryClient

    client = WutongRegistryClient(settings.wutong_registry_url, tenant=settings.wutong_tenant)
    try:
        return await client.list_recent_agents(limit=limit)
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    finally:
        await client.aclose()


def _integration_status_error(exc: IntegrationError) -> HTTPException:
    status_code = exc.status_code if exc.status_code in {401, 403, 404, 409, 422} else 502
    if exc.code == "not_configured":
        status_code = 409
    return HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)})


def require_real_write_access(
    x_real_write_token: str | None = Header(default=None, alias="X-Real-Write-Token"),
) -> bool:
    """Protect external ERP/MES writes with an explicitly configured local token."""
    expected = os.getenv("REAL_WRITE_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="真实系统写入未启用：未配置 REAL_WRITE_API_TOKEN")
    if not x_real_write_token or not hmac.compare_digest(x_real_write_token, expected):
        raise HTTPException(status_code=403, detail="真实系统写入令牌无效")
    return True


async def require_real_identity(
    authorization: str | None = Header(default=None),
):
    """Resolve the authenticated upstream user for real business actions.

    ``approved_by`` in a request is treated only as a compatibility hint and
    is checked against this server-resolved identity by each write endpoint.
    """
    from app.services.identity import resolve_real_identity

    bearer = ""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not value.strip():
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_authorization", "message": "Authorization 必须是 Bearer 会话"},
            )
        bearer = value.strip()
    try:
        return await resolve_real_identity(request_bearer_token=bearer)
    except IntegrationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "identity_unavailable", "message": f"真实身份解析失败：{exc}"},
        ) from exc


def _actor_for_request(provided: str | None, identity, capability: str) -> str:
    """Validate an optional legacy actor field and enforce the business role."""
    from app.services.identity import ensure_role

    try:
        ensure_role(identity, capability)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    value = (provided or "").strip()
    if value and value != identity.actor_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "actor_mismatch",
                "message": "请求中的审批人必须与真实身份源返回的当前用户一致",
                "authenticated_actor": identity.actor_id,
            },
        )
    return identity.actor_id


@app.get("/api/integrations/status", tags=["integrations"])
def integration_status() -> dict:
    """Report which providers are configured without revealing credentials."""
    from app.adapters.factory import get_adapter_status
    status = IntegrationSettings.from_environment().public_status()
    status["adapters"] = get_adapter_status()
    return status


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


@app.post("/api/quotation/analyze-rfq", tags=["quotation"])
async def analyze_rfq(body: dict) -> dict:
    """分析 RFQ 描述，提取结构化信息。配置 DeepSeek 时使用 LLM，否则回退到确定性规则。"""
    description = body.get("description", "")
    use_llm = body.get("use_llm", True)

    settings = IntegrationSettings.from_environment()
    llm_client = None
    if use_llm and settings.deepseek_api_key:
        llm_client = DeepSeekClient(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )

    try:
        result = await extract_rfq(description, llm_client)
    finally:
        if llm_client:
            await llm_client.aclose()

    # 附加报价分析因子
    complexity_factor = compute_complexity_factor(result.get("complexity", "medium"))
    quantity_factor = compute_quantity_discount(result.get("quantity"))
    result["complexity_factor"] = complexity_factor
    result["quantity_discount_factor"] = quantity_factor
    result["price_adjustment_factor"] = round(complexity_factor * quantity_factor, 4)

    return result


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


@app.get("/api/integrations/openmes/production-completions", tags=["integrations"])
async def openmes_production_completions(
    since: str | None = None,
    cursor: str | None = None,
) -> dict:
    """Read OpenMES production completions with the scoped ERP API key."""
    settings = IntegrationSettings.from_environment()
    try:
        client = OpenMESClient(
            settings.openmes_base_url,
            erp_api_key=settings.openmes_erp_api_key or None,
        )
        try:
            return await client.list_erp_production_completions(since=since, cursor=cursor)
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


# ========== 统一 ERP 数据接口（适配器工厂自动切换 Mock ↔ 真实） ==========

@app.get("/api/erp/customers/{customer_id}", tags=["erp"])
async def erp_get_customer(customer_id: str) -> dict:
    """获取客户信息。自动使用 ERPNext 或 Mock 适配器。"""
    from app.adapters.factory import get_erp_adapter
    adapter = get_erp_adapter()
    return await adapter.get_customer(customer_id)


@app.get("/api/erp/items/{item_code}", tags=["erp"])
async def erp_get_item(item_code: str, version: str | None = None) -> dict:
    """获取物料主数据。自动使用 ERPNext 或 Mock 适配器。"""
    from app.adapters.factory import get_erp_adapter
    adapter = get_erp_adapter()
    return await adapter.get_item(item_code, version)


@app.get("/api/erp/items/{item_code}/prices", tags=["erp"])
async def erp_get_prices(item_code: str, as_of: str = "") -> list[dict]:
    """获取物料价格列表。自动使用 ERPNext 或 Mock 适配器。"""
    from app.adapters.factory import get_erp_adapter
    from datetime import date
    adapter = get_erp_adapter()
    return await adapter.get_prices(item_code, as_of or str(date.today()))


@app.get("/api/erp/items/{item_code}/bom", tags=["erp"])
async def erp_get_bom(item_code: str, version: str | None = None) -> dict:
    """获取物料清单。自动使用 ERPNext 或 Mock 适配器。"""
    from app.adapters.factory import get_erp_adapter
    adapter = get_erp_adapter()
    return await adapter.get_bom(item_code, version)


@app.get("/api/erp/inventory", tags=["erp"])
async def erp_get_inventory(item_codes: str) -> list[dict]:
    """获取库存（多个物料用逗号分隔）。自动使用 ERPNext 或 Mock 适配器。"""
    from app.adapters.factory import get_erp_adapter
    adapter = get_erp_adapter()
    items = [x.strip() for x in item_codes.split(",") if x.strip()]
    return await adapter.get_inventory(items)


# ========== 统一 MES 数据接口（适配器工厂自动切换 Mock ↔ 真实） ==========

@app.get("/api/mes/work-orders", tags=["mes"])
async def mes_get_work_orders(status: str | None = None, search: str | None = None, limit: int = 50) -> list[dict]:
    """获取工单列表。自动使用 OpenMES 或 Mock 适配器。"""
    from app.adapters.factory import get_mes_adapter
    adapter = get_mes_adapter()
    scope: dict[str, Any] = {"limit": limit}
    if status:
        scope["status"] = status
    if search:
        scope["search"] = search
    return await adapter.get_work_orders(scope)


@app.get("/api/mes/work-orders/{work_order_id}/progress", tags=["mes"])
async def mes_get_operation_progress(work_order_id: str) -> list[dict]:
    """获取工序进度。自动使用 OpenMES 或 Mock 适配器。"""
    from app.adapters.factory import get_mes_adapter
    adapter = get_mes_adapter()
    return await adapter.get_operation_progress(work_order_id)


@app.get("/api/mes/quality-records", tags=["mes"])
async def mes_get_quality_records(work_order_id: str | None = None, batch_no: str | None = None) -> list[dict]:
    """获取质量记录。自动使用 OpenMES 或 Mock 适配器。"""
    from app.adapters.factory import get_mes_adapter
    adapter = get_mes_adapter()
    scope: dict[str, Any] = {}
    if work_order_id:
        scope["work_order_id"] = work_order_id
    if batch_no:
        scope["batch_no"] = batch_no
    return await adapter.get_quality_records(scope)


@app.get("/api/mes/production-documents", tags=["mes"])
async def mes_get_production_documents(work_order_id: str) -> list[dict]:
    """获取生产文档（SOP、Control Plan 等）。自动使用 OpenMES 或 Mock 适配器。"""
    from app.adapters.factory import get_mes_adapter
    adapter = get_mes_adapter()
    return await adapter.get_production_documents({"work_order_id": work_order_id})


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
    # HTTP middleware does not run for WebSocket handshakes. Enforce the same
    # Mock-surface boundary here so a real deployment cannot still expose the
    # synthetic event stream by switching protocols.
    from app.runtime.surface import mock_demo_enabled
    if not mock_demo_enabled():
        await websocket.close(code=1008, reason="mock_surface_disabled")
        return
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


# ============================================================================
# 真实订单业务链接口（基于真实 ERPNext / OpenMES 数据）
# 与 /api/scenarios/{name}/run 的 Mock 场景完全分离
# ============================================================================

from pydantic import BaseModel


class RealOrderQuotationRequest(BaseModel):
    customer_id: str
    item_code: str
    quantity: int
    delivery_date: str | None = None


class RealOrderApproveRequest(BaseModel):
    approved: bool
    approved_by: str
    notes: str | None = None


class RealOrderErpDraftRequest(BaseModel):
    draft_type: str  # "quotation" or "sales_order"
    approved_by: str
    approval_id: str


@app.post("/api/real-orders/quotation/analyze", tags=["real-orders"])
async def real_order_analyze_quotation(body: RealOrderQuotationRequest) -> dict:
    """报价 Agent：读取真实 ERP 数据，生成报价方案（含来源证据）。"""
    from app.services.real_order import analyze_quotation
    result = await analyze_quotation(
        customer_id=body.customer_id,
        item_code=body.item_code,
        quantity=body.quantity,
        delivery_date=body.delivery_date,
    )
    return result


@app.get("/api/real-orders/mes/track/{work_order_id}", tags=["real-orders"])
async def real_order_track(work_order_id: str) -> dict:
    """跟单 Agent：读取真实 MES 工单数据，计算 ETA 和风险。"""
    from app.services.real_order import track_order
    result = await track_order(work_order_id)
    return result


@app.get("/api/real-orders/quality/package/{work_order_id}", tags=["real-orders"])
async def real_order_quality_package(work_order_id: str) -> dict:
    """质量文档 Agent：读取真实质量记录，生成质量资料包和门禁判断。"""
    from app.services.real_order import quality_package
    try:
        return await quality_package(work_order_id)
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "openmes_quality_invalid", "message": str(exc)},
        ) from exc


class RealOrderQualityIssueResolutionRequest(BaseModel):
    requested_by: str | None = None
    notes: str | None = None


@app.post("/api/real-orders/quality/issues/{issue_id}/resolution-request", tags=["real-orders"])
async def real_order_request_quality_issue_resolution(
    issue_id: str,
    body: RealOrderQualityIssueResolutionRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """建立质量问题处理审批记录，不写入 OpenMES。"""
    from app.services.real_order import request_quality_issue_resolution
    requested_by = _actor_for_request(body.requested_by, identity, "quality_resolution_request")
    result = request_quality_issue_resolution(issue_id, requested_by, body.notes)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "创建审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


class RealOrderQualityIssueApproveRequest(BaseModel):
    approved_by: str | None = None


@app.post("/api/real-orders/quality/resolution-approvals/{approval_id}/approve", tags=["real-orders"])
async def real_order_approve_quality_issue_resolution(
    approval_id: str,
    body: RealOrderQualityIssueApproveRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """批准质量问题处理请求；此步骤也不写入 OpenMES。"""
    from app.services.real_order import approve_quality_issue_resolution
    approved_by = _actor_for_request(body.approved_by, identity, "quality_resolution_approval")
    result = approve_quality_issue_resolution(approval_id, approved_by)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


class RealOrderQualityIssueResolveRequest(BaseModel):
    work_order_id: str
    resolution_notes: str
    approval_id: str
    approved_by: str | None = None


class RealOrderQualityDispositionRequest(BaseModel):
    work_order_id: str
    requested_by: str | None = None
    disposition: str
    non_conforming_qty: float | None = None
    root_cause: str
    containment_action: str
    nc_source: str | None = None


class RealOrderQualityDispositionWriteRequest(BaseModel):
    work_order_id: str
    approval_id: str
    approved_by: str | None = None


class RealOrderQualityCloseRequest(BaseModel):
    work_order_id: str
    requested_by: str | None = None


class RealOrderQualityCloseWriteRequest(BaseModel):
    work_order_id: str
    approval_id: str
    approved_by: str | None = None


class RealOrderWorkOrderDispatchRequest(BaseModel):
    quotation_id: str
    requested_by: str | None = None


class RealOrderWorkOrderDispatchApproveRequest(BaseModel):
    approved_by: str | None = None


class RealOrderWorkOrderDispatchWriteRequest(BaseModel):
    quotation_id: str
    approval_id: str
    approved_by: str | None = None


# ==================== 工单下达（ERP 草稿 → OpenMES 工单，人工审批门禁） ====================


@app.post("/api/real-orders/work-orders/dispatch-request", tags=["real-orders"])
async def real_order_request_work_order_dispatch(
    body: RealOrderWorkOrderDispatchRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """① 建立"工单下达"审批：只登记将执行内容，不写 OpenMES。"""
    from app.services.real_order import request_work_order_dispatch
    requested_by = _actor_for_request(body.requested_by, identity, "work_order_dispatch_request")
    result = await request_work_order_dispatch(body.quotation_id, requested_by)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "创建工单下达审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/work-orders/dispatch-approvals/{approval_id}/approve", tags=["real-orders"])
async def real_order_approve_work_order_dispatch(
    approval_id: str,
    body: RealOrderWorkOrderDispatchApproveRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """② 人工批准工单下达审批。"""
    from app.services.real_order import approve_work_order_dispatch
    approved_by = _actor_for_request(body.approved_by, identity, "work_order_dispatch_approval")
    result = approve_work_order_dispatch(approval_id, approved_by)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/work-orders/dispatch", tags=["real-orders"])
async def real_order_dispatch_work_order(
    body: RealOrderWorkOrderDispatchWriteRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """③ 审批通过后创建 OpenMES 工单并回读验证（customer_order_no 幂等）。"""
    from app.services.real_order import dispatch_work_order_to_openmes
    approved_by = _actor_for_request(body.approved_by, identity, "work_order_dispatch_write")
    result = await dispatch_work_order_to_openmes(body.quotation_id, body.approval_id, approved_by)
    if not result.get("success") and result.get("written"):
        raise HTTPException(status_code=502, detail=result)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "工单下达失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/quality/issues/{issue_id}/resolve", tags=["real-orders"])
async def real_order_resolve_quality_issue(
    issue_id: str,
    body: RealOrderQualityIssueResolveRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """使用已持久化且已批准的审批记录写入 OpenMES，并回读验证。"""
    from app.services.real_order import resolve_quality_issue
    approved_by = _actor_for_request(body.approved_by, identity, "quality_resolution_write")
    result = await resolve_quality_issue(
        work_order_id=body.work_order_id,
        issue_id=issue_id,
        resolution_notes=body.resolution_notes,
        approval_id=body.approval_id,
        approved_by=approved_by,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    if not result.get("success") and result.get("written"):
        raise HTTPException(status_code=502, detail=result)
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/quality/issues/{issue_id}/disposition-request", tags=["real-orders"])
async def real_order_request_quality_issue_disposition(
    issue_id: str,
    body: RealOrderQualityDispositionRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    """校验并登记 NCR 处置审批，不直接写 OpenMES。"""
    from app.services.real_order import request_quality_issue_disposition
    requested_by = _actor_for_request(body.requested_by, identity, "quality_resolution_request")
    result = request_quality_issue_disposition(
        issue_id,
        body.work_order_id,
        requested_by,
        disposition=body.disposition,
        non_conforming_qty=body.non_conforming_qty,
        root_cause=body.root_cause,
        containment_action=body.containment_action,
        nc_source=body.nc_source,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "创建审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/quality/disposition-approvals/{approval_id}/approve", tags=["real-orders"])
async def real_order_approve_quality_issue_disposition(
    approval_id: str,
    body: RealOrderQualityIssueApproveRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    from app.services.real_order import approve_quality_issue_disposition
    approved_by = _actor_for_request(body.approved_by, identity, "quality_resolution_approval")
    result = approve_quality_issue_disposition(approval_id, approved_by)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/quality/issues/{issue_id}/disposition", tags=["real-orders"])
async def real_order_set_quality_issue_disposition(
    issue_id: str,
    body: RealOrderQualityDispositionWriteRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    from app.services.real_order import set_quality_issue_disposition
    approved_by = _actor_for_request(body.approved_by, identity, "quality_disposition_write")
    result = await set_quality_issue_disposition(
        issue_id, body.approval_id, approved_by, expected_work_order_id=body.work_order_id
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "NCR 处置写回失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.get("/api/real-orders/quality/issues/{issue_id}/closure-check", tags=["real-orders"])
async def real_order_quality_issue_closure_check(issue_id: str, work_order_id: str) -> dict:
    from app.services.real_order import assess_quality_issue_closure
    return await assess_quality_issue_closure(work_order_id, issue_id)


@app.post("/api/real-orders/quality/issues/{issue_id}/close-request", tags=["real-orders"])
async def real_order_request_quality_issue_close(
    issue_id: str,
    body: RealOrderQualityCloseRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    from app.services.real_order import request_quality_issue_close
    requested_by = _actor_for_request(body.requested_by, identity, "quality_resolution_request")
    result = request_quality_issue_close(issue_id, body.work_order_id, requested_by)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "创建审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/quality/close-approvals/{approval_id}/approve", tags=["real-orders"])
async def real_order_approve_quality_issue_close(
    approval_id: str,
    body: RealOrderQualityIssueApproveRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    from app.services.real_order import approve_quality_issue_close
    approved_by = _actor_for_request(body.approved_by, identity, "quality_resolution_approval")
    result = approve_quality_issue_close(approval_id, approved_by)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/quality/issues/{issue_id}/close", tags=["real-orders"])
async def real_order_close_quality_issue(
    issue_id: str,
    body: RealOrderQualityCloseWriteRequest,
    _write_access: bool = Depends(require_real_write_access),
    identity=Depends(require_real_identity),
) -> dict:
    from app.services.real_order import close_quality_issue_with_approval
    approved_by = _actor_for_request(body.approved_by, identity, "quality_close_write")
    result = await close_quality_issue_with_approval(
        issue_id, body.approval_id, approved_by, expected_work_order_id=body.work_order_id
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "NCR 关闭失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.get("/api/real-orders/ship-gate/{work_order_id}", tags=["real-orders"])
async def real_order_ship_gate(
    work_order_id: str,
    quotation_approved: bool = False,
) -> dict:
    """发运门禁：综合质量状态和审批状态，判断是否允许发运。"""
    from app.services.real_order import ship_gate_check
    result = await ship_gate_check(
        work_order_id=work_order_id,
        quotation_approved=quotation_approved,
    )
    return result


@app.get("/api/real-orders/erp/customers/search", tags=["real-orders"])
async def real_order_search_customers(keyword: str = "", limit: int = 20) -> list[dict]:
    """搜索 ERP 客户列表（用于前端选择器）。"""
    from app.adapters.erp.erpnext import ERPNextClient
    from app.integrations.settings import IntegrationSettings
    settings = IntegrationSettings.from_environment()
    client = ERPNextClient(
        settings.erpnext_base_url,
        settings.erpnext_api_key,
        settings.erpnext_api_secret,
    )
    try:
        rows = await client.list_documents(
            "Customer",
            fields=["name", "customer_name", "customer_group", "territory"],
            filters=[["customer_name", "like", f"%{keyword}%"]] if keyword else [],
            limit=min(limit, 50),
        )
        return [
            {
                "customer_id": r.get("name", ""),
                "customer_name": r.get("customer_name", ""),
                "customer_group": r.get("customer_group", ""),
                "territory": r.get("territory", ""),
                "authority": "ERPNext",
            }
            for r in rows
        ]
    finally:
        await client.aclose()


# ==================== 采购 Agent 端点 ====================

class RealOrderProcurementAnalyzeRequest(BaseModel):
    quotation_id: str


class RealOrderProcurementApproveRequest(BaseModel):
    option_id: str
    approved: bool
    approved_by: str | None = None
    notes: str | None = None


class RealOrderPoDraftFromPlanRequest(BaseModel):
    plan_id: str
    approval_id: str
    approved_by: str | None = None


@app.get("/api/real-orders/erp/suppliers/search", tags=["real-orders"])
async def real_order_search_suppliers(keyword: str = "", limit: int = 20) -> list[dict]:
    """搜索 ERP 供应商列表。"""
    from app.adapters.factory import get_erp_adapter
    erp = get_erp_adapter()
    return await erp.search_suppliers(keyword, min(limit, 50))


@app.post("/api/real-orders/procurement/analyze", tags=["real-orders"])
async def real_order_analyze_procurement(body: RealOrderProcurementAnalyzeRequest) -> dict:
    """采购 Agent：计算物料需求并生成多供应商采购方案。"""
    from app.services.real_order import analyze_procurement
    result = await analyze_procurement(quotation_id=body.quotation_id)
    if not result.get("plan_id") and result.get("success") is False:
        raise HTTPException(status_code=400, detail=result.get("error", "采购分析失败"))
    return result


@app.get("/api/real-orders/procurement/plans", tags=["real-orders"])
async def real_order_list_procurement_plans() -> list[dict]:
    """列出所有采购方案。"""
    from app.services.real_order import list_procurement_plans
    return list_procurement_plans()


@app.get("/api/real-orders/procurement/plans/{plan_id}", tags=["real-orders"])
async def real_order_get_procurement_plan(plan_id: str) -> dict:
    """获取单个采购方案详情。"""
    from app.services.real_order import get_procurement_plan
    plan = get_procurement_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"采购方案 {plan_id} 不存在")
    return plan


@app.post("/api/real-orders/procurement/plans/{plan_id}/approve", tags=["real-orders"])
async def real_order_approve_procurement_plan(
    plan_id: str,
    body: RealOrderProcurementApproveRequest,
    identity=Depends(require_real_identity),
) -> dict:
    """审批采购方案（选择供应商方案 + 批准/驳回）。"""
    from app.services.real_order import approve_procurement_plan
    approved_by = _actor_for_request(body.approved_by, identity, "procurement_approval")
    result = await approve_procurement_plan(
        plan_id=plan_id,
        option_id=body.option_id,
        approved=body.approved,
        approved_by=approved_by,
        notes=body.notes,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.post("/api/real-orders/erp/draft/po-from-plan", tags=["real-orders"])
async def real_order_create_po_from_plan(
    body: RealOrderPoDraftFromPlanRequest,
    identity=Depends(require_real_identity),
) -> dict:
    """根据已审批采购方案创建 ERP 采购订单草稿（创建+回读确认）。"""
    from app.services.real_order import create_erp_purchase_order_from_plan
    approved_by = _actor_for_request(body.approved_by, identity, "po_draft_creation")
    result = await create_erp_purchase_order_from_plan(
        plan_id=body.plan_id,
        approval_id=body.approval_id,
        approved_by=approved_by,
    )
    if not result.get("success"):
        error_msg = result.get("error") or result.get("draft", {}).get("error") or "创建失败"
        raise HTTPException(status_code=400, detail=error_msg)
    result["authenticated_identity"] = identity.as_dict()
    return result


class RealOrderQuotationApproveRequest(BaseModel):
    approved: bool
    approved_by: str | None = None
    notes: str | None = None


class RealOrderErpDraftFromQuotationRequest(BaseModel):
    quotation_id: str
    approval_id: str
    approved_by: str | None = None


@app.get("/api/real-orders/identity/me", tags=["real-orders"])
async def real_order_identity(identity=Depends(require_real_identity)) -> dict:
    """返回当前真实身份及业务角色，供页面显示和审批审计使用。"""
    return identity.as_dict()


class RealOrderLoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/real-orders/auth/login", tags=["real-orders"])
async def real_order_auth_login(body: RealOrderLoginRequest) -> dict:
    """用真实 OpenMES 登录契约换取短时会话令牌并回读身份。

    凭据只转发给已配置的 OpenMES 认证端点，不落日志、不持久化。
    刻意不代理 logout/refresh：OpenMES 的 logout/refresh 会吊销或轮换
    该上游用户的全部 token，可能波及服务端集成令牌；本地登出仅清除
    浏览器会话。
    """
    from app.integrations.errors import IntegrationError, IntegrationNotConfigured
    from app.services.session_auth import login_openmes_session

    try:
        return await login_openmes_session(username=body.username, password=body.password)
    except IntegrationNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except IntegrationError as exc:
        if exc.status_code in {401, 403, 422}:
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_credentials", "message": str(exc)},
            ) from exc
        raise _integration_status_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_login_input", "message": str(exc)}) from exc


@app.get("/api/real-orders/quality/todo", tags=["real-orders"])
async def real_order_quality_todo(identity=Depends(require_real_identity)) -> dict:
    """跨工单未关闭质量问题队列（阶段九，只读，MRB 待办视角）。

    身份解析失败（如会话令牌无效）先于数据返回；数据读取失败按集成
    错误映射，不回退空列表冒充"没有待办"。
    """
    from app.services.real_order import quality_todo_list

    try:
        result = await quality_todo_list()
    except IntegrationError as exc:
        raise _integration_status_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "openmes_issues_invalid", "message": str(exc)},
        ) from exc
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.get("/api/real-orders/quality/workflow-states", tags=["real-orders"])
async def real_order_quality_workflow_states(identity=Depends(require_real_identity)) -> dict:
    """按 issue 聚合最近的 NCR 处置/关闭审批（只读，前端刷新后恢复面板进度）。"""
    from app.services.real_order import get_quality_issue_workflow_states

    return {
        "states": get_quality_issue_workflow_states(),
        "authority": "OpenMES",
        "data_source": "real_approvals",
        "authenticated_identity": identity.as_dict(),
    }


@app.get("/api/real-orders/quotations", tags=["real-orders"])
async def real_order_list_quotations() -> list[dict]:
    """列出所有报价分析记录。"""
    from app.services.real_order import list_quotations
    return list_quotations()


@app.get("/api/real-orders/quotations/{quotation_id}", tags=["real-orders"])
async def real_order_get_quotation(quotation_id: str) -> dict:
    """获取单个报价详情。"""
    from app.services.real_order import get_quotation
    q = get_quotation(quotation_id)
    if not q:
        raise HTTPException(status_code=404, detail="报价不存在")
    return q


@app.post("/api/real-orders/quotations/{quotation_id}/approve", tags=["real-orders"])
async def real_order_approve_quotation(
    quotation_id: str,
    body: RealOrderQuotationApproveRequest,
    identity=Depends(require_real_identity),
) -> dict:
    """审批报价（通过/驳回）。审批通过后可用于创建 ERP 草稿。"""
    from app.services.real_order import approve_quotation
    approved_by = _actor_for_request(body.approved_by, identity, "quotation_approval")
    result = await approve_quotation(
        quotation_id=quotation_id,
        approved=body.approved,
        approved_by=approved_by,
        notes=body.notes,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.get("/api/real-orders/approvals", tags=["real-orders"])
async def real_order_list_approvals() -> list[dict]:
    """列出所有审批记录。"""
    from app.services.real_order import list_approvals
    return list_approvals()


@app.post("/api/real-orders/erp/draft/from-quotation", tags=["real-orders"])
async def real_order_create_so_from_quotation(
    body: RealOrderErpDraftFromQuotationRequest,
    identity=Depends(require_real_identity),
) -> dict:
    """根据已审批报价创建 ERP 销售订单草稿（创建+回读确认）。"""
    from app.services.real_order import create_erp_sales_order_from_quotation
    approved_by = _actor_for_request(body.approved_by, identity, "sales_order_draft_creation")
    result = await create_erp_sales_order_from_quotation(
        quotation_id=body.quotation_id,
        approval_id=body.approval_id,
        approved_by=approved_by,
    )
    if not result.get("success"):
        error_msg = result.get("error") or result.get("draft", {}).get("error") or "创建失败"
        raise HTTPException(status_code=400, detail=error_msg)
    result["authenticated_identity"] = identity.as_dict()
    return result


@app.get("/api/real-orders/erp/items/search", tags=["real-orders"])
async def real_order_search_items(keyword: str = "", limit: int = 20) -> list[dict]:
    """搜索 ERP 物料列表（用于前端选择器）。"""
    from app.adapters.erp.erpnext import ERPNextClient
    from app.integrations.settings import IntegrationSettings
    settings = IntegrationSettings.from_environment()
    client = ERPNextClient(
        settings.erpnext_base_url,
        settings.erpnext_api_key,
        settings.erpnext_api_secret,
    )
    try:
        rows = await client.list_documents(
            "Item",
            fields=["name", "item_code", "item_name", "item_group", "stock_uom", "is_stock_item"],
            filters=[["item_name", "like", f"%{keyword}%"]] if keyword else [],
            limit=min(limit, 50),
        )
        return [
            {
                "item_id": r.get("name", ""),
                "item_code": r.get("item_code", ""),
                "item_name": r.get("item_name", ""),
                "item_group": r.get("item_group", ""),
                "stock_uom": r.get("stock_uom", ""),
                "is_stock_item": r.get("is_stock_item", 0),
                "authority": "ERPNext",
            }
            for r in rows
        ]
    finally:
        await client.aclose()


# ============================================================================
# 真实 ERP 订单 ↔ 真实 MES 工单关联（只读）
# 正式关联仅按 OpenMES work_orders.customer_order_no == ERP 销售订单号精确匹配，
# 找不到时明确返回"未建立关联"，不用其他工单代替，不回退 Mock。
# ============================================================================

@app.get("/api/real-orders/agent-runs", tags=["real-orders"])
async def real_order_agent_runs(agent_type: str = "", limit: int = 50) -> list[dict]:
    """查询 Agent 运行记录（输入/状态摘要；完整结果按 run_id 查询）。"""
    from app.services.real_order import list_agent_runs
    return list_agent_runs(agent_type=agent_type, limit=min(limit, 200))


@app.get("/api/real-orders/agent-runs/{run_id}", tags=["real-orders"])
async def real_order_agent_run_detail(run_id: str) -> dict:
    """查询单次 Agent 运行的完整记录（含决策依据与所用 ERP/MES 证据）。"""
    from app.services.real_order import get_agent_run
    result = get_agent_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"运行记录不存在: {run_id}")
    return result


@app.post("/api/real-orders/assistant/ask", tags=["real-orders"])
async def real_order_assistant_ask(body: dict) -> dict:
    """协调智能体：自然语言业务问题 → 动态调用四个真实智能体（AIP）→ 汇总回答。

    只读通道：协调者仅能调用查询类技能，不执行任何 ERP/MES 写操作。
    DeepSeek 未配置时返回 503，不伪造回答。
    """
    question = str((body or {}).get("question", "")).strip()
    context = (body or {}).get("context") or {}
    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")

    from app.integrations.errors import IntegrationNotConfigured
    from app.services.coordinator import build_coordinator

    try:
        coordinator = build_coordinator()
    except IntegrationNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "llm_not_configured",
                "message": f"协调智能体不可用：{exc}。不使用固定话术伪造回答。",
            },
        )
    try:
        return await coordinator.ask(question, context)
    except IntegrationError as exc:
        raise _integration_status_error(exc)
    finally:
        await coordinator.aclose()


@app.post("/api/real-orders/assistant/ask/stream", tags=["real-orders"])
async def real_order_assistant_ask_stream(body: dict):
    """协调智能体流式版（SSE）：每完成一次智能体工具调用推送一条 step 事件，
    回答完成后推送 done（完整结果，结构与同步端点一致）或 error。

    只读通道与同步端点完全相同（同一个 ask()，额外透传 on_step 回调）；
    前端在网络异常或事件流中断时应回退同步端点。
    """
    import json as _json

    from fastapi.responses import StreamingResponse

    from app.integrations.errors import IntegrationNotConfigured
    from app.services.coordinator import build_coordinator

    question = str((body or {}).get("question", "")).strip()
    context = (body or {}).get("context") or {}
    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")

    try:
        coordinator = build_coordinator()
    except IntegrationNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "llm_not_configured",
                "message": f"协调智能体不可用：{exc}。不使用固定话术伪造回答。",
            },
        )

    queue: asyncio.Queue = asyncio.Queue()

    async def _run() -> None:
        try:
            result = await coordinator.ask(
                question,
                context,
                on_step=lambda step: queue.put_nowait(("step", step)),
            )
            queue.put_nowait(("done", result))
        except IntegrationError as exc:
            queue.put_nowait(("error", {"message": str(exc), "kind": "integration_error"}))
        except Exception as exc:  # 任何失败都如实推送，不让事件流静默挂死
            queue.put_nowait(("error", {"message": f"{type(exc).__name__}: {exc}", "kind": "internal_error"}))
        finally:
            await coordinator.aclose()
            queue.put_nowait(None)

    task = asyncio.create_task(_run())

    async def _event_stream():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 心跳注释行，防止代理在长工具调用期间断开
                    continue
                if item is None:
                    break
                kind, payload = item
                data = _json.dumps(payload, ensure_ascii=False, default=str)
                yield f"event: {kind}\ndata: {data}\n\n"
        finally:
            task.cancel()  # 客户端断开时中止仍在运行的协调任务

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/real-orders/erp/sales-orders", tags=["real-orders"])
async def real_order_list_sales_orders(limit: int = 50) -> dict:
    """列出真实 ERP 销售订单（供用户选择）。连接失败时明确报错。"""
    from app.integrations.errors import IntegrationError
    from app.services.order_linkage import list_real_sales_orders
    try:
        return await list_real_sales_orders(limit=min(limit, 100))
    except IntegrationError as e:
        raise HTTPException(status_code=502, detail=f"ERP 连接失败: {e}") from e


@app.get("/api/real-orders/erp/sales-orders/{order_id}/mes-link", tags=["real-orders"])
async def real_order_mes_link(order_id: str) -> dict:
    """查询真实 ERP 销售订单与真实 MES 工单的正式关联（只读，含证据）。"""
    from app.integrations.errors import IntegrationError
    from app.services.order_linkage import get_order_mes_link
    try:
        result = await get_order_mes_link(order_id)
    except IntegrationError as e:
        raise HTTPException(
            status_code=502,
            detail=f"ERP/MES 连接失败，无法判断订单关联: {e}",
        ) from e
    if result.get("status") == "ERP_ORDER_NOT_FOUND":
        raise HTTPException(status_code=404, detail=result.get("message", "ERP 订单不存在"))
    return result
