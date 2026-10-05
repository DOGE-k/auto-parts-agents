from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    customer_id: Mapped[str] = mapped_column(String(120))
    product_id: Mapped[str] = mapped_column(String(120))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(80))
    data_source: Mapped[str] = mapped_column(String(40), default="synthetic_demo_only")
    scenario: Mapped[str] = mapped_column(String(80), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentCaseRow(Base):
    __tablename__ = "agent_cases"

    case_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    agent_type: Mapped[str] = mapped_column(String(40), index=True)
    operational_state: Mapped[str] = mapped_column(String(40))
    business_state: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer, default=1)
    objective: Mapped[str] = mapped_column(Text)


class BusinessEventRow(Base):
    __tablename__ = "business_events"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_business_event_idempotency"),)

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_agent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_agent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    object_type: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(100), index=True)
    object_version: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(180))


class InboxRow(Base):
    __tablename__ = "inbox"
    __table_args__ = (Index("ix_inbox_status", "status"),)

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTaskRow(Base):
    __tablename__ = "agent_tasks"

    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("agent_cases.case_id", ondelete="CASCADE"), index=True)
    aip_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capability_id: Mapped[str] = mapped_column(String(120))
    protocol_role: Mapped[str] = mapped_column(String(20))
    input_snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(100), index=True)


class PlanRow(Base):
    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("agent_cases.case_id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(64))
    assumptions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("agent_cases.case_id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(100), index=True)
    object_version: Mapped[int] = mapped_column(Integer)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), index=True, default="pending")
    approver: Mapped[str | None] = mapped_column(String(120), nullable=True)
    action_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    source_system: Mapped[str] = mapped_column(String(80))
    source_type: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[str] = mapped_column(String(120))
    source_version: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AuditRecordRow(Base):
    __tablename__ = "audit_records"

    audit_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    trace_id: Mapped[str] = mapped_column(String(100), index=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(40))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"

    tool_call_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.plan_id", ondelete="SET NULL"), nullable=True)
    tool_id: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(80))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OutboxRow(Base):
    __tablename__ = "outbox"

    outbox_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("business_events.event_id", ondelete="CASCADE"), unique=True)
    topic: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ReplayRunRow(Base):
    __tablename__ = "replay_runs"

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(80))
    seed: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(40), default="replay")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str] = mapped_column(String(40), default="running")


class IdempotencyRow(Base):
    __tablename__ = "request_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(180), primary_key=True)
    operation: Mapped[str] = mapped_column(String(120))
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# ============================================================================
# 真实业务链状态（报价 / 采购方案 / 审批记录）—— 持久化，重启不丢失
# 与上面 Mock 场景系统的表分开；完整业务快照以 JSON 存储。
# ============================================================================

class RealQuotationRow(Base):
    __tablename__ = "real_quotations"

    quotation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    adapter_mode: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[str] = mapped_column(String(120), default="")
    item_code: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RealProcurementPlanRow(Base):
    __tablename__ = "real_procurement_plans"

    plan_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    quotation_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    adapter_mode: Mapped[str] = mapped_column(String(20))
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RealApprovalRow(Base):
    __tablename__ = "real_approvals"

    approval_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str] = mapped_column(String(120))
    reference_type: Mapped[str] = mapped_column(String(40), index=True)
    reference_id: Mapped[str] = mapped_column(String(100), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RealAgentRunRow(Base):
    """Agent 运行记录：每次真实 Agent 调用的输入/依据/结果/错误，可追溯。"""

    __tablename__ = "real_agent_runs"

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    agent_type: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    result_status: Mapped[str] = mapped_column(String(20), index=True, default="ok")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 会话关联与保留期：审批/写入/回读/幂等运行记录 expires_at 为 NULL（永久）；
    # 协调者原始问答（含中间工具参数）按 ASSISTANT_RETENTION_DAYS 过期清理。
    session_id: Mapped[str | None] = mapped_column(String(60), index=True, nullable=True)
    business_task_id: Mapped[str | None] = mapped_column(String(60), index=True, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)


class AssistantSessionRow(Base):
    """协同问答会话：一次连续对话的锚点（元数据；原始消息按保留期清理）。"""

    __tablename__ = "assistant_sessions"

    session_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    business_task_id: Mapped[str | None] = mapped_column(String(60), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantMessageRow(Base):
    """协同问答原始消息（用户/助手原文），按 ASSISTANT_RETENTION_DAYS 过期清理。"""

    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(60), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)


class BusinessTaskRow(Base):
    """业务任务：跨轮次的业务上下文与状态。

    entity_context/active_plan/summary 与关联记录（报价/方案/审批/ERP/MES
    单号）属于业务任务的最终摘要与状态，永久保留；凭据/令牌按铁律从不
    进入本表或任何会话持久化内容。
    """

    __tablename__ = "business_tasks"

    task_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE", index=True)
    entity_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    stale_downstream: Mapped[list[Any]] = mapped_column(JSON, default=list)
    selected_option_id: Mapped[str] = mapped_column(String(40), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CollaborationEventRow(Base):
    """跨智能体协同事件（P1）：事件编号 + 去重键 + 失败记录 + 重试上限 + 人工接管。

    事件由已批准的业务写入（如质量问题登记）触发，协同本身只读真实
    ERP/MES；绝不代表已执行的写入，也不绕过任何审批门禁。
    """

    __tablename__ = "collaboration_events"

    event_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True, default="PENDING")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    taken_over_by: Mapped[str] = mapped_column(String(120), default="")
    taken_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectUserRow(Base):
    """项目自身的人工用户，不与 ERPNext/OpenMES 服务账号混用。"""

    __tablename__ = "project_users"
    __table_args__ = (UniqueConstraint("username", name="uq_project_users_username"),)

    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    username: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(300))
    roles_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProjectSessionRow(Base):
    """项目登录会话；仅保存令牌摘要，不保存浏览器会话原文。"""

    __tablename__ = "project_sessions"

    session_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("project_users.user_id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
