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
