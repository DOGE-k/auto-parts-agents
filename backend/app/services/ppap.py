"""PPAP 编制服务层（第四岗：质量文档智能体）。

职责边界（设计说明 §2/§7/§8.2/§9，清单 §1.2）：

- 只写项目本地 ``ppap_*`` 表与受控存储目录（``backend/data/ppap_store``），
  **不写 ERP/MES**；ERP/MES 的真实写入仍走既有 8 步人工审批门禁。
- 取证只调用《PPAP阶段A确认清单》§1.2 已核对的只读接口，不发明字段与接口。
- 读取结果三态分离：``OK`` / ``EMPTY``（接口成功但该来源没有数据）/ ``ERROR``
  （接口失败或不可达）。"接口返回 0 条"不等于认证失败，接口失败也不等于
  "无此项"，两者都不允许被当成证据使用。
- 编制内容全部来自真实来源或已登录用户确认的输入：缺数据一律标待补，
  不填默认值、不编造测量值/评分/控制方法、不生成签字与合格结论。
- 判定只在 ``acceptance_rules.json`` 已配置且经人工确认时给出；未配置时一律
  "规则未确认"，公开参考区值只作参考、不作判定依据。
- 审计轨迹：``AuditRecordRow`` 绑定 ``projects.project_id``，PPAP 任务没有
  project 归属，因此**不写审计表**；审计由 ``ppap_compile_runs.call_chain_json``
  、``ppap_evidence``、``ppap_reviews`` 与 ``task.coverage_json["audit_trail"]``
  共同承担（设计说明 §7 的可追溯要求）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from app.persistence.database import SessionLocal
from app.persistence.models import (
    PpapArtifactRow,
    PpapCompileRunRow,
    PpapElementRow,
    PpapEvidenceRow,
    PpapInputRow,
    PpapIssueRow,
    PpapReviewRow,
    PpapTaskRow,
    utc_now,
)
from app.services import (
    ppap_calc,
    ppap_checks,
    ppap_assistance,
    ppap_characteristics,
    ppap_export,
    ppap_inputs,
    ppap_mode,
    ppap_storage,
    ppap_template,
    real_order,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 状态常量（设计说明 §8.1：四类状态分开，不与既有质量流程混用）
# ----------------------------------------------------------------------

RUN_NOT_STARTED = "NOT_STARTED"
RUN_RUNNING = "RUNNING"
RUN_READY = "READY"
RUN_COMPILED = "COMPILED"
RUN_BLOCKED = "BLOCKED"
RUN_FAILED = "FAILED"
RUN_STATES = (RUN_NOT_STARTED, RUN_RUNNING, RUN_READY, RUN_COMPILED, RUN_BLOCKED, RUN_FAILED)

PKG_NOT_SUBMITTED = "NOT_SUBMITTED"
PKG_PENDING_REVIEW = "PENDING_REVIEW"
PKG_APPROVED = "APPROVED"
PKG_RETURNED = "RETURNED"
PKG_ARCHIVED = "ARCHIVED"
PACKAGE_STATES = (
    PKG_NOT_SUBMITTED,
    PKG_PENDING_REVIEW,
    PKG_APPROVED,
    PKG_RETURNED,
    PKG_ARCHIVED,
)

CUST_NOT_SUBMITTED = "NOT_SUBMITTED"
CUST_SUBMITTED = "SUBMITTED"
CUST_ACCEPTED = "ACCEPTED"
CUST_REJECTED = "REJECTED"
CUSTOMER_STATES = (CUST_NOT_SUBMITTED, CUST_SUBMITTED, CUST_ACCEPTED, CUST_REJECTED)

ART_DRAFT = "DRAFT"
ART_APPROVED = "APPROVED"
ART_ARCHIVED = "ARCHIVED"
ART_SUPERSEDED = "SUPERSEDED"
ARTIFACT_STATES = (ART_DRAFT, ART_APPROVED, ART_ARCHIVED, ART_SUPERSEDED)

REVIEW_SUBMITTED = "SUBMITTED"
REVIEW_APPROVED = "APPROVED"
REVIEW_RETURNED = "RETURNED"
REVIEW_DECISIONS = (REVIEW_APPROVED, REVIEW_RETURNED)

ISSUE_OPEN = "OPEN"
ISSUE_RESOLVED = "RESOLVED"
ISSUE_WAIVED = "WAIVED"

# 证据读取三态
READ_OK = "OK"
READ_EMPTY = "EMPTY"
READ_ERROR = "ERROR"

SCOPE_PACKAGE = "PACKAGE"

# 任务业务对象里允许写入的键（只用这些规范键，不做别名推断）
BUSINESS_OBJECT_KEYS = (
    "work_order_no",
    "work_order_id",
    "quotation_id",
    "erp_order_id",
    "issue_id",
    "quotation_approved",
    "work_order_batch_no",
    "design_responsibility",
    "customer_spec_reference",
)

# 可直接修改的任务列字段
TASK_FIELD_KEYS = (
    "customer_id",
    "customer_name",
    "part_no",
    "part_name",
    "part_side",
    "drawing_version",
    "engineering_change_level",
    "submission_reason",
    "submission_level",
    "owner",
    "reviewer",
    "due_date",
)

HUMAN_EVIDENCE_TYPES = (
    ppap_template.EV_USER_UPLOAD,
    ppap_template.EV_INTERNAL_PACKAGE,
)

AUDIT_KEY = "audit_trail"
COMPILATION_ENGINE_VERSION = "ppap-compile-2026.10.14-2"


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------


def _gen_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid4().hex[:6]}"


def _now() -> datetime:
    return utc_now()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    return "" if value is None else str(value)


def _jsonable(value: Any) -> Any:
    """归一化为数据库 JSON 列可安全存储的结构（时间等按 ISO 文本）。"""
    return json.loads(ppap_storage.canonical_json(value))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_limit(limit: Any, *, maximum: int = 200, default: int = 50) -> int:
    number = _int(limit, default)
    if number <= 0:
        number = default
    return max(1, min(number, maximum))


def _append_audit(coverage: Mapping[str, Any] | None, entry: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(coverage or {})
    trail = list(data.get(AUDIT_KEY) or [])
    trail.append({**dict(entry), "at": ppap_storage.now_iso()})
    data[AUDIT_KEY] = trail[-200:]
    return data


# ----------------------------------------------------------------------
# ORM → dict
# ----------------------------------------------------------------------


def _task_dict(row: PpapTaskRow) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "data_mode": ppap_mode.task_mode(row),
        "customer_id": row.customer_id,
        "customer_name": row.customer_name,
        "part_no": row.part_no,
        "part_name": row.part_name,
        "part_side": row.part_side,
        "drawing_version": row.drawing_version,
        "engineering_change_level": row.engineering_change_level,
        "submission_reason": row.submission_reason,
        "submission_level": row.submission_level,
        "template_id": row.template_id,
        "template_version": row.template_version,
        "template_source": row.template_source,
        "owner": row.owner,
        "reviewer": row.reviewer,
        "due_date": row.due_date,
        "run_state": row.run_state,
        "package_state": row.package_state,
        "customer_state": row.customer_state,
        "business_object": dict(row.business_object_json or {}),
        "coverage": dict(row.coverage_json or {}),
        "input_snapshot_hash": row.input_snapshot_hash or "",
        "authorized_by": row.authorized_by or "",
        "authorization_ref": row.authorization_ref or "",
        "state_note": row.state_note or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _element_spec_fields(element_id: str) -> dict[str, Any]:
    spec = ppap_template.element_spec(element_id)
    if spec is None:
        return {}
    return {
        "title": spec.title,
        "sheet_name": spec.sheet_name,
        "category": spec.category,
        "suggested_requirement": spec.suggested_requirement,
        "side_sensitive": spec.side_sensitive,
        "generates_content": spec.generates_content,
        "requires_engineering_input": spec.requires_engineering_input,
        "responsible_role": spec.responsible_role,
        "required_inputs": list(spec.required_inputs),
        "evidence_types": list(spec.evidence_types),
        "applicability_condition": spec.applicability_condition,
        "notes": spec.notes,
        "blocks_approval_when_unmet": spec.blocks_approval_when_unmet,
    }


def _element_dict(row: PpapElementRow, *, include_content: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "element_id": row.element_id,
        "category": row.category,
        "requirement": row.requirement,
        "applicability_basis": row.applicability_basis or "",
        "title": row.title or "",
        "compilation_method": row.compilation_method or "",
        "status": row.status,
        "status_reason": row.status_reason or "",
        "responsible_role": ppap_template.element_spec(row.element_id).responsible_role
        if ppap_template.element_spec(row.element_id)
        else "",
        "source_refs": dict(row.source_refs_json or {}),
        "check_result": dict(row.check_result_json or {}),
        "drafted_at": _iso(row.drafted_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_content:
        data["content"] = dict(row.content_json or {})
    data.update(_element_spec_fields(row.element_id))
    return data


def _evidence_dict(row: PpapEvidenceRow, *, include_payload: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "evidence_id": row.evidence_id,
        "task_id": row.task_id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "source_version": row.source_version or "",
        "locator": row.locator or "",
        "summary": row.summary or "",
        "read_status": row.read_status,
        "truncated": bool(row.truncated),
        "content_hash": row.content_hash or "",
        "review_state": row.review_state,
        "element_ids": list(row.element_ids_json or []),
        "collected_by": row.collected_by or "",
        "collected_at": _iso(row.collected_at),
        "dedup_key": row.dedup_key or "",
        "error": dict(row.error_json or {}) if row.error_json else None,
    }
    if include_payload:
        data["payload"] = dict(row.payload_json or {})
    return data


def _input_dict(row: PpapInputRow) -> dict[str, Any]:
    return {
        "input_id": row.input_id,
        "task_id": row.task_id,
        "dataset": row.dataset,
        "record_key": row.record_key,
        "part_side": row.part_side,
        "element_id": row.element_id or "",
        "payload": dict(row.payload_json or {}),
        "source_type": row.source_type,
        "source_ref": row.source_ref or "",
        "confirmed_by": row.confirmed_by or "",
        "confirmed_at": _iso(row.confirmed_at),
        "updated_at": _iso(row.updated_at),
    }


def _issue_dict(row: PpapIssueRow) -> dict[str, Any]:
    return {
        "issue_id": row.issue_id,
        "task_id": row.task_id,
        "dedup_key": row.dedup_key,
        "kind": row.kind,
        "severity": row.severity,
        "check_id": row.check_id or "",
        "element_id": row.element_id or "",
        "field_path": row.field_path or "",
        "title": row.title,
        "detail": row.detail or "",
        "basis": row.basis or "",
        "required_evidence": row.required_evidence or "",
        "responsible_role": row.responsible_role or "",
        "status": row.status,
        "blocks_approval": bool(row.blocks_approval),
        "resolution_note": row.resolution_note or "",
        "resolved_by": row.resolved_by or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "resolved_at": _iso(row.resolved_at),
    }


def _run_dict(row: PpapCompileRunRow, *, include_steps: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "run_id": row.run_id,
        "task_id": row.task_id,
        "trigger": row.trigger,
        "request_key": row.request_key or "",
        "input_snapshot_hash": row.input_snapshot_hash or "",
        "template_id": row.template_id or "",
        "template_version": row.template_version or "",
        "rule_version": row.rule_version or "",
        "status": row.status,
        "summary": dict(row.summary_json or {}),
        "error": dict(row.error_json or {}) if row.error_json else None,
        "idempotent_replay": bool(row.idempotent_replay),
        "requested_by": row.requested_by or "",
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }
    if include_steps:
        data["steps"] = list(row.steps_json or [])
        data["call_chain"] = list(row.call_chain_json or [])
    return data


def _artifact_dict(row: PpapArtifactRow) -> dict[str, Any]:
    return {
        "artifact_id": row.artifact_id,
        "task_id": row.task_id,
        "run_id": row.run_id or "",
        "scope": row.scope,
        "element_id": row.element_id or "",
        "version": int(row.version or 0),
        "kind": row.kind,
        "file_name": row.file_name or "",
        "status": row.status,
        "content_hash": row.content_hash or "",
        "byte_size": int(row.byte_size or 0),
        "input_snapshot_hash": row.input_snapshot_hash or "",
        "template_id": row.template_id or "",
        "template_version": row.template_version or "",
        "issue_count": dict(row.issue_count_json or {}),
        "created_by": row.created_by or "",
        "created_at": _iso(row.created_at),
        "frozen_at": _iso(row.frozen_at),
    }


def _review_dict(row: PpapReviewRow) -> dict[str, Any]:
    return {
        "review_id": row.review_id,
        "task_id": row.task_id,
        "artifact_id": row.artifact_id or "",
        "artifact_version": int(row.artifact_version or 0),
        "input_snapshot_hash": row.input_snapshot_hash or "",
        "decision": row.decision,
        "reviewer": row.reviewer or "",
        "reviewer_role": row.reviewer_role or "",
        "author": row.author or "",
        "separation_warning": row.separation_warning or "",
        "comments": row.comments or "",
        "blockers": list(row.blocker_summary_json or []),
        "created_at": _iso(row.created_at),
    }


# ----------------------------------------------------------------------
# 读取上下文加载（供检查器与编制共用）
# ----------------------------------------------------------------------


def _load_task_rows(session: Any, task_id: str) -> tuple[PpapTaskRow | None, list[PpapElementRow]]:
    task = session.get(PpapTaskRow, task_id)
    if task is None:
        return None, []
    elements = (
        session.query(PpapElementRow)
        .filter(PpapElementRow.task_id == task_id)
        .order_by(PpapElementRow.element_id)
        .all()
    )
    return task, elements


def _load_input_rows(session: Any, task_id: str) -> list[PpapInputRow]:
    return (
        session.query(PpapInputRow)
        .filter(PpapInputRow.task_id == task_id)
        .order_by(PpapInputRow.dataset, PpapInputRow.record_key, PpapInputRow.part_side)
        .all()
    )


def _load_evidence_rows(session: Any, task_id: str) -> list[PpapEvidenceRow]:
    return (
        session.query(PpapEvidenceRow)
        .filter(PpapEvidenceRow.task_id == task_id)
        .order_by(PpapEvidenceRow.source_type, PpapEvidenceRow.source_id)
        .all()
    )


def _load_issue_rows(session: Any, task_id: str) -> list[PpapIssueRow]:
    return (
        session.query(PpapIssueRow)
        .filter(PpapIssueRow.task_id == task_id)
        .order_by(PpapIssueRow.severity, PpapIssueRow.check_id)
        .all()
    )


def _load_artifact_rows(session: Any, task_id: str) -> list[PpapArtifactRow]:
    return (
        session.query(PpapArtifactRow)
        .filter(PpapArtifactRow.task_id == task_id)
        .order_by(PpapArtifactRow.version, PpapArtifactRow.created_at)
        .all()
    )


def _build_checks_context(
    task_row: PpapTaskRow,
    element_rows: Sequence[PpapElementRow],
    input_rows: Sequence[PpapInputRow],
    evidence_rows: Sequence[PpapEvidenceRow],
    rules: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "task": _task_dict(task_row),
        "elements": [_element_dict(row) for row in element_rows],
        "inputs": [_input_dict(row) for row in input_rows],
        "evidence": [_evidence_dict(row, include_payload=True) for row in evidence_rows],
        "rules": dict(rules or {}),
        "template": ppap_template.template_descriptor(),
    }


# ----------------------------------------------------------------------
# 取证计划与来源类型分类
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Collector:
    collector_id: str
    source_type: str
    label: str
    requires: tuple[str, ...] = ()
    parent: str = ""
    slice_key: str = ""
    description: str = ""


COLLECTORS: tuple[Collector, ...] = (
    Collector(
        "openmes.work_order_lookup",
        ppap_template.EV_OPENMES_WORK_ORDER,
        "MES 工单编号精确查找",
        requires=("work_order_no",),
        description="按工单编号精确匹配 MES 工单，取得数字 work_order_id（不推断、不模糊匹配）。",
    ),
    Collector(
        "openmes.track_order",
        ppap_template.EV_OPENMES_WORK_ORDER,
        "MES 工单生产状态",
        requires=("work_order_id",),
        description="读取工单数量/进度/交期与风险；MES 不可达时如实报错，不当作“工单不存在”。",
    ),
    Collector(
        "openmes.quality_package",
        ppap_template.EV_OPENMES_QUALITY_PACKAGE,
        "MES 质量资料包",
        requires=("work_order_id",),
        description="读取工单质量记录、工程文档与检验记录，并如实标注 OpenMES 不支持的能力。",
    ),
    Collector(
        "openmes.engineering_documents",
        ppap_template.EV_OPENMES_ENGINEERING_DOC,
        "MES 工程文档（SOP/控制计划类载体）",
        parent="openmes.quality_package",
        slice_key="documents",
        description="取自质量资料包中的工程文档列表；空列表表示该工单没有冻结工程文档，不等于读取失败。",
    ),
    Collector(
        "openmes.inspections",
        ppap_template.EV_OPENMES_INSPECTION,
        "MES 检验记录（按工单批次 lot）",
        parent="openmes.quality_package",
        slice_key="inspections",
        description="取自质量资料包中按工单批次过滤后的检验记录；0 条表示当前没有可用的检验记录。",
    ),
    Collector(
        "erp.quotation",
        ppap_template.EV_ERP_SALES_ORDER,
        "报价/订单记录（按报价编号）",
        requires=("quotation_id",),
        description="读取本地保存的真实报价记录（数据源 ERPNext）。",
    ),
    Collector(
        "erp.quotation_by_order",
        ppap_template.EV_ERP_SALES_ORDER,
        "报价/订单记录（按 ERP 订单号）",
        requires=("erp_order_id",),
        description="按 ERP 销售订单号查找已保存的真实关联报价，不做关联推断。",
    ),
    Collector(
        "procurement.plan",
        ppap_template.EV_PROCUREMENT_PLAN,
        "采购方案（按报价编号）",
        requires=("quotation_id",),
        description="读取该报价最新的真实采购方案与审批/PO 草稿状态。",
    ),
    Collector(
        "quality.issue_queue",
        ppap_template.EV_QUALITY_ISSUE_WORKFLOW,
        "未关闭质量问题队列",
        description="跨工单读取 OpenMES 未关闭质量问题；任一状态读取失败即报错，不回退空列表。",
    ),
    Collector(
        "quality.issue_workflow",
        ppap_template.EV_QUALITY_ISSUE_WORKFLOW,
        "质量问题处置/关闭审批状态",
        description="本地审批库中最近的质量问题处置与关闭审批记录。",
    ),
    Collector(
        "quality.issue_closure",
        ppap_template.EV_QUALITY_ISSUE_WORKFLOW,
        "指定质量问题的关闭前置条件",
        requires=("work_order_id", "issue_id"),
        description="读取指定 NCR 的处置动作与关闭前置条件（只读，不写入）。",
    ),
    Collector(
        "shipping.gate",
        ppap_template.EV_SHIP_GATE,
        "发运门禁联检",
        requires=("work_order_id",),
        description="综合质量门禁与生产进度判断是否可发运；报价审批状态只取业务对象显式值，不推断。",
    ),
)

COLLECTOR_BY_ID: dict[str, Collector] = {c.collector_id: c for c in COLLECTORS}
DEFAULT_SOURCE_TYPES: tuple[str, ...] = tuple(
    sorted({c.source_type for c in COLLECTORS})
)

CONTEXT_KEYS = ("work_order_no", "work_order_id", "quotation_id", "erp_order_id", "issue_id", "quotation_approved")


def _validate_source_types(source_types: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """把请求的来源类型归一化为可取证列表，并显式报告不可自动取证的类型。"""
    requested = [_text(item) for item in _as_list(source_types) if _text(item)]
    if not requested:
        requested = list(DEFAULT_SOURCE_TYPES)
    known = set(ppap_template.EVIDENCE_SOURCE_TYPES)
    usable: list[str] = []
    rejected: list[dict[str, Any]] = []
    for source_type in requested:
        if source_type not in known:
            rejected.append({"source_type": source_type, "reason": "unknown_source_type"})
            continue
        if source_type in HUMAN_EVIDENCE_TYPES:
            rejected.append(
                {
                    "source_type": source_type,
                    "reason": "needs_human_registration",
                    "message": "该来源必须由已登录人员登记原件（受控导入），系统不自动取证。",
                }
            )
            continue
        if source_type not in DEFAULT_SOURCE_TYPES:
            rejected.append(
                {
                    "source_type": source_type,
                    "reason": "not_readable",
                    "message": "该来源类型当前没有已核对的只读接口，不自动取证。",
                }
            )
            continue
        if source_type not in usable:
            usable.append(source_type)
    return usable, rejected


def _context_from_business(business: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(business or {})
    approved = data.get("quotation_approved")
    return {
        "work_order_no": _text(data.get("work_order_no")),
        "work_order_id": _text(data.get("work_order_id")),
        "quotation_id": _text(data.get("quotation_id")),
        "erp_order_id": _text(data.get("erp_order_id")),
        "issue_id": _text(data.get("issue_id")),
        "quotation_approved": approved if isinstance(approved, bool) else None,
    }


def plan_evidence(task_id: str) -> dict[str, Any]:
    """取证计划（只读）：哪些来源可用、缺哪些业务对象键、哪些必须人工登记。"""
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return {"ok": False, "error": "task_not_found", "task_id": task_id}
        business = dict(task.business_object_json or {})
        context = _context_from_business(business)

    entries: list[dict[str, Any]] = []
    has_work_order_id = bool(context.get("work_order_id"))
    has_work_order_no = bool(context.get("work_order_no"))
    for collector in COLLECTORS:
        note = ""
        if collector.collector_id == "openmes.work_order_lookup":
            if has_work_order_id:
                missing, resolvable = [], False
                note = "任务已登记工单 id，无需再按编号反查。"
            elif has_work_order_no:
                missing, resolvable = [], True
            else:
                missing, resolvable = ["work_order_no"], False
                note = "缺少工单编号，无法反查工单 id。"
        else:
            missing = [key for key in collector.requires if not context.get(key)]
            resolvable = not missing
            if missing == ["work_order_id"] and has_work_order_no:
                # 工单 id 由只读接口按工单编号解析后使用；解析失败则该来源按读取失败记录，
                # 不猜测 id、不用其它工单顶替。
                missing, resolvable = [], True
                note = "work_order_id 先由工单编号只读解析；解析失败则本条按读取失败记录。"
        entries.append(
            {
                "collector_id": collector.collector_id,
                "source_type": collector.source_type,
                "label": collector.label,
                "requires": list(collector.requires),
                "missing_business_keys": missing,
                "resolvable": resolvable,
                "note": note,
                "description": collector.description,
            }
        )

    element_needs = _element_evidence_needs()
    return {
        "ok": True,
        "task_id": task_id,
        "business_object": business,
        "context": context,
        "collectors": entries,
        "human_registration_required": [
            {
                "source_type": source_type,
                "elements": [spec["element_id"] for spec in element_needs.get(source_type, [])],
                "message": "需由已登录人员登记原件或登记引用的既有批准包，系统不自动取证。",
            }
            for source_type in HUMAN_EVIDENCE_TYPES
            if element_needs.get(source_type)
        ],
        "element_evidence_needs": element_needs,
        "notice": "取证只调用清单 §1.2 已核对的只读接口；缺失业务对象键时该来源报 skip，不猜测。",
    }


def _element_evidence_needs() -> dict[str, list[dict[str, str]]]:
    needs: dict[str, list[dict[str, str]]] = {}
    for spec in ppap_template.element_specs():
        for source_type in spec.evidence_types:
            needs.setdefault(source_type, []).append(
                {"element_id": spec.element_id, "title": spec.title, "role": spec.responsible_role}
            )
    return needs


def _truncated_for(collector_id: str, payload: Mapping[str, Any]) -> bool:
    """分页/截断判定：只在确实施加了窗口且窗口已满时置位，避免误报。"""
    list_window = 50
    approval_window = 200
    if collector_id == "openmes.quality_package":
        inspections = _as_list(payload.get("inspections"))
        return len(inspections) >= list_window
    if collector_id == "openmes.inspections":
        # 只按本来源实际切出的条目数判断。gate_details.inspections_total 是全局计数，
        # 与按工单批次 lot 关联后的条目数不同量纲，用它判定会长期误报截断。
        return len(_as_list(payload.get("__items"))) >= list_window
    if collector_id == "quality.issue_workflow":
        return len(_as_dict(payload)) >= approval_window
    return False


def _classify_read(collector_id: str, payload: Any) -> tuple[str, str, dict[str, Any] | None]:
    """把一次真实读取结果分成 OK / EMPTY / ERROR 三态，并给出可追溯摘要。"""
    if payload is None:
        return READ_ERROR, "读取返回空结果对象（None），按读取失败处理。", {"code": "empty_result"}
    if not isinstance(payload, Mapping):
        return READ_ERROR, f"读取返回非对象结果（{type(payload).__name__}），无法作为证据。", {
            "code": "invalid_result_type"
        }
    data = dict(payload)
    if data.get("error"):
        return READ_ERROR, f"读取失败：{_text(data.get('error'))}", {
            "code": "reader_error",
            "detail": _text(data.get("error")),
        }

    if collector_id == "openmes.work_order_lookup":
        if data.get("found"):
            return (
                READ_OK,
                f"精确匹配到工单 {_text(data.get('work_order_no'))}（id={_text(data.get('work_order_id'))}）。",
                None,
            )
        return READ_EMPTY, _text(data.get("note")) or "MES 中没有该工单编号。", None

    if collector_id == "openmes.track_order":
        status = _text(data.get("status"))
        if status == "MES_UNREACHABLE":
            return READ_ERROR, "OpenMES 不可达，无法读取生产数据。", {"code": "mes_unreachable"}
        if status == "NOT_FOUND":
            return READ_EMPTY, "工单在 MES 中不存在，无法读取进度。", None
        return (
            READ_OK,
            f"工单状态 {status or '未知'}，完成率 {data.get('completion_rate')}。",
            None,
        )

    if collector_id == "openmes.quality_package":
        records = _as_list(data.get("quality_records"))
        documents = _as_list(data.get("documents"))
        inspections = _as_list(data.get("inspections"))
        unsupported = [
            _text(item.get("capability"))
            for item in _as_list(data.get("unsupported_capabilities"))
            if _text(_as_dict(item).get("status")) == "NOT_SUPPORTED"
        ]
        summary = (
            f"质量记录 {len(records)} 条、工程文档 {len(documents)} 份、检验记录 {len(inspections)} 条；"
            f"质量门禁通过={bool(data.get('quality_gate_passed'))}。"
        )
        scope = _text(_as_dict(data.get("gate_details")).get("inspections_scope"))
        if scope == "unavailable":
            summary += "检验记录无法按工单批次关联（批次读取失败），该维度存在数据缺口，需人工核对。"
        if unsupported:
            summary += f"OpenMES 不支持的能力：{unsupported}。"
        return READ_OK, summary, None

    if collector_id in ("openmes.engineering_documents", "openmes.inspections"):
        items = _as_list(data.get("__items"))
        label = COLLECTOR_BY_ID[collector_id].label
        if not items:
            return READ_EMPTY, f"{label}：接口读取成功但没有数据（0 条），不等于读取失败。", None
        return READ_OK, f"{label}：{len(items)} 条。", None

    if collector_id in ("erp.quotation", "erp.quotation_by_order", "procurement.plan"):
        if data.get("found") is False:
            return READ_EMPTY, _text(data.get("note")) or "没有找到对应的真实记录。", None
        if collector_id == "erp.quotation":
            return (
                READ_OK,
                f"报价 {_text(data.get('quotation_id'))}，状态 {_text(data.get('status'))}，"
                f"币种 {_text(data.get('currency'))}。",
                None,
            )
        return READ_OK, f"记录 {_text(data.get('quotation_id') or data.get('plan_id'))}，状态 {_text(data.get('status'))}。", None

    if collector_id == "quality.issue_queue":
        items = _as_list(data.get("items"))
        if not items:
            return READ_EMPTY, "OpenMES 未关闭质量问题队列为空（读取成功）。", None
        return READ_OK, f"未关闭质量问题 {len(items)} 条。", None

    if collector_id == "quality.issue_workflow":
        if not data:
            return READ_EMPTY, "本地审批库中没有质量问题处置/关闭审批记录。", None
        return READ_OK, f"涉及 {len(data)} 个质量问题的处置/关闭审批记录。", None

    if collector_id == "quality.issue_closure":
        if _text(data.get("status")) == "error":
            return READ_ERROR, f"读取失败：{_text(data.get('error'))}", {"code": "reader_error"}
        checks = _as_dict(data.get("checks"))
        ready = bool(data.get("closure_ready"))
        summary = f"关闭前置条件满足={ready}；" + "，".join(
            f"{key}={value}" for key, value in checks.items()
        )
        gap = _text(data.get("data_gap"))
        if gap:
            summary += f"；动作读取异常：{gap}"
        return READ_OK, summary, None

    if collector_id == "shipping.gate":
        return (
            READ_OK,
            f"可发运={bool(data.get('can_ship'))}，阻断原因：{_as_list(data.get('blocking_reasons'))}。",
            None,
        )

    return READ_OK, f"读取成功（{collector_id}）。", None


# ----------------------------------------------------------------------
# 真实只读读取器（只使用清单 §1.2 已核对的函数）
# ----------------------------------------------------------------------


async def _read_work_order_lookup(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.find_work_order_by_no(context.get("work_order_no", ""))


async def _read_track_order(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.track_order(context.get("work_order_id", ""))


async def _read_quality_package(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.quality_package(context.get("work_order_id", ""))


async def _read_quotation(context: Mapping[str, Any]) -> dict[str, Any]:
    """本地报价记录（同步读取）。无记录返回 found=False（EMPTY），不是读取失败。"""
    quotation_id = _text(context.get("quotation_id"))
    result = real_order.get_quotation(quotation_id)
    if result is None:
        return {
            "found": False,
            "quotation_id": quotation_id,
            "note": "本地没有该报价编号的真实记录（读取成功，无数据）。",
        }
    return dict(result)


async def _read_quotation_by_order(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.find_quotation_by_erp_order(context.get("erp_order_id", ""))


async def _read_procurement_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.find_latest_plan_by_quotation(context.get("quotation_id", ""))


async def _read_issue_queue(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.quality_todo_list()


async def _read_issue_workflow(context: Mapping[str, Any]) -> dict[str, Any]:
    return real_order.get_quality_issue_workflow_states(limit=200)


async def _read_issue_closure(context: Mapping[str, Any]) -> dict[str, Any]:
    return await real_order.assess_quality_issue_closure(
        context.get("work_order_id", ""), context.get("issue_id", "")
    )


async def _read_ship_gate(context: Mapping[str, Any]) -> dict[str, Any]:
    approved = context.get("quotation_approved")
    return await real_order.ship_gate_check(
        context.get("work_order_id", ""), quotation_approved=bool(approved)
    )


READERS: dict[str, Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    "openmes.work_order_lookup": _read_work_order_lookup,
    "openmes.track_order": _read_track_order,
    "openmes.quality_package": _read_quality_package,
    "erp.quotation": _read_quotation,
    "erp.quotation_by_order": _read_quotation_by_order,
    "procurement.plan": _read_procurement_plan,
    "quality.issue_queue": _read_issue_queue,
    "quality.issue_workflow": _read_issue_workflow,
    "quality.issue_closure": _read_issue_closure,
    "shipping.gate": _read_ship_gate,
}


# ----------------------------------------------------------------------
# 任务
# ----------------------------------------------------------------------


def create_task(
    *,
    customer_id: str = "",
    customer_name: str,
    part_no: str,
    part_name: str,
    owner: str,
    part_side: str = "",
    drawing_version: str = "",
    engineering_change_level: str = "",
    submission_reason: str = "",
    submission_level: str = "",
    reviewer: str = "",
    due_date: str = "",
    template_id: str = ppap_template.TEMPLATE_ID,
    authorization_ref: str = "",
    actor: str = "",
    data_mode: str = ppap_mode.MODE_REAL,
) -> dict[str, Any]:
    """创建 PPAP 任务：登记基础信息 + 生成全部条目（适用性未确认、状态待确认）。"""
    customer_name = _text(customer_name)
    part_no = _text(part_no)
    part_name = _text(part_name)
    owner = _text(owner) or _text(actor)
    problems: list[str] = []
    if not customer_name:
        problems.append("customer_name 不能为空（客户名称必须来自真实业务记录，不填默认值）")
    if not part_no:
        problems.append("part_no 不能为空")
    if not part_name:
        problems.append("part_name 不能为空")
    if not owner:
        problems.append("owner 不能为空（任务负责人）")
    side, side_error = ppap_inputs.normalize_part_side(part_side or ppap_inputs.SIDE_NONE)
    if side_error:
        problems.append(side_error)
    if problems:
        return {"success": False, "error": "；".join(problems), "code": "invalid_task"}
    try:
        mode = ppap_mode.normalize_mode(data_mode)
    except ValueError as exc:
        return _failure("invalid_task_mode", str(exc))
    mode_problem = ppap_mode.validate_task_data(mode, {
        "customer_id": customer_id, "customer_name": customer_name, "part_no": part_no,
        "part_name": part_name, "drawing_version": drawing_version,
        "engineering_change_level": engineering_change_level, "submission_reason": submission_reason,
        "submission_level": submission_level, "authorization_ref": authorization_ref})
    if mode_problem:
        return mode_problem

    if _text(template_id) != ppap_template.TEMPLATE_ID:
        return {
            "success": False,
            "code": "unknown_template",
            "error": (
                f"当前只有项目自定义模板 {ppap_template.TEMPLATE_ID}（{ppap_template.TEMPLATE_SOURCE}）；"
                "客户模板尚未取得（清单 §9 问题 1），不得声称按客户模板编制。"
            ),
        }

    task_id = _gen_id("PPAP")
    business_object = {
        "work_order_no": "",
        "work_order_id": "",
        "quotation_id": "",
        "erp_order_id": "",
        "issue_id": "",
        "design_responsibility": "",
        "customer_spec_reference": "",
    }
    coverage = {
        "evidence": {"total": 0, "ok": 0, "empty": 0, "error": 0, "truncated": 0, "by_source_type": {}},
        "checks": {},
        "applicability": {"confirmed": 0, "total": len(ppap_template.ELEMENT_IDS)},
    }
    coverage = _append_audit(
        coverage,
        {
            "action": "task_created",
            "by": actor or owner,
            "detail": {"part_no": part_no, "part_side": side, "customer_name": customer_name},
        },
    )

    with SessionLocal() as session:
        row = PpapTaskRow(
            task_id=task_id,
            data_mode=mode,
            customer_id=_text(customer_id),
            customer_name=customer_name,
            part_no=part_no,
            part_name=part_name,
            part_side=side,
            drawing_version=_text(drawing_version),
            engineering_change_level=_text(engineering_change_level),
            submission_reason=_text(submission_reason),
            submission_level=_text(submission_level),
            template_id=ppap_template.TEMPLATE_ID,
            template_version=ppap_template.TEMPLATE_VERSION,
            template_source=ppap_template.TEMPLATE_SOURCE,
            owner=owner,
            reviewer=_text(reviewer),
            due_date=_text(due_date),
            run_state=RUN_NOT_STARTED,
            package_state=PKG_NOT_SUBMITTED,
            customer_state=CUST_NOT_SUBMITTED,
            business_object_json=business_object,
            coverage_json=coverage,
            authorized_by=_text(actor) or owner,
            authorization_ref=_text(authorization_ref),
            state_note="任务已创建：22 条目适用性未确认，需人工逐条确认后才可编制。",
        )
        session.add(row)
        for spec in ppap_template.element_specs():
            session.add(
                PpapElementRow(
                    element_row_id=_gen_id("PELM"),
                    task_id=task_id,
                    element_id=spec.element_id,
                    category=spec.category,
                    requirement=ppap_template.REQ_UNCONFIRMED,
                    applicability_basis=(
                        f"建议值 {spec.suggested_requirement}（公开 PPAP 惯例，未确认）。"
                        + (f"适用条件：{spec.applicability_condition}" if spec.applicability_condition else "")
                    ),
                    title=spec.title,
                    compilation_method=spec.compilation_method,
                    status=ppap_template.EL_APPLICABILITY_UNCONFIRMED,
                    status_reason="适用性尚未由人工确认（PPAP-CHK-REQ-001 阻断批准）",
                )
            )
        session.commit()
        task, elements = _load_task_rows(session, task_id)
        assert task is not None
        return {
            "success": True,
            "task": _task_dict(task),
            "elements": [_element_dict(item, include_content=False) for item in elements],
            "template": ppap_template.template_descriptor(),
            "written": "local_ppap_store",
            "authority": "ppap_local",
        }


def list_tasks(
    *,
    customer_id: str = "",
    part_no: str = "",
    run_state: str = "",
    package_state: str = "",
    owner: str = "",
    limit: Any = 50,
    offset: Any = 0,
    mode: str = "",
) -> dict[str, Any]:
    try:
        mode = ppap_mode.normalize_mode(mode) if _text(mode) else ""
    except ValueError as exc:
        return _failure("invalid_task_mode", str(exc))
    size = _clamp_limit(limit)
    start = max(0, _int(offset, 0))
    with SessionLocal() as session:
        query = session.query(PpapTaskRow)
        if mode:
            query = query.filter(PpapTaskRow.data_mode == mode)
        if _text(customer_id):
            query = query.filter(PpapTaskRow.customer_id == _text(customer_id))
        if _text(part_no):
            query = query.filter(PpapTaskRow.part_no == _text(part_no))
        if _text(run_state):
            query = query.filter(PpapTaskRow.run_state == _text(run_state))
        if _text(package_state):
            query = query.filter(PpapTaskRow.package_state == _text(package_state))
        if _text(owner):
            query = query.filter(PpapTaskRow.owner == _text(owner))
        total = query.count()
        rows = query.order_by(PpapTaskRow.created_at.desc()).offset(start).limit(size).all()
        items = [_task_dict(row) for row in rows]
        task_ids = [row.task_id for row in rows]
        open_issues: dict[str, int] = {}
        blockers: dict[str, int] = {}
        if task_ids:
            for issue in (
                session.query(PpapIssueRow)
                .filter(PpapIssueRow.task_id.in_(task_ids), PpapIssueRow.status == ISSUE_OPEN)
                .all()
            ):
                open_issues[issue.task_id] = open_issues.get(issue.task_id, 0) + 1
                if issue.blocks_approval:
                    blockers[issue.task_id] = blockers.get(issue.task_id, 0) + 1
    for item in items:
        item["open_issue_count"] = open_issues.get(item["task_id"], 0)
        item["blocking_issue_count"] = blockers.get(item["task_id"], 0)
    return {
        "items": items,
        "total": total,
        "limit": size,
        "offset": start,
        "authority": "ppap_local",
        "data_source": "ppap_tasks",
    }


def get_task(task_id: str, *, include_content: bool = True) -> dict[str, Any]:
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return {"ok": False, "error": "task_not_found", "task_id": task_id}
        input_rows = _load_input_rows(session, task_id)
        evidence_rows = _load_evidence_rows(session, task_id)
        issue_rows = _load_issue_rows(session, task_id)
        artifact_rows = _load_artifact_rows(session, task_id)
        review_rows = (
            session.query(PpapReviewRow)
            .filter(PpapReviewRow.task_id == task_id)
            .order_by(PpapReviewRow.created_at)
            .all()
        )
        run_rows = (
            session.query(PpapCompileRunRow)
            .filter(PpapCompileRunRow.task_id == task_id)
            .order_by(PpapCompileRunRow.started_at.desc())
            .limit(10)
            .all()
        )
    elements_data = [_element_dict(row, include_content=include_content) for row in elements]
    by_element_issues: dict[str, list[dict[str, Any]]] = {}
    for row in issue_rows:
        by_element_issues.setdefault(row.element_id or "", []).append(_issue_dict(row))
    for item in elements_data:
        item["issues"] = by_element_issues.get(item["element_id"], [])
    return {
        "ok": True,
        "task": _task_dict(task),
        "elements": elements_data,
        "inputs": [_input_dict(row) for row in input_rows],
        "evidence": [_evidence_dict(row) for row in evidence_rows],
        "issues": [_issue_dict(row) for row in issue_rows],
        "artifacts": [_artifact_dict(row) for row in artifact_rows],
        "reviews": [_review_dict(row) for row in review_rows],
        "runs": [_run_dict(row) for row in run_rows],
        "template": ppap_template.template_descriptor(),
        "applicability": _applicability_summary(elements),
        "authority": "ppap_local",
    }


def _applicability_summary(elements: Sequence[PpapElementRow]) -> dict[str, Any]:
    confirmed = [row for row in elements if _text(row.requirement) in ppap_template.REQUIREMENT_VALUES]
    unconfirmed = [row.element_id for row in elements if _text(row.requirement) == ppap_template.REQ_UNCONFIRMED]
    return {
        "total": len(elements),
        "confirmed": len(confirmed),
        "unconfirmed_element_ids": unconfirmed,
        "submit_count": len([row for row in elements if row.requirement == ppap_template.REQ_SUBMIT]),
        "not_applicable_count": len(
            [row for row in elements if row.requirement == ppap_template.REQ_NOT_APPLICABLE]
        ),
    }


def update_task(task_id: str, patch: Mapping[str, Any], *, actor: str, note: str = "") -> dict[str, Any]:
    """修改任务登记信息或业务对象键（白名单，未知键显式拒绝）。"""
    payload = _as_dict(patch)
    mode_problem = ppap_mode.validate_mode_patch(payload)
    if mode_problem:
        return mode_problem
    if not payload:
        return {"success": False, "error": "patch 不能为空", "code": "empty_patch"}
    fields: dict[str, Any] = {}
    business: dict[str, Any] = {}
    rejected: list[str] = []
    for key, value in payload.items():
        name = _text(key)
        if name in TASK_FIELD_KEYS:
            fields[name] = value
        elif name in BUSINESS_OBJECT_KEYS:
            business[name] = value
        else:
            rejected.append(name)
    if rejected:
        return {
            "success": False,
            "code": "unknown_fields",
            "error": f"不支持的字段：{rejected}；允许的任务字段 {list(TASK_FIELD_KEYS)}，业务对象键 {list(BUSINESS_OBJECT_KEYS)}",
        }

    if "part_side" in fields:
        side, side_error = ppap_inputs.normalize_part_side(fields["part_side"])
        if side_error:
            return {"success": False, "code": "invalid_part_side", "error": side_error}
        fields["part_side"] = side
    if "quotation_approved" in business:
        business["quotation_approved"] = business["quotation_approved"] if isinstance(
            business["quotation_approved"], bool
        ) else None
        if business["quotation_approved"] is None:
            return {
                "success": False,
                "code": "invalid_quotation_approved",
                "error": "quotation_approved 必须是布尔值（true/false），不接受推断或文本；未确认时请不要传该键。",
            }

    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return {"success": False, "error": "task_not_found", "code": "task_not_found"}
        candidate = {**_task_dict(task), **fields,
                     "business_object": {**dict(task.business_object_json or {}), **business}}
        mode_problem = ppap_mode.validate_task_data(ppap_mode.task_mode(task), candidate)
        if mode_problem:
            return mode_problem
        changed: dict[str, Any] = {}
        for key, value in fields.items():
            text_value = _text(value)
            if _text(getattr(task, key)) != text_value:
                setattr(task, key, text_value)
                changed[key] = text_value
        business_object = dict(task.business_object_json or {})
        for key, value in business.items():
            new_value = value if isinstance(value, bool) else _text(value)
            if business_object.get(key) != new_value:
                business_object[key] = new_value
                changed[f"business_object.{key}"] = new_value
        if not changed:
            return {"success": True, "changed": {}, "task": _task_dict(task), "note": "无变化"}
        task.business_object_json = business_object
        task.coverage_json = _append_audit(
            task.coverage_json, {"action": "task_updated", "by": actor, "detail": changed, "note": _text(note)}
        )
        task.updated_at = _now()
        session.commit()
        task_row, _elements = _load_task_rows(session, task_id)
        assert task_row is not None
        return {
            "success": True,
            "changed": changed,
            "task": _task_dict(task_row),
            "written": "local_ppap_store",
        }


def confirm_applicability(
    task_id: str, decisions: Any, *, actor: str, note: str = ""
) -> dict[str, Any]:
    """人工确认条目适用性（支持批量）；不适用必须填写依据。"""
    items = [_as_dict(item) for item in _as_list(decisions)]
    if not items:
        return {"success": False, "error": "decisions 不能为空", "code": "empty_decisions"}
    results: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return {"success": False, "error": "task_not_found", "code": "task_not_found"}
        rows = {row.element_id: row for row in elements}
        for item in items:
            element_id = _text(item.get("element_id"))
            requirement = _text(item.get("requirement"))
            basis = _text(item.get("basis"))
            row = rows.get(element_id)
            if row is None:
                rejected.append({"element_id": element_id, "reason": "unknown_element"})
                continue
            error = ppap_template.validate_requirement(requirement)
            if error:
                rejected.append({"element_id": element_id, "reason": "invalid_requirement", "message": error})
                continue
            if requirement == ppap_template.REQ_UNCONFIRMED:
                rejected.append(
                    {"element_id": element_id, "reason": "empty_requirement", "message": "请给出明确适用性取值"}
                )
                continue
            if requirement == ppap_template.REQ_NOT_APPLICABLE and not basis:
                rejected.append(
                    {
                        "element_id": element_id,
                        "reason": "missing_basis",
                        "message": "判定不适用必须填写依据（客户规则/图纸/工程说明），不允许留空。",
                    }
                )
                continue
            row.requirement = requirement
            row.applicability_basis = basis or row.applicability_basis or ""
            row.status = (
                ppap_template.EL_NOT_APPLICABLE
                if requirement == ppap_template.REQ_NOT_APPLICABLE
                else ppap_template.EL_WAITING_DATA
            )
            row.status_reason = (
                "人工确认不适用" if requirement == ppap_template.REQ_NOT_APPLICABLE else "适用性已确认，等待数据"
            )
            row.updated_at = _now()
            results.append(
                {
                    "element_id": element_id,
                    "requirement": requirement,
                    "basis": row.applicability_basis,
                    "status": row.status,
                }
            )
        remaining = [
            row.element_id
            for row in elements
            if _text(row.requirement) == ppap_template.REQ_UNCONFIRMED
        ]
        if results:
            coverage = dict(task.coverage_json or {})
            summary = _applicability_summary(elements)
            coverage["applicability"] = {"confirmed": summary["confirmed"], "total": summary["total"]}
            task.coverage_json = _append_audit(
                coverage,
                {
                    "action": "applicability_confirmed",
                    "by": actor,
                    "detail": {"confirmed": [item["element_id"] for item in results], "note": _text(note)},
                },
            )
            task.updated_at = _now()
        session.commit()
        task_row, element_rows = _load_task_rows(session, task_id)
        assert task_row is not None
        return {
            "success": True,
            "confirmed": results,
            "rejected": rejected,
            "remaining_unconfirmed": remaining,
            "applicability": _applicability_summary(element_rows),
            "task": _task_dict(task_row),
            "written": "local_ppap_store",
            "note": "适用性确认属于业务判断，不推断；未确认的条目仍阻断批准。",
        }


# ----------------------------------------------------------------------
# 取证
# ----------------------------------------------------------------------


async def collect_evidence(
    task_id: str,
    *,
    source_types: Any = None,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    """按计划执行只读取证，三态落库并返回覆盖情况（不写 ERP/MES）。"""
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return {"success": False, "error": "task_not_found", "code": "task_not_found"}
        business = dict(task.business_object_json or {})
    context = _context_from_business(business)
    usable, rejected = _validate_source_types(source_types)
    if not usable:
        return {
            "success": False,
            "code": "no_readable_source",
            "error": "没有任何可自动取证的来源类型",
            "rejected": rejected,
        }

    planned = [c for c in COLLECTORS if c.source_type in set(usable)]
    collected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    call_chain: list[dict[str, Any]] = []

    # 工单编号 → 工单 id 的解析必须先用真实接口完成，再供其他来源使用。
    if not context.get("work_order_id") and context.get("work_order_no"):
        lookup = COLLECTOR_BY_ID["openmes.work_order_lookup"]
        lookup_failure = ""
        try:
            payload = _jsonable(await _read_work_order_lookup(context))
        except Exception as exc:  # 网络/适配器异常：如实记读取失败，不当成"没有该工单"
            payload = None
            lookup_failure = f"{type(exc).__name__}: {exc}"
            logger.warning("PPAP 按工单编号解析工单 id 失败: %s", exc)
            skipped.append(
                {
                    "collector_id": lookup.collector_id,
                    "source_type": lookup.source_type,
                    "label": lookup.label,
                    "reason": "read_failed",
                    "message": lookup_failure,
                }
            )
            call_chain.append(
                {"step": "read", "target": lookup.collector_id, "result": "error", "detail": lookup_failure}
            )
        if payload is None:
            collected.append(
                _store_evidence(
                    task_id,
                    collector=lookup,
                    source_id=context.get("work_order_no"),
                    payload={},
                    read_status=READ_ERROR,
                    summary=f"读取失败：{lookup_failure or '返回了空结果'}",
                    error_json={"code": "read_exception", "detail": lookup_failure},
                    actor=actor,
                    note=note,
                )
            )
        else:
            status, summary, error_json = _classify_read(lookup.collector_id, payload)
            call_chain.append(
                {"step": "read", "target": lookup.collector_id, "result": status, "detail": summary}
            )
            if status == READ_OK:
                context["work_order_id"] = _text(payload.get("work_order_id"))
            results[lookup.collector_id] = payload
            collected.append(
                _store_evidence(
                    task_id,
                    collector=lookup,
                    source_id=context.get("work_order_no"),
                    payload=payload,
                    read_status=status,
                    summary=summary,
                    error_json=error_json,
                    actor=actor,
                    note=note,
                )
            )

    for collector in planned:
        if collector.collector_id == "openmes.work_order_lookup":
            continue
        missing = [key for key in collector.requires if not context.get(key)]
        if missing:
            if "work_order_id" in missing and context.get("work_order_no"):
                missing = [key for key in missing if key != "work_order_id"]
            if missing:
                skipped.append(
                    {
                        "collector_id": collector.collector_id,
                        "source_type": collector.source_type,
                        "label": collector.label,
                        "reason": "missing_business_keys",
                        "missing_business_keys": missing,
                        "message": (
                            f"业务对象缺少 {missing}，该来源不取证（不猜测、不拼接）。"
                            "请在任务业务对象里登记真实单号/编号后重试。"
                        ),
                    }
                )
                continue

        if collector.parent:
            parent_payload = results.get(collector.parent)
            if parent_payload is None:
                skipped.append(
                    {
                        "collector_id": collector.collector_id,
                        "source_type": collector.source_type,
                        "label": collector.label,
                        "reason": "parent_unavailable",
                        "message": f"父来源 {collector.parent} 未取得结果，本条不单独取证。",
                    }
                )
                continue
            items = _as_list(parent_payload.get(collector.slice_key))
            payload = _jsonable(
                {
                    "__items": items,
                    "__slice_of": collector.parent,
                    "__slice_key": collector.slice_key,
                    "gate_details": parent_payload.get("gate_details", {}),
                }
            )
        else:
            failure = ""
            try:
                payload = _jsonable(await READERS[collector.collector_id](context))
            except Exception as exc:  # 网络/适配器异常：如实记读取失败
                payload = None
                failure = f"{type(exc).__name__}: {exc}"
                logger.warning("PPAP 取证读取失败 (%s): %s", collector.collector_id, exc)
                call_chain.append(
                    {"step": "read", "target": collector.collector_id, "result": "error", "detail": failure}
                )
            if payload is None:
                collected.append(
                    _store_evidence(
                        task_id,
                        collector=collector,
                        source_id=_collector_source_id(collector, context),
                        payload={},
                        read_status=READ_ERROR,
                        summary=f"读取失败：{failure or '返回了空结果'}",
                        error_json={"code": "read_exception", "detail": failure},
                        actor=actor,
                        note=note,
                    )
                )
                continue

        status, summary, error_json = _classify_read(collector.collector_id, payload)
        truncated = _truncated_for(collector.collector_id, payload)
        if truncated:
            summary += "（分页窗口已满，不能声明已取得全量结果）"
        results[collector.collector_id] = payload
        call_chain.append(
            {
                "step": "read",
                "target": collector.collector_id,
                "result": status,
                "truncated": truncated,
                "detail": summary,
            }
        )
        collected.append(
            _store_evidence(
                task_id,
                collector=collector,
                source_id=_collector_source_id(collector, context),
                payload=payload,
                read_status=status,
                summary=summary,
                error_json=error_json,
                truncated=truncated,
                actor=actor,
                note=note,
            )
        )

    with SessionLocal() as session:
        task_row, element_rows = _load_task_rows(session, task_id)
        assert task_row is not None
        evidence_rows = _load_evidence_rows(session, task_id)
        input_rows = _load_input_rows(session, task_id)
        coverage = _compute_coverage(task_row, element_rows, input_rows, evidence_rows)
        task_row.coverage_json = _append_audit(
            dict(task_row.coverage_json or {}),
            {
                "action": "evidence_collected",
                "by": actor,
                "detail": {
                    "collected": [item["collector_id"] for item in collected],
                    "skipped": [item["collector_id"] for item in skipped],
                    "note": _text(note),
                },
            },
        )
        task_row.coverage_json = {**dict(task_row.coverage_json or {}), "evidence": coverage["evidence"]}
        task_row.run_state = _compute_run_state(task_row, element_rows, input_rows, evidence_rows)
        task_row.updated_at = _now()
        session.commit()
        task_data = _task_dict(task_row)

    return {
        "success": True,
        "task_id": task_id,
        "collected": collected,
        "skipped": skipped,
        "rejected": rejected,
        "call_chain": call_chain,
        "coverage": coverage,
        "task": task_data,
        "authority": "openmes/erp/local(只读)",
        "written": "local_ppap_store",
        "note": "取证只读取真实系统；EMPTY 表示接口成功但没有数据，ERROR 表示读取失败，两者都不作为证据。",
    }


def _collector_source_id(collector: Collector, context: Mapping[str, Any]) -> str:
    if collector.collector_id == "openmes.work_order_lookup":
        return _text(context.get("work_order_no")) or "UNKNOWN"
    if collector.collector_id == "erp.quotation_by_order":
        return _text(context.get("erp_order_id")) or "UNKNOWN"
    if collector.collector_id == "quality.issue_queue":
        return "ALL_OPEN_ISSUES"
    if collector.collector_id == "quality.issue_workflow":
        return "RECENT_APPROVALS"
    if collector.collector_id == "quality.issue_closure":
        return f"{_text(context.get('work_order_id'))}:{_text(context.get('issue_id'))}"
    if collector.collector_id in ("erp.quotation", "procurement.plan"):
        return _text(context.get("quotation_id")) or "UNKNOWN"
    return _text(context.get("work_order_id")) or "UNKNOWN"


def _store_evidence(
    task_id: str,
    *,
    collector: Collector,
    source_id: str,
    payload: Mapping[str, Any],
    read_status: str,
    summary: str,
    error_json: Mapping[str, Any] | None,
    truncated: bool = False,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    """按 (task, collector, source_id) 落库：同一来源重读只更新内容哈希与时间。"""
    element_ids = sorted(
        {spec.element_id for spec in ppap_template.elements_using_evidence(collector.source_type)}
    )
    content_hash = ppap_storage.sha256_json(payload) if read_status != READ_ERROR else ""
    dedup_key = f"{collector.collector_id}:{source_id}"
    with SessionLocal() as session:
        row = (
            session.query(PpapEvidenceRow)
            .filter(PpapEvidenceRow.task_id == task_id, PpapEvidenceRow.dedup_key == dedup_key)
            .one_or_none()
        )
        if row is None:
            row = PpapEvidenceRow(
                evidence_id=_gen_id("PEVD"),
                task_id=task_id,
                dedup_key=dedup_key,
                source_system=collector.collector_id.split(".")[0],
                source_type=collector.source_type,
                source_id=source_id,
                source_version="",
                locator=collector.collector_id,
                summary="",
                read_status=read_status,
                truncated=truncated,
                content_hash="",
                review_state="UNVERIFIED",
                collected_by=actor,
                element_ids_json=element_ids,
                payload_json={},
                collected_at=_now(),
            )
            session.add(row)
        prior_attempts = _int((row.payload_json or {}).get("__read_attempts"), 0)
        row.summary = summary
        row.read_status = read_status
        row.truncated = truncated
        row.content_hash = content_hash
        row.element_ids_json = element_ids
        row.collected_by = actor or row.collected_by
        row.error_json = dict(error_json) if error_json else None
        row.payload_json = _jsonable(
            {
                "collector_id": collector.collector_id,
                "collector_label": collector.label,
                "collector_description": collector.description,
                "read": dict(payload),
                "__read_attempts": prior_attempts + 1,
                "__last_read_at": ppap_storage.now_iso(),
                "__note": _text(note),
            }
        )
        row.source_version = (content_hash[:16] if content_hash else "unavailable")
        row.collected_at = _now()
        session.commit()
        return {
            "collector_id": collector.collector_id,
            "source_type": collector.source_type,
            "source_id": source_id,
            "read_status": read_status,
            "truncated": truncated,
            "content_hash": content_hash,
            "summary": summary,
            "read_attempts": prior_attempts + 1,
        }


def register_human_evidence(
    task_id: str,
    *,
    source_type: str,
    source_id: str,
    summary: str,
    locator: str = "",
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """登记人工提供的来源（原件上传 / 引用的既有批准包）。

    - ``user_upload``：``locator`` 为受控目录内的相对路径或外部文件编号；文件在受控目录内
      时计算真实内容哈希，否则如实记录“未校验内容”。
    - ``internal_ppap_package``：``locator`` 为既有 PPAP 产物 ``artifact_id``，会核对其
      真实存在与内容哈希；不存在即拒绝登记。
    本接口不生成任何文件内容、不生成签字或结论。
    """
    source_type = _text(source_type)
    if source_type not in HUMAN_EVIDENCE_TYPES:
        return {
            "success": False,
            "code": "source_type_not_allowed",
            "error": f"只允许人工登记 {list(HUMAN_EVIDENCE_TYPES)}；真实系统来源请用取证接口。",
        }
    source_id = _text(source_id) or _text(locator)
    locator = _text(locator)
    summary = _text(summary)
    if not source_id or not summary:
        return {"success": False, "code": "invalid_payload", "error": "source_id 与 summary 必填"}
    if source_type == ppap_template.EV_USER_UPLOAD and not locator:
        return {
            "success": False,
            "code": "missing_locator",
            "error": "登记原件必须给出 locator（受控目录相对路径或外部文件编号）。",
        }

    content_hash = ""
    byte_size = 0
    verified = False
    detail: dict[str, Any] = {}
    if source_type == ppap_template.EV_INTERNAL_PACKAGE:
        with SessionLocal() as session:
            artifact = session.get(PpapArtifactRow, locator)
            if artifact is None:
                return {
                    "success": False,
                    "code": "artifact_not_found",
                    "error": "引用的既有产物不存在，拒绝登记（不得虚构已批准包）。",
                }
            if artifact.status not in (ART_APPROVED, ART_ARCHIVED):
                return {
                    "success": False,
                    "code": "artifact_not_approved",
                    "error": f"产物 {locator} 当前状态为 {artifact.status}，只有已批准/已归档版本可被引用。",
                }
            content_hash = artifact.content_hash or ""
            byte_size = int(artifact.byte_size or 0)
            verified = True
            source_id = source_id or artifact.artifact_id
            detail = {
                "artifact_id": artifact.artifact_id,
                "artifact_version": artifact.version,
                "artifact_status": artifact.status,
                "file_name": artifact.file_name,
            }
    else:
        rel_path = locator.replace("\\", "/").lstrip("/")
        try:
            data = ppap_storage.read_bytes(rel_path)
        except Exception:
            data = None
        if data is not None:
            content_hash = ppap_storage.sha256_bytes(data)
            byte_size = len(data)
            verified = True
            detail = {"storage_rel_path": rel_path, "byte_size": byte_size}
        else:
            detail = {
                "external_reference": locator,
                "content_verified": False,
                "message": "文件不在受控目录内：仅登记外部编号，未校验文件内容（设计说明 §7）。",
            }

    collector = Collector(
        collector_id=f"human.{source_type}",
        source_type=source_type,
        label="人工登记来源",
        description="由已登录人员登记的原件或既有批准包引用，系统不生成内容。",
    )
    record = _store_evidence(
        task_id,
        collector=collector,
        source_id=source_id,
        payload={"human_registration": detail, "locator": locator, "content_verified": verified},
        read_status=READ_OK if verified else READ_EMPTY,
        summary=summary + ("" if verified else "（未校验文件内容）"),
        error_json=None if verified else {"code": "content_not_verified"},
        truncated=False,
        actor=actor,
        note=note,
    )
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return {"success": False, "code": "task_not_found", "error": "task_not_found"}
        task.coverage_json = _append_audit(
            task.coverage_json,
            {"action": "human_evidence_registered", "by": actor, "detail": {**record, "note": _text(note)}},
        )
        task.updated_at = _now()
        session.commit()
    record["success"] = True
    record["content_verified"] = verified
    record["review_state"] = "UNVERIFIED"
    record["detail"] = detail
    record["written"] = "local_ppap_store"
    return record


def list_evidence(
    task_id: str, *, source_type: str = "", include_payload: bool = False
) -> dict[str, Any]:
    with SessionLocal() as session:
        if session.get(PpapTaskRow, task_id) is None:
            return {"ok": False, "error": "task_not_found", "task_id": task_id}
        rows = _load_evidence_rows(session, task_id)
    items = [
        _evidence_dict(row, include_payload=include_payload)
        for row in rows
        if not _text(source_type) or row.source_type == _text(source_type)
    ]
    return {"ok": True, "task_id": task_id, "items": items, "total": len(items)}


# ----------------------------------------------------------------------
# 结构化输入
# ----------------------------------------------------------------------


def upsert_input(
    task_id: str,
    *,
    dataset: str,
    payload: Mapping[str, Any],
    actor: str,
    part_side: str = "",
    record_key: str = "",
    source_type: str = "user_confirmation",
    source_ref: str = "",
    element_id: str = "",
) -> dict[str, Any]:
    """写入一条已确认的结构化输入（按契约校验；判定类字段一律拒绝）。"""
    dataset = _text(dataset)
    contract = ppap_inputs.dataset_contract(dataset)
    if contract is None:
        return {
            "success": False,
            "code": "unknown_dataset",
            "error": f"未知数据集 {dataset!r}；允许 {list(ppap_template.ALL_DATASETS)}",
        }
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return {"success": False, "error": "task_not_found", "code": "task_not_found"}
        task_side = task.part_side
    request_payload = dict(payload or {})

    side_input = _text(part_side)
    inherited_from_task = False
    if side_input and side_input != ppap_inputs.SIDE_NONE:
        # AC01：左右件数据必须各持独立记录。任务的 part_side 一旦声明，
        # 显式传入的侧别不得与之相反，也不得在无名侧任务上挂左右件数据。
        conflict = None
        if _text(task_side) in (ppap_inputs.SIDE_LEFT, ppap_inputs.SIDE_RIGHT):
            if side_input in (ppap_inputs.SIDE_LEFT, ppap_inputs.SIDE_RIGHT) and side_input != _text(
                task_side
            ):
                conflict = (
                    f"任务 part_side={_text(task_side)}，不能录入 part_side={side_input} 的记录；"
                    "左右件必须各自建独立 PPAP 任务与独立数据。"
                )
        elif side_input in (ppap_inputs.SIDE_LEFT, ppap_inputs.SIDE_RIGHT):
            conflict = (
                f"任务未声明左右件（part_side={_text(task_side) or 'NONE'}），"
                f"不能录入 part_side={side_input} 的记录；请先在任务上确认侧别。"
            )
        if conflict:
            return {"success": False, "code": "side_conflict", "error": conflict, "dataset": dataset}
    if not side_input and dataset in ppap_template.SIDE_SENSITIVE_DATASETS:
        if _text(task_side) in (ppap_inputs.SIDE_LEFT, ppap_inputs.SIDE_RIGHT, ppap_inputs.SIDE_BOTH):
            side_input = _text(task_side)
            inherited_from_task = True
    outcome = ppap_inputs.validate_input(dataset, request_payload, part_side=side_input or None)
    if not outcome.get("ok"):
        return {
            "success": False,
            "code": "invalid_input",
            "errors": outcome.get("errors", []),
            "dataset": dataset,
            "error": "；".join(outcome.get("errors", [])) or "输入校验失败",
        }
    normalized = dict(outcome.get("payload") or {})
    side = _text(outcome.get("part_side")) or ppap_inputs.SIDE_NONE
    key = _text(record_key) or _record_key(dataset, normalized, side)

    with SessionLocal() as session:
        # PostgreSQL serializes natural-key validation across processes for a task.
        if session.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text
            session.execute(text("BEGIN IMMEDIATE"))
        locked_task = session.get(PpapTaskRow, task_id, with_for_update=True)
        if locked_task is None:
            return _failure("task_not_found")
        mode_problem = ppap_mode.validate_input_source(ppap_mode.task_mode(locked_task), source_type, source_ref, normalized)
        if mode_problem:
            return mode_problem
        if dataset == ppap_template.DS_CHARACTERISTIC:
            duplicate = ppap_characteristics.validate_characteristic_upsert(
                [_input_dict(item) for item in _load_input_rows(session, task_id)],
                {"task_id": task_id, "dataset": dataset, "record_key": key,
                 "part_side": side, "payload": normalized})
            if not duplicate["ok"]:
                return {**_failure(duplicate["code"], "同一特性及侧别已存在记录；请载入原记录修订，不能另建记录选择另一套规格"),
                        "conflict": duplicate}
        row = (
            session.query(PpapInputRow)
            .filter(
                PpapInputRow.task_id == task_id,
                PpapInputRow.dataset == dataset,
                PpapInputRow.record_key == key,
                PpapInputRow.part_side == side,
            )
            .one_or_none()
        )
        created = row is None
        if row is None:
            row = PpapInputRow(
                input_id=_gen_id("PIN"),
                task_id=task_id,
                dataset=dataset,
                record_key=key,
                part_side=side,
                element_id=_text(element_id),
                payload_json=normalized,
                source_type=_text(source_type) or "user_confirmation",
                source_ref=_text(source_ref),
                confirmed_by=actor,
                confirmed_at=_now(),
            )
            session.add(row)
        else:
            row.payload_json = normalized
            row.element_id = _text(element_id) or row.element_id
            row.source_type = _text(source_type) or row.source_type
            row.source_ref = _text(source_ref) or row.source_ref
            row.confirmed_by = actor
            row.confirmed_at = _now()
            row.updated_at = _now()
        session.commit()
        task_row, element_rows = _load_task_rows(session, task_id)
        assert task_row is not None
        evidence_rows = _load_evidence_rows(session, task_id)
        input_rows = _load_input_rows(session, task_id)
        task_row.run_state = _compute_run_state(task_row, element_rows, input_rows, evidence_rows)
        task_row.coverage_json = _append_audit(
            task_row.coverage_json,
            {
                "action": "input_confirmed",
                "by": actor,
                "detail": {
                    "dataset": dataset,
                    "record_key": key,
                    "part_side": side,
                    "created": created,
                    "part_side_inherited_from_task": inherited_from_task,
                },
            },
        )
        task_row.updated_at = _now()
        session.commit()
        return {
            "success": True,
            "input_id": row.input_id,
            "created": created,
            "dataset": dataset,
            "record_key": key,
            "part_side": side,
            "part_side_inherited_from_task": inherited_from_task,
            "payload": normalized,
            "confirmed_by": actor,
            "written": "local_ppap_store",
            "note": "输入只接受原始数据与规格；“合格/不合格”等判定字段由检查与已确认规则产生。",
        }


def _record_key(dataset: str, payload: Mapping[str, Any], side: str) -> str:
    """按数据集自然键生成记录键；无自然键时用内容哈希（稳定且可追溯）。"""
    data = dict(payload or {})
    if dataset == ppap_template.DS_PRODUCT_BASE:
        return f"{_text(data.get('part_no'))}|{side}"
    if dataset == ppap_template.DS_PROCESS_OPERATION:
        return f"{_text(data.get('op_id'))}|{side}"
    if dataset == ppap_template.DS_PROCESS_RELATION:
        return f"{_text(data.get('from_op'))}>{_text(data.get('to_op'))}|{side}"
    if dataset == ppap_template.DS_CHARACTERISTIC:
        return f"{_text(data.get('characteristic_id'))}|{side}"
    if dataset == ppap_template.DS_INSPECTION_VALUE:
        point = _text(data.get("point"))
        sample = _text(data.get("sample_no"))
        return f"{_text(data.get('characteristic_id'))}:{point}:{sample}|{side}"
    if dataset in (ppap_template.DS_MSA_STUDY, ppap_template.DS_PROCESS_STUDY):
        return f"{_text(data.get('study_id'))}|{side}"
    if dataset == ppap_template.DS_MATERIAL_EVIDENCE:
        return f"{_text(data.get('report_no')) or _text(data.get('lot_no'))}:{_text(data.get('test_item'))}|{side}"
    if dataset == ppap_template.DS_PACKAGING_EQUIPMENT:
        return f"{_text(data.get('item'))}|{side}"
    if dataset == ppap_template.DS_CHANGE_RECORD:
        return _text(data.get("change_no")) or ppap_storage.sha256_json(data)[:16]
    if dataset == ppap_template.DS_PHYSICAL_RECORD:
        return f"{_text(data.get('record_type'))}:{_text(data.get('storage_location'))}|{side}"
    if dataset == ppap_template.DS_CUSTOMER_HANDLING:
        return f"{_text(data.get('stage'))}:{ppap_storage.sha256_json(data)[:12]}|{side}"
    return ppap_storage.sha256_json(data)[:16]


def list_inputs(task_id: str, *, dataset: str = "") -> dict[str, Any]:
    with SessionLocal() as session:
        if session.get(PpapTaskRow, task_id) is None:
            return {"ok": False, "error": "task_not_found", "task_id": task_id}
        rows = _load_input_rows(session, task_id)
    items = [_input_dict(row) for row in rows if not _text(dataset) or row.dataset == _text(dataset)]
    return {
        "ok": True,
        "task_id": task_id,
        "items": items,
        "total": len(items),
        "contracts": ppap_inputs.dataset_contracts(),
        "datasets": list(ppap_template.ALL_DATASETS),
    }


# ----------------------------------------------------------------------
# 侧别匹配、条目就绪度、输入快照
# ----------------------------------------------------------------------


def _side_matches(record_side: Any, task_side: Any) -> bool:
    """一条输入的侧别是否属于本任务（AC01 左右件隔离）。

    - 未标注侧别的记录（NONE）视为左右共用，任何任务都接受。
    - 任务声明 LEFT/RIGHT 时，只接受同侧记录与 BOTH（左右共用记录）。
    - 任务未声明侧别时，不接受 LEFT/RIGHT 记录（避免左右件数据混入无名侧任务）；
      任务声明 BOTH 时同样不接受单侧记录——左右件必须各持独立任务。
    """
    record = _text(record_side).upper() or ppap_inputs.SIDE_NONE
    task = _text(task_side).upper() or ppap_inputs.SIDE_NONE
    if record == ppap_inputs.SIDE_NONE:
        return True
    if task == ppap_inputs.SIDE_NONE:
        return False
    if record == ppap_inputs.SIDE_BOTH:
        return True
    return record == task


def _input_records(
    task_row: PpapTaskRow,
    input_rows: Sequence[PpapInputRow],
    dataset: str,
) -> tuple[list[PpapInputRow], int]:
    """返回（属于本任务的记录，被侧别隔离排除的记录数）。"""
    kept: list[PpapInputRow] = []
    excluded = 0
    for row in input_rows:
        if _text(row.dataset) != dataset:
            continue
        if _side_matches(row.part_side, task_row.part_side):
            kept.append(row)
        else:
            excluded += 1
    return kept, excluded


def _input_payloads(
    task_row: PpapTaskRow,
    input_rows: Sequence[PpapInputRow],
    dataset: str,
) -> list[tuple[PpapInputRow, dict[str, Any]]]:
    """按数据集取本任务的输入记录及其归一化载荷（保持确认人可追溯）。"""
    kept, _ = _input_records(task_row, input_rows, dataset)
    return [(row, dict(row.payload_json or {})) for row in kept]


def _evidence_for(source_type: str, evidence_rows: Sequence[PpapEvidenceRow]) -> list[PpapEvidenceRow]:
    return [row for row in evidence_rows if _text(row.source_type) == source_type]


def _evidence_usable(rows: Sequence[PpapEvidenceRow]) -> list[PpapEvidenceRow]:
    """可用证据：读取状态为 OK。EMPTY（合法空结果）与 ERROR（取不到）都不能当资料。"""
    return [row for row in rows if _text(row.read_status) == READ_OK]


def _evidence_state(
    source_type: str, evidence_rows: Sequence[PpapEvidenceRow]
) -> dict[str, Any]:
    rows = _evidence_for(source_type, evidence_rows)
    statuses = [_text(row.read_status) for row in rows]
    if not rows:
        state = "NOT_COLLECTED"
    elif READ_OK in statuses:
        state = READ_OK
    elif READ_ERROR in statuses:
        state = READ_ERROR
    else:
        state = READ_EMPTY
    return {
        "source_type": source_type,
        "state": state,
        "row_count": len(rows),
        "ok_count": sum(1 for item in statuses if item == READ_OK),
        "truncated_ids": [row.source_id for row in rows if bool(row.truncated)],
        "error_ids": [row.source_id for row in rows if _text(row.read_status) == READ_ERROR],
    }


def _element_readiness(
    task_row: PpapTaskRow,
    spec: ppap_template.PpapElementSpec,
    input_rows: Sequence[PpapInputRow],
    evidence_rows: Sequence[PpapEvidenceRow],
) -> dict[str, Any]:
    """条目资料就绪度（只描述资料，不产生判定，也不改条目状态）。

    就绪口径（与设计说明 §5.2 方法表一致）：

    - ``required_inputs`` 必须齐（按侧别匹配后的记录）——这是"可编制"的结构性前提。
    - ``generates_content=False`` 的条目（引用文件/实物登记）没有可生成的内容，
      其产物就是原件本身，因此还必须有至少一种已采集成功（OK）的证据。
    - ``generates_content=True`` 的条目只以输入齐备为就绪条件；证据缺失由
      ``PPAP-CHK-EVID-001`` 作为独立缺项暴露，不在此处合并（四类状态分开，§8.1）。
    """
    missing_inputs: list[str] = []
    for dataset in spec.required_inputs:
        kept, _excluded = _input_records(task_row, input_rows, dataset)
        if not kept:
            missing_inputs.append(dataset)
    evidence_states = [_evidence_state(source_type, evidence_rows) for source_type in spec.evidence_types]
    usable_evidence = [item for item in evidence_states if item["state"] == READ_OK]
    if spec.generates_content:
        data_ready = not missing_inputs
    else:
        data_ready = not missing_inputs and bool(usable_evidence)
    return {
        "element_id": spec.element_id,
        "required_inputs": list(spec.required_inputs),
        "missing_inputs": missing_inputs,
        "evidence_types": list(spec.evidence_types),
        "evidence_states": evidence_states,
        "missing_evidence": [
            item["source_type"]
            for item in evidence_states
            if item["state"] in ("NOT_COLLECTED", READ_EMPTY, READ_ERROR)
        ],
        "broken_evidence": [item["source_type"] for item in evidence_states if item["state"] == READ_ERROR],
        "truncated_evidence": [
            item["source_type"] for item in evidence_states if item["truncated_ids"]
        ],
        "data_ready": data_ready,
    }


def _snapshot_document(
    task_row: PpapTaskRow,
    element_rows: Sequence[PpapElementRow],
    input_rows: Sequence[PpapInputRow],
    evidence_rows: Sequence[PpapEvidenceRow],
) -> dict[str, Any]:
    """编制输入快照：任务身份 + 条目适用性/内容指纹 + 输入 + 证据指纹 + 模板版本。

    刻意不含条目 ``status``：编制完成后的状态流转不应让已产出的产物版本失效。
    也刻意不含产物清单本身，否则"生成产物"会改变快照形成自指，导致同一份输入
    永远无法命中同一版本（幂等要求见设计说明 §7）。
    """
    elements = [
        {
            "element_id": row.element_id,
            "requirement": row.requirement,
            "applicability_basis": row.applicability_basis or "",
            "content_hash": (
                ppap_storage.sha256_json(dict(row.content_json or {}))
                if dict(row.content_json or {})
                else ""
            ),
        }
        for row in sorted(element_rows, key=lambda item: item.element_id)
    ]
    inputs = [
        {
            "dataset": row.dataset,
            "record_key": row.record_key,
            "part_side": row.part_side,
            "payload_hash": ppap_storage.sha256_json(dict(row.payload_json or {})),
            "confirmed_by": row.confirmed_by or "",
            "confirmed_at": _iso(row.confirmed_at),
        }
        for row in sorted(input_rows, key=lambda item: (item.dataset, item.record_key, item.part_side))
    ]
    evidence = [
        {
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_version": row.source_version or "",
            "read_status": row.read_status,
            "truncated": bool(row.truncated),
            "content_hash": row.content_hash or "",
        }
        for row in sorted(
            evidence_rows, key=lambda item: (item.source_type, item.source_id, item.dedup_key)
        )
    ]
    return {
        "scope": "compile_input",
        "task": {
            "task_id": task_row.task_id,
            "customer_id": task_row.customer_id or "",
            "customer_name": task_row.customer_name or "",
            "part_no": task_row.part_no or "",
            "part_name": task_row.part_name or "",
            "part_side": task_row.part_side or "",
            "drawing_version": task_row.drawing_version or "",
            "engineering_change_level": task_row.engineering_change_level or "",
            "submission_reason": task_row.submission_reason or "",
            "submission_level": task_row.submission_level or "",
        },
        "business_object": dict(task_row.business_object_json or {}),
        "template": {
            "template_id": task_row.template_id or "",
            "template_version": task_row.template_version or "",
            "template_source": task_row.template_source or "",
            "rule_version": ppap_template.RULE_VERSION,
        },
        "elements": elements,
        "inputs": inputs,
        "evidence": evidence,
    }


def _current_snapshot_hash(
    task_row: PpapTaskRow,
    element_rows: Sequence[PpapElementRow],
    input_rows: Sequence[PpapInputRow],
    evidence_rows: Sequence[PpapEvidenceRow],
) -> str:
    return ppap_storage.sha256_json(
        _snapshot_document(task_row, element_rows, input_rows, evidence_rows)
    )


def _latest_package_artifact(session: Any, task_id: str) -> PpapArtifactRow | None:
    return (
        session.query(PpapArtifactRow)
        .filter(
            PpapArtifactRow.task_id == task_id,
            PpapArtifactRow.scope == SCOPE_PACKAGE,
        )
        .order_by(PpapArtifactRow.version.desc())
        .first()
    )


def _open_blocker_count(session: Any, task_id: str) -> int:
    return int(
        session.query(PpapIssueRow)
        .filter(
            PpapIssueRow.task_id == task_id,
            PpapIssueRow.status == ISSUE_OPEN,
            PpapIssueRow.blocks_approval.is_(True),
        )
        .count()
    )


def _compile_facts(task_id: str) -> dict[str, Any]:
    """读"最近产物快照哈希"与"未关闭阻断项数"。

    查不到（数据库不可用）时显式返回 ``ok=False``，调用方不得据此宣称已编制或已就绪。
    """
    if not _text(task_id):
        return {"ok": False, "snapshot_hash": "", "open_blockers": 0, "error": "task_id_empty"}
    try:
        with SessionLocal() as session:
            artifact = _latest_package_artifact(session, task_id)
            blockers = _open_blocker_count(session, task_id)
        return {
            "ok": True,
            "snapshot_hash": _text(artifact.input_snapshot_hash) if artifact is not None else "",
            "artifact_id": _text(artifact.artifact_id) if artifact is not None else "",
            "artifact_version": int(artifact.version or 0) if artifact is not None else 0,
            "open_blockers": blockers,
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - 数据库异常时不伪造状态
        logger.warning("PPAP compile facts unavailable for task %s: %s", task_id, exc)
        return {"ok": False, "snapshot_hash": "", "open_blockers": 0, "error": str(exc)}


def _any_requirement_confirmed(element_rows: Sequence[PpapElementRow]) -> bool:
    return any(_text(row.requirement) != ppap_template.REQ_UNCONFIRMED for row in element_rows)


def _compute_run_state(
    task_row: PpapTaskRow,
    element_rows: Sequence[PpapElementRow],
    input_rows: Sequence[PpapInputRow],
    evidence_rows: Sequence[PpapEvidenceRow],
    *,
    compile_facts: Mapping[str, Any] | None = None,
) -> str:
    """任务运行状态（设计说明 §8.1 第一类状态；与包审核、客户处理分开）。

    - ``NOT_STARTED``：适用性未确认且没有任何输入。
    - ``RUNNING``：存在未确认适用性，或有 submit 条目资料未齐。
    - ``BLOCKED``：资料已齐但存在未关闭的阻断项（检查发现）。
    - ``COMPILED``：最近产物版本的输入快照与当前输入完全一致。
    - ``READY``：资料齐全且无阻断项，可以编制。

    READY 只以 ``submit`` 条目的资料齐备为门槛：``retain``/``on_request`` 未满足
    由检查器作为独立缺项提示，不阻断 READY（设计说明 §5.3 的未满足语义）。
    无法核对产物/阻断项时不宣称 COMPILED 或 READY——宁可停在 RUNNING。
    """
    if not element_rows:
        return RUN_NOT_STARTED
    if not _any_requirement_confirmed(element_rows) and not list(input_rows):
        return RUN_NOT_STARTED

    unconfirmed = [
        row.element_id
        for row in element_rows
        if _text(row.requirement) == ppap_template.REQ_UNCONFIRMED
    ]
    unmet: list[str] = []
    for row in element_rows:
        if not ppap_template.requirement_blocks_approval(_text(row.requirement)):
            continue
        if _text(row.status) == ppap_template.EL_NOT_APPLICABLE:
            continue
        spec = ppap_template.element_spec(row.element_id)
        if spec is None:
            continue
        if not _element_readiness(task_row, spec, input_rows, evidence_rows)["data_ready"]:
            unmet.append(row.element_id)

    if unconfirmed or unmet:
        return RUN_RUNNING

    facts = dict(compile_facts) if compile_facts is not None else _compile_facts(task_row.task_id)
    if not facts.get("ok"):
        return RUN_RUNNING
    if int(facts.get("open_blockers") or 0) > 0:
        return RUN_BLOCKED
    if _text(facts.get("snapshot_hash")) and _text(facts.get("snapshot_hash")) == (
        _current_snapshot_hash(task_row, element_rows, input_rows, evidence_rows)
    ):
        return RUN_COMPILED
    return RUN_READY


def _compute_coverage(
    task_row: PpapTaskRow,
    element_rows: Sequence[PpapElementRow],
    input_rows: Sequence[PpapInputRow],
    evidence_rows: Sequence[PpapEvidenceRow],
) -> dict[str, Any]:
    """覆盖度：证据三态计数、按数据集的输入（含被侧别隔离排除数）、条目就绪度、适用性。

    以现有 ``coverage_json`` 为基，保留 ``audit_trail`` 与其它已写入的键，
    只覆盖由当前数据算出的分区。
    """
    data = dict(task_row.coverage_json or {})

    totals = {"total": len(evidence_rows), "ok": 0, "empty": 0, "error": 0, "truncated": 0}
    by_source_type: dict[str, dict[str, int]] = {}
    for row in evidence_rows:
        bucket = by_source_type.setdefault(
            _text(row.source_type) or "unknown",
            {"total": 0, "ok": 0, "empty": 0, "error": 0, "truncated": 0},
        )
        bucket["total"] += 1
        status = _text(row.read_status)
        if status == READ_OK:
            totals["ok"] += 1
            bucket["ok"] += 1
        elif status == READ_EMPTY:
            totals["empty"] += 1
            bucket["empty"] += 1
        else:
            totals["error"] += 1
            bucket["error"] += 1
        if bool(row.truncated):
            totals["truncated"] += 1
            bucket["truncated"] += 1
    data["evidence"] = {**totals, "by_source_type": by_source_type}

    excluded_total = 0
    dataset_rows: list[dict[str, Any]] = []
    for dataset in ppap_template.ALL_DATASETS:
        kept, excluded = _input_records(task_row, input_rows, dataset)
        excluded_total += excluded
        dataset_rows.append(
            {
                "dataset": dataset,
                "record_count": len(kept),
                "excluded_by_side": excluded,
                "confirmed_by": sorted(
                    {_text(row.confirmed_by) for row in kept if _text(row.confirmed_by)}
                ),
            }
        )
    data["inputs"] = {
        "total": len(input_rows),
        "excluded_by_side": excluded_total,
        "by_dataset": dataset_rows,
    }

    element_rows_out: list[dict[str, Any]] = []
    ready_count = 0
    for row in sorted(element_rows, key=lambda item: item.element_id):
        spec = ppap_template.element_spec(row.element_id)
        if spec is None:
            element_rows_out.append({"element_id": row.element_id, "spec_missing": True})
            continue
        readiness = _element_readiness(task_row, spec, input_rows, evidence_rows)
        blocks = ppap_template.requirement_blocks_approval(_text(row.requirement))
        if blocks and readiness["data_ready"]:
            ready_count += 1
        element_rows_out.append(
            {
                "element_id": row.element_id,
                "requirement": _text(row.requirement),
                "status": _text(row.status),
                "blocks_approval_when_unmet": blocks,
                "data_ready": readiness["data_ready"],
                "missing_inputs": readiness["missing_inputs"],
                "missing_evidence": readiness["missing_evidence"],
                "broken_evidence": readiness["broken_evidence"],
                "truncated_evidence": readiness["truncated_evidence"],
            }
        )
    data["elements"] = {
        "total": len(element_rows),
        "submit_ready": ready_count,
        "submit_total": sum(
            1
            for row in element_rows
            if ppap_template.requirement_blocks_approval(_text(row.requirement))
        ),
        "items": element_rows_out,
    }
    data["applicability"] = _applicability_summary(element_rows)
    if not isinstance(data.get("checks"), dict):
        data["checks"] = {}
    data["updated_at"] = ppap_storage.now_iso()
    return data


# ----------------------------------------------------------------------
# 编制：纯函数内容生成，持久化只由受控任务操作调用
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class DraftContext:
    task: PpapTaskRow
    elements: Sequence[PpapElementRow]
    inputs: Sequence[PpapInputRow]
    evidence: Sequence[PpapEvidenceRow]
    rules: Mapping[str, Any]

    def records(self, dataset: str) -> list[dict[str, Any]]:
        return [
            {**payload, "input_id": row.input_id, "source_ref": row.source_ref,
             "task_id": row.task_id, "record_key": row.record_key, "source_type": row.source_type,
             "part_side": row.part_side, "confirmed_by": row.confirmed_by}
            for row, payload in _input_payloads(self.task, self.inputs, dataset)
        ]

    def references(self, element_id: str) -> list[dict[str, Any]]:
        spec = ppap_template.element_spec(element_id)
        types = set(spec.evidence_types if spec else ())
        return [_evidence_dict(row) for row in self.evidence
                if row.source_type in types and row.read_status == READ_OK
                and not row.truncated
                and (element_id in (row.element_ids_json or []) or
                     (not row.element_ids_json and row.source_type not in HUMAN_EVIDENCE_TYPES))]

    def base(self) -> dict[str, Any]:
        fields = ("customer_id", "customer_name", "part_no", "part_name", "part_side",
                  "drawing_version", "engineering_change_level", "submission_reason",
                  "submission_level", "data_mode")
        task = _task_dict(self.task)
        return {key: task[key] for key in fields}


def _reference_draft(ctx: DraftContext, element_id: str) -> dict[str, Any]:
    return {"references": ctx.references(element_id),
            "notice": "仅归集原件引用，不生成原始批准、资质、图纸或签字。"}


def draft_submission_checklist(ctx: DraftContext) -> dict[str, Any]:
    return {"rows": [{"element_id": row.element_id, "title": row.title,
                      "requirement": row.requirement, "applicability_basis": row.applicability_basis}
                     for row in ctx.elements]}


def draft_design_records(ctx: DraftContext) -> dict[str, Any]:
    return _reference_draft(ctx, "design_records")


def draft_engineering_change_documents(ctx: DraftContext) -> dict[str, Any]:
    return {"rows": ctx.records(ppap_template.DS_CHANGE_RECORD),
            "references": ctx.references("engineering_change_documents")}


def draft_customer_engineering_approval(ctx: DraftContext) -> dict[str, Any]:
    return _reference_draft(ctx, "customer_engineering_approval")


def draft_dfmea(ctx: DraftContext) -> dict[str, Any]:
    return _reference_draft(ctx, "dfmea")


def draft_process_flow_diagram(ctx: DraftContext) -> dict[str, Any]:
    return {"operations": ctx.records(ppap_template.DS_PROCESS_OPERATION),
            "relations": ctx.records(ppap_template.DS_PROCESS_RELATION),
            "references": ctx.references("process_flow_diagram")}


def draft_pfmea(ctx: DraftContext) -> dict[str, Any]:
    return {"operations": ctx.records(ppap_template.DS_PROCESS_OPERATION),
            "characteristics": ctx.records(ppap_template.DS_CHARACTERISTIC),
            "references": ctx.references("pfmea"),
            "pending_confirmations": ["工序与特性映射、失效模式、原因、控制措施和风险评级需要工程基准；现有输入契约没有这些字段，不能推断。"],
            "engineering_state": "NEEDS_ENGINEERING_INPUT"}


def draft_control_plan(ctx: DraftContext) -> dict[str, Any]:
    return {"operations": ctx.records(ppap_template.DS_PROCESS_OPERATION),
            "characteristics": ctx.records(ppap_template.DS_CHARACTERISTIC),
            "rows": ctx.records(ppap_template.DS_INSPECTION_VALUE),
            "references": ctx.references("control_plan"),
            "pending_confirmations": ["检验方法、频次、样本量、失控反应和工序特性映射须由工程基准确认；不能由实测值推测。"],
            "engineering_state": "NEEDS_ENGINEERING_INPUT"}


def _metric_entries(result: Mapping[str, Any], metrics: Sequence[str],
                    ctx: DraftContext, study: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = _as_dict(result.get("value"))
    return [{**ppap_calc.classify_metric(metric, values.get(metric), ctx.rules),
             "study_id": study.get("study_id"), "characteristic_id": study.get("characteristic_id"),
             "calc_state": result.get("state"), "problems": result.get("problems", []),
             "notes": result.get("notes", []), "flags": result.get("flags", [])}
            for metric in metrics]


def _stable_calculation(result: Mapping[str, Any]) -> dict[str, Any]:
    # 运行时间由 compile_runs 留痕，不进入内容快照而破坏幂等。
    return {key: value for key, value in result.items() if key != "calculated_at"}


def draft_msa(ctx: DraftContext) -> dict[str, Any]:
    studies, calculations = [], []
    for study in ctx.records(ppap_template.DS_MSA_STUDY):
        specification = ppap_characteristics.resolve_characteristic(
            ctx.records(ppap_template.DS_CHARACTERISTIC), characteristic_id=study.get("characteristic_id", ""),
            part_side=study.get("part_side", ""), task_id=study.get("task_id", ""))
        if study.get("method") != "average_range":
            result = {"state": "UNSUPPORTED_CONFIG", "value": {},
                      "problems": ["MSA 方法未明确确认为 average_range；其他方法尚未实现。"]}
        elif not specification["ok"]:
            result = {"state": "INVALID_INPUT", "value": {}, "problems": [
                "MSA 引用的特性规格未唯一确认：" + specification["code"]],
                "spec_sources": specification["sources"]}
        else:
            result = _stable_calculation(ppap_calc.average_range_grr(study.get("measurements"), tolerance=study.get("tolerance")))
        studies.append({"study_id": study.get("study_id"),
                        "characteristic_id": study.get("characteristic_id"),
                        "raw_data": study, "result": result})
        calculations.extend(_metric_entries(result, ("percent_ev", "percent_av", "percent_grr",
                                                     "percent_tolerance", "ndc"), ctx, study))
    return {"studies": studies, "calculations": calculations}


def draft_dimensional_results(ctx: DraftContext) -> dict[str, Any]:
    chars = ctx.records(ppap_template.DS_CHARACTERISTIC)
    originals = ctx.records(ppap_template.DS_INSPECTION_VALUE)
    prepared = ppap_characteristics.prepare_dimensional_comparison(chars, originals)
    rows = prepared["rows"]
    result = _stable_calculation(ppap_calc.dimensional_results(rows))
    result["problems"].extend(prepared["problems"])
    return {"rows": result.get("rows", []), "result": result,
            "characteristics": chars, "raw_rows": originals, "comparison_rows": rows,
            "specification_conflicts": prepared["specification_conflicts"]}


def draft_material_performance_results(ctx: DraftContext) -> dict[str, Any]:
    return {"rows": ctx.records(ppap_template.DS_MATERIAL_EVIDENCE),
            "references": ctx.references("material_performance_results"),
            "notice": "原始材料/性能记录汇总；不代表符合性或客户批准。"}


def draft_initial_process_study(ctx: DraftContext) -> dict[str, Any]:
    studies, calculations = [], []
    for study in ctx.records(ppap_template.DS_PROCESS_STUDY):
        specification = ppap_characteristics.resolve_characteristic(
            ctx.records(ppap_template.DS_CHARACTERISTIC), characteristic_id=study.get("characteristic_id", ""),
            part_side=study.get("part_side", ""), task_id=study.get("task_id", ""))
        conflicts = (ppap_characteristics.compare_reference_definition(study, specification["characteristic"])
                     if specification["ok"] else [])
        if not specification["ok"] or conflicts:
            result = {"state": "INVALID_INPUT", "value": {}, "problems": [
                "过程研究引用的特性规格未唯一确认或与研究规格冲突：" + str(conflicts or specification["code"])],
                "spec_sources": specification["sources"]}
        else:
            result = _stable_calculation(ppap_calc.capability_indices(study.get("values"), usl=study.get("usl"),
                                                 lsl=study.get("lsl"), target=study.get("target"),
                                                 subgroups=study.get("subgroups")))
        studies.append({"study_id": study.get("study_id"),
                        "characteristic_id": study.get("characteristic_id"),
                        "raw_data": study, "result": result})
        calculations.extend(_metric_entries(result, ("cp", "cpk", "pp", "ppk"), ctx, study))
    return {"studies": studies, "calculations": calculations}


def draft_qualified_laboratory_documentation(ctx: DraftContext) -> dict[str, Any]:
    return _reference_draft(ctx, "qualified_laboratory_documentation")


def draft_appearance_approval_report(ctx: DraftContext) -> dict[str, Any]:
    return _reference_draft(ctx, "appearance_approval_report")


def _physical_draft(ctx: DraftContext, element_id: str) -> dict[str, Any]:
    return {"rows": [row for row in ctx.records(ppap_template.DS_PHYSICAL_RECORD)
                     if row.get("record_type") == element_id],
            "references": ctx.references(element_id),
            "notice": "此表仅登记实物流转证据，生成文件不等于已提供实物。"}


def draft_sample_production_part(ctx: DraftContext) -> dict[str, Any]:
    return _physical_draft(ctx, "sample_production_part")


def draft_master_sample(ctx: DraftContext) -> dict[str, Any]:
    return _physical_draft(ctx, "master_sample")


def draft_checking_aid(ctx: DraftContext) -> dict[str, Any]:
    return _physical_draft(ctx, "checking_aid")


def draft_customer_specific_requirements(ctx: DraftContext) -> dict[str, Any]:
    return {"rows": ctx.records(ppap_template.DS_CUSTOMER_REQUIREMENT),
            "references": ctx.references("customer_specific_requirements")}


def draft_psw(ctx: DraftContext) -> dict[str, Any]:
    return {"submission": {"reason": ctx.task.submission_reason, "level": ctx.task.submission_level},
            "customer_handling": ctx.records(ppap_template.DS_CUSTOMER_HANDLING),
            "signature": None, "customer_approval": None,
            "notice": "PSW 草稿：没有供方声明签字或客户批准，文件生成不等于接受。"}


def draft_attachment_index(ctx: DraftContext) -> dict[str, Any]:
    return {"rows": [_evidence_dict(row) for row in ctx.evidence]}


def draft_equipment_packaging(ctx: DraftContext) -> dict[str, Any]:
    return {"rows": ctx.records(ppap_template.DS_PACKAGING_EQUIPMENT)}


def draft_dimensional_layout(ctx: DraftContext) -> dict[str, Any]:
    return _reference_draft(ctx, "dimensional_layout")


DRAFTERS = {name: globals()[f"draft_{name}"] for name in ppap_template.ELEMENT_IDS}


def draft_element(ctx: DraftContext, element: PpapElementRow) -> dict[str, Any]:
    spec = ppap_template.element_spec(element.element_id)
    if spec is None:
        raise ValueError("unknown_element")
    readiness = _element_readiness(ctx.task, spec, ctx.inputs, ctx.evidence)
    content = {"base_info": ctx.base(), "template_source": ppap_template.TEMPLATE_SOURCE,
               "approval_state": "DRAFT", "rule_hash": ppap_storage.sha256_json(dict(ctx.rules)),
               **DRAFTERS[element.element_id](ctx)}
    if element.requirement == ppap_template.REQ_NOT_APPLICABLE:
        status, reason = ppap_template.EL_NOT_APPLICABLE, element.applicability_basis
        content = {"base_info": ctx.base(), "applicability_basis": element.applicability_basis,
                   "template_source": ppap_template.TEMPLATE_SOURCE, "approval_state": "DRAFT"}
    elif not element.requirement:
        status, reason = ppap_template.EL_APPLICABILITY_UNCONFIRMED, "适用性尚未确认"
    elif not readiness["data_ready"]:
        status, reason = ppap_template.EL_WAITING_DATA, "缺少输入或可用原件证据"
    elif content.get("engineering_state") == "NEEDS_ENGINEERING_INPUT":
        status, reason = ppap_template.EL_WAITING_DATA, "；".join(content["pending_confirmations"])
    elif spec.category == ppap_template.CAT_PHYSICAL and not content.get("rows"):
        status, reason = ppap_template.EL_WAITING_DATA, "缺少对应实物类型的登记记录"
    elif content.get("studies") and any(study["result"].get("state") not in ("OK", "INCOMPLETE")
                                        for study in content["studies"]):
        status, reason = ppap_template.EL_WAITING_DATA, "原始研究数据或方法不满足计算条件"
    elif element.element_id == "dimensional_results" and (content["result"]["state"] != "OK" or any(
            content["result"]["summary"].get(key) for key in ("missing_value", "missing_spec", "ng"))):
        status, reason = ppap_template.EL_WAITING_DATA, "存在尺寸异常、缺值、缺规格或单位冲突，需要核对或正式偏差依据"
    elif content.get("calculations") and any(entry.get("acceptance_state") != "MEETS_RULE"
            for entry in content["calculations"] if entry.get("metric") in (
                "percent_grr", "ndc", "cp", "cpk", "pp", "ppk")):
        status, reason = ppap_template.EL_WAITING_DATA, "研究判定规则未确认、指标缺值或未满足规则；不作为已完成提交资料"
    else:
        status, reason = ppap_template.EL_DRAFTED, "已按有来源的输入生成未批准草稿"
    return {"status": status, "status_reason": reason, "content": _jsonable(content),
            "readiness": readiness,
            "source_refs": {"input_ids": [row.input_id for row in ctx.inputs
                                            if _side_matches(row.part_side, ctx.task.part_side)],
                            "evidence_ids": [row["evidence_id"] for row in content.get("references", [])]}}


# ----------------------------------------------------------------------
# 检查与发现同步。豁免保留记录，不能消除检查器仍报告的阻断。
# ----------------------------------------------------------------------

def _failure(code: str, message: str = "") -> dict[str, Any]:
    return {"success": False, "ok": False, "code": code, "error": message or code}


def _sync_findings(session: Any, task_id: str, findings: Sequence[Mapping[str, Any]], actor: str) -> None:
    existing = {row.dedup_key: row for row in _load_issue_rows(session, task_id)}
    active = set()
    fields = ("kind", "severity", "check_id", "element_id", "field_path", "title", "detail",
              "basis", "required_evidence", "responsible_role", "blocks_approval")
    for finding in findings:
        key = _text(finding.get("dedup_key"))
        active.add(key)
        row = existing.get(key)
        if row is None:
            row = PpapIssueRow(issue_id=_gen_id("PISS"), task_id=task_id, dedup_key=key)
            session.add(row)
        for field in fields:
            setattr(row, field, bool(finding.get(field)) if field == "blocks_approval"
                    else _text(finding.get(field)))
        # 重新检出必须重开；旧处理人/理由保留用于审计。
        row.status = ISSUE_OPEN
        row.resolved_at = None
        row.updated_at = _now()
    for key, row in existing.items():
        if key not in active and row.status == ISSUE_OPEN:
            row.status = ISSUE_RESOLVED
            row.resolved_by = actor
            row.resolution_note = "重新检查未再检出该发现"
            row.resolved_at = _now()
            row.updated_at = _now()


def _check_in_session(session: Any, task: PpapTaskRow, elements: Sequence[PpapElementRow],
                      inputs: Sequence[PpapInputRow], evidence: Sequence[PpapEvidenceRow],
                      rules: Mapping[str, Any], actor: str) -> dict[str, Any]:
    result = ppap_checks.run_all_checks(_build_checks_context(task, elements, inputs, evidence, rules))
    for row in elements:
        related = [item for item in result["findings"] if not item.get("element_id")
                   or item.get("element_id") == row.element_id]
        row.check_result_json = {"findings": related,
                                 "blocks_approval": bool(ppap_checks.blocking_findings(related))}
    _sync_findings(session, task.task_id, result["findings"], actor)
    coverage = _compute_coverage(task, elements, inputs, evidence)
    coverage["checks"] = result["summary"]
    task.coverage_json = coverage
    return result


def run_checks(task_id: str, *, actor: str) -> dict[str, Any]:
    if not _text(actor):
        return _failure("actor_required")
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return _failure("task_not_found")
        mode_problem = _validate_task_provenance(session, task)
        if mode_problem:
            return mode_problem
        result = _check_in_session(session, task, elements, _load_input_rows(session, task_id),
                                   _load_evidence_rows(session, task_id), ppap_calc.load_acceptance_rules(), actor)
        task.coverage_json = _append_audit(task.coverage_json, {"action": "checks_run", "by": actor})
        task.updated_at = _now()
        session.commit()
        return {"success": True, "task_id": task_id, **result, "written": "local_ppap_store"}


def get_missing_items(task_id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return _failure("task_not_found")
        inputs, evidence = _load_input_rows(session, task_id), _load_evidence_rows(session, task_id)
        result = ppap_checks.run_all_checks(_build_checks_context(task, elements, inputs, evidence,
                                                                  ppap_calc.load_acceptance_rules()))
        serialized_inputs = [_input_dict(row) for row in inputs]
        for finding in result["findings"]:
            finding["navigation"] = ppap_assistance.finding_navigation(finding, serialized_inputs)
        readiness = [_element_readiness(task, ppap_template.element_spec(row.element_id), inputs, evidence)
                     for row in elements if ppap_template.element_spec(row.element_id)]
        return {"ok": True, "task_id": task_id, **result, "readiness": readiness,
                "authority": "ppap_local", "read_only": True}


def list_issues(task_id: str, *, status: str = "", limit: int = 100) -> dict[str, Any]:
    with SessionLocal() as session:
        if session.get(PpapTaskRow, task_id) is None:
            return _failure("task_not_found")
        rows = [row for row in _load_issue_rows(session, task_id) if not status or row.status == status]
        return {"ok": True, "task_id": task_id, "items": [_issue_dict(row) for row in rows[:_clamp_limit(limit)]],
                "total": len(rows)}


def resolve_issue(task_id: str, issue_id: str, *, actor: str, status: str = ISSUE_RESOLVED,
                  note: str = "") -> dict[str, Any]:
    if not _text(actor) or status not in (ISSUE_RESOLVED, ISSUE_WAIVED) or not _text(note):
        return _failure("invalid_resolution", "处理人、明确处理状态及依据必填")
    current = get_missing_items(task_id)
    if not current.get("ok"):
        return current
    with SessionLocal() as session:
        row = session.get(PpapIssueRow, issue_id)
        if row is None or row.task_id != task_id:
            return _failure("issue_not_found")
        if status == ISSUE_RESOLVED and any(f["dedup_key"] == row.dedup_key for f in current["findings"]):
            return _failure("issue_still_present", "仍检出该发现，必须先修正数据；接受风险请显式登记 WAIVED")
        row.status, row.resolution_note, row.resolved_by = status, note, actor
        row.resolved_at, row.updated_at = _now(), _now()
        task = session.get(PpapTaskRow, task_id)
        task.coverage_json = _append_audit(task.coverage_json,
                                          {"action": "issue_handled", "by": actor,
                                           "detail": {"issue_id": issue_id, "status": status, "note": note}})
        session.commit()
        return {"success": True, "issue": _issue_dict(row), "written": "local_ppap_store"}


# ----------------------------------------------------------------------
# 原子编制与幂等；输入快照使用确定性编制后的内容，避免自指。
# ----------------------------------------------------------------------

def compile_task(task_id: str, *, actor: str, request_key: str = "") -> dict[str, Any]:
    from sqlalchemy import text
    from app.services import ppap_export

    if not _text(actor):
        return _failure("actor_required")
    with SessionLocal() as session:
        # SQLite 先取得写事务；PostgreSQL 锁任务行，版本与请求键在同一事务分配。
        if session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        else:
            session.query(PpapTaskRow).filter_by(task_id=task_id).with_for_update().first()
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return _failure("task_not_found")
        if not task.authorized_by:
            return _failure("task_not_authorized")
        mode_problem = _validate_task_provenance(session, task)
        if mode_problem:
            return mode_problem
        inputs, evidence = _load_input_rows(session, task_id), _load_evidence_rows(session, task_id)
        rules = ppap_calc.load_acceptance_rules()
        ctx = DraftContext(task, elements, inputs, evidence, rules)
        for row in elements:
            drafted = draft_element(ctx, row)
            row.content_json, row.source_refs_json = drafted["content"], drafted["source_refs"]
            row.status, row.status_reason = drafted["status"], drafted["status_reason"]
        snapshot_hash = _current_snapshot_hash(task, elements, inputs, evidence)
        key = _text(request_key) or f"auto-{snapshot_hash[:24]}"
        if len(key) > 120:
            return _failure("invalid_request_key")
        prior = session.query(PpapCompileRunRow).filter_by(task_id=task_id, request_key=key).first()
        if prior and prior.input_snapshot_hash != snapshot_hash:
            return _failure("idempotency_conflict", "请求键已用于另一份输入快照")
        if prior is None:
            prior = session.query(PpapCompileRunRow).filter_by(
                task_id=task_id, input_snapshot_hash=snapshot_hash,
                template_id=task.template_id, template_version=task.template_version,
                rule_version=ppap_template.RULE_VERSION).filter(
                    PpapCompileRunRow.status.in_((RUN_COMPILED, RUN_BLOCKED)),
                    PpapCompileRunRow.trigger != "evidence_collect").first()
        if prior and prior.status in (RUN_COMPILED, RUN_BLOCKED):
            # 上面的纯草稿推导不应改变已审核条目的持久化状态。
            result = _run_dict(prior, include_steps=True)
            artifact = session.query(PpapArtifactRow).filter_by(task_id=task_id, run_id=prior.run_id).first()
            artifact_result = _artifact_dict(artifact) if artifact else None
            session.rollback()
            return {"success": True, "idempotent_replay": True, "run": result,
                    "artifact": artifact_result}
        if prior:
            return _failure("run_not_replayable", "该请求此前失败或仍在运行，请使用新的显式请求键重试")
        version = max([row.version for row in _load_artifact_rows(session, task_id)
                       if row.scope == SCOPE_PACKAGE] or [0]) + 1
        run = PpapCompileRunRow(run_id=_gen_id("PCR"), task_id=task_id, request_key=key,
                               input_snapshot_hash=snapshot_hash, template_id=task.template_id,
                               template_version=task.template_version, rule_version=ppap_template.RULE_VERSION,
                               status=RUN_RUNNING, requested_by=actor)
        session.add(run)
        for row in elements:
            row.drafted_at, row.updated_at = _now(), _now()
        check = _check_in_session(session, task, elements, inputs, evidence, rules, actor)
        task.input_snapshot_hash = snapshot_hash
        snapshot = _snapshot_document(task, elements, inputs, evidence)
        steps = [{"step": "draft", "status": "COMPLETED", "element_count": len(elements)},
                 {"step": "checks", "status": "COMPLETED", **check["summary"]}]
        # 重用先前实际取证运行的调用记录，不捏造本次发生了新的 AIP 请求。
        history = session.query(PpapCompileRunRow).filter_by(task_id=task_id).order_by(
            PpapCompileRunRow.started_at.desc()).all()
        chain = [{**entry, "reused_from_run_id": old.run_id}
                 for old in history if old.run_id != run.run_id
                 for entry in (old.call_chain_json or []) if entry.get("reused_from_run_id") is None]
        run_identity = {"run_id": run.run_id, "template_id": task.template_id,
                        "template_version": task.template_version}
        try:
            exported = ppap_export.export_package(task=_task_dict(task),
                elements=[_element_dict(row) for row in elements],
                evidence=[_evidence_dict(row) for row in evidence], issues=check["findings"],
                version=version, snapshot_hash=snapshot_hash, compile_run_id=run.run_id)
            artifact = PpapArtifactRow(artifact_id=_gen_id("PART"), task_id=task_id, run_id=run.run_id,
                scope=SCOPE_PACKAGE, version=version, kind="excel", file_name=exported["file_name"],
                storage_rel_path=exported["rel_path"], content_hash=exported["content_hash"],
                byte_size=exported["byte_size"], status=ART_DRAFT, input_snapshot_hash=snapshot_hash,
                template_id=task.template_id, template_version=task.template_version, created_by=actor,
                issue_count_json={**check["summary"], "rule_hash": ppap_storage.sha256_json(rules)})
            session.add(artifact)
            steps.append({"step": "export", "status": "COMPLETED", "artifact_id": artifact.artifact_id})
            run.status = RUN_BLOCKED if check["summary"]["blocks_approval"] else RUN_COMPILED
            run.steps_json, run.call_chain_json = steps, chain
            run.summary_json = {**check["summary"], "artifact_id": artifact.artifact_id,
                                "artifact_version": version, "snapshot": snapshot,
                                "rule_hash": ppap_storage.sha256_json(rules),
                                "template_source": ppap_template.TEMPLATE_SOURCE}
            run.finished_at = _now()
            task.run_state = run.status
            task.package_state, task.customer_state = PKG_NOT_SUBMITTED, CUST_NOT_SUBMITTED
            task.coverage_json = _append_audit(task.coverage_json,
                {"action": "compiled", "by": actor, "detail": {"run_id": run.run_id, "version": version}})
            task.updated_at = _now()
            session.commit()
        except Exception as exc:
            # 提交异常会使 Session 失效；先回滚全部草稿和产物行，再独立留痕。
            # 已写出的文件不会成为可下载产物，也不会覆盖已有冻结版本。
            failure_data = dict(**run_identity, task_id=task_id, request_key=key,
                input_snapshot_hash=snapshot_hash, rule_version=ppap_template.RULE_VERSION,
                status=RUN_FAILED, requested_by=actor, finished_at=_now(),
                error_json={"code": "compile_failed", "exception_type": type(exc).__name__},
                steps_json=steps + [{"step": "commit_or_export", "status": "FAILED"}],
                call_chain_json=chain)
            session.rollback()
            try:
                with SessionLocal() as failure_session:
                    failed_run = PpapCompileRunRow(**failure_data)
                    failure_session.add(failed_run)
                    failed_task = failure_session.get(PpapTaskRow, task_id)
                    if failed_task:
                        failed_task.run_state = RUN_FAILED
                        failed_task.coverage_json = _append_audit(failed_task.coverage_json,
                            {"action": "compile_failed", "by": actor,
                             "detail": {"run_id": failed_run.run_id}})
                    failure_session.commit()
                    failed_result = _run_dict(failed_run, include_steps=True)
            except Exception:
                return _failure("compile_failed", "编制失败且运行记录未能保存，请检查数据库与存储状态")
            return {**_failure("compile_failed", "编制失败，已回滚本次数据库写入；没有创建批准产物"),
                    "run": failed_result}
        return {"success": True, "idempotent_replay": False, "run": _run_dict(run, include_steps=True),
                "artifact": _artifact_dict(artifact), "written": "local_ppap_store"}


def _task_mode_values(task: PpapTaskRow) -> dict[str, Any]:
    return {**{key: getattr(task, key, "") for key in TASK_FIELD_KEYS},
            "authorization_ref": task.authorization_ref,
            "business_object": dict(task.business_object_json or {})}


def _validate_package_reference(session: Any, target_mode: str, artifact_id: str,
                                ancestors: tuple[str, ...] = ()) -> dict[str, Any] | None:
    """Validate the recorded, version-specific reference graph without writes."""
    if artifact_id in ancestors:
        return _failure("package_reference_cycle", "资料包引用存在循环")
    if len(ancestors) >= 32:
        return _failure("package_reference_chain_too_deep", "资料包引用层级超过可核对范围")
    artifact = session.get(PpapArtifactRow, artifact_id)
    if artifact is None:
        return _failure("artifact_not_found")
    source_task = session.get(PpapTaskRow, artifact.task_id)
    if source_task is None:
        return _failure("source_task_not_found")
    source_mode = ppap_mode.task_mode(source_task)
    if source_mode != target_mode:
        return _failure("package_mode_mismatch", "测试资料包与正式资料包不能相互引用")
    run = session.get(PpapCompileRunRow, artifact.run_id)
    snapshot = _as_dict(_as_dict(run.summary_json if run else {}).get("snapshot"))
    if not snapshot:
        return _failure("package_provenance_unavailable", "引用包缺少编制来源快照，不能确认正式/测试隔离")
    problem = ppap_mode.validate_task_data(target_mode, {
        **_as_dict(snapshot.get("task")), "business_object": _as_dict(snapshot.get("business_object"))})
    if problem:
        return problem
    provenance = _as_dict(snapshot.get("provenance"))
    if (not isinstance(provenance.get("inputs"), list)
            or not isinstance(provenance.get("evidence"), list)
            or len(provenance["inputs"]) != len(_as_list(snapshot.get("inputs")))
            or len(provenance["evidence"]) != len(_as_list(snapshot.get("evidence")))):
        return _failure("package_provenance_unavailable", "引用包来源记录不完整，不能推断正式/测试分类；请重新编制")
    for item in _as_list(provenance.get("inputs")):
        record = _as_dict(item)
        payload = record.get("payload")
        if payload is None and target_mode == ppap_mode.MODE_REAL:
            # Legacy packages only store hashes. Verify the matching original
            # input if it still exists; otherwise provenance cannot be guessed.
            input_row = session.get(PpapInputRow, record.get("input_id"))
            old_hash = next((entry.get("payload_hash") for entry in _as_list(snapshot.get("inputs"))
                if entry.get("dataset") == getattr(input_row, "dataset", None)
                and entry.get("record_key") == getattr(input_row, "record_key", None)
                and entry.get("part_side") == getattr(input_row, "part_side", None)), None)
            if (input_row is None or input_row.task_id != source_task.task_id or not old_hash
                    or ppap_storage.sha256_json(input_row.payload_json or {}) != old_hash):
                return _failure("package_provenance_unavailable", "历史包未保存完整输入且原输入已变，不能推断来源；请重新编制")
            payload = input_row.payload_json
        problem = ppap_mode.validate_input_source(target_mode, record.get("source_type"), record.get("source_ref"), payload or {})
        if problem:
            return problem
    for item in _as_list(provenance.get("evidence")):
        record = _as_dict(item)
        source_type = record.get("source_type")
        source_id = record.get("source_id", "")
        payload = record.get("payload")
        if not source_type:
            # Legacy snapshots have the immutable locator but omit its type.
            evidence = session.get(PpapEvidenceRow, record.get("evidence_id"))
            if evidence is None:
                return _failure("package_provenance_unavailable", "历史引用来源无法核对，请重新编制")
            source_type = evidence.source_type
        if payload is None and target_mode == ppap_mode.MODE_REAL:
            evidence = session.get(PpapEvidenceRow, record.get("evidence_id"))
            old_evidence = next((entry for entry in _as_list(snapshot.get("evidence"))
                if entry.get("source_type") == source_type
                and entry.get("source_id") == getattr(evidence, "source_id", None)
                and entry.get("source_version") == (getattr(evidence, "source_version", None) or "")), None)
            if (evidence is None or evidence.task_id != source_task.task_id or old_evidence is None
                    or old_evidence.get("content_hash") != evidence.content_hash
                    or record.get("locator", "") != (evidence.locator or "")):
                return _failure("package_provenance_unavailable", "历史包未保存完整来源且原证据已变，不能推断来源；请重新编制")
            source_id, payload = evidence.source_id, evidence.payload_json
        problem = ppap_mode.validate_evidence_source(target_mode, source_type, source_id, record.get("locator", ""), payload)
        if problem:
            return problem
        if source_type == ppap_template.EV_INTERNAL_PACKAGE:
            problem = _validate_package_reference(session, target_mode, _text(record.get("locator")), (*ancestors, artifact_id))
            if problem:
                return problem
    return None


def _validate_task_provenance(session: Any, task: PpapTaskRow) -> dict[str, Any] | None:
    mode = ppap_mode.task_mode(task)
    problem = ppap_mode.validate_task_data(mode, _task_mode_values(task))
    if problem:
        return problem
    for row in _load_input_rows(session, task.task_id):
        problem = ppap_mode.validate_input_source(mode, row.source_type, row.source_ref, row.payload_json)
        if problem:
            return problem
    for row in _load_evidence_rows(session, task.task_id):
        problem = ppap_mode.validate_evidence_source(mode, row.source_type, row.source_id, row.locator, row.payload_json)
        if problem:
            return problem
        if row.source_type == ppap_template.EV_INTERNAL_PACKAGE:
            problem = _validate_package_reference(session, mode, row.locator)
            if problem:
                return problem
    return None


def _validate_artifact(session: Any, task: PpapTaskRow, artifact: PpapArtifactRow,
                       elements: Sequence[PpapElementRow]) -> dict[str, Any]:
    mode_problem = _validate_task_provenance(session, task)
    if mode_problem:
        return mode_problem
    inputs, evidence = _load_input_rows(session, task.task_id), _load_evidence_rows(session, task.task_id)
    for row in evidence:
        if row.source_type not in HUMAN_EVIDENCE_TYPES or row.read_status != READ_OK:
            continue
        try:
            if row.source_type == ppap_template.EV_USER_UPLOAD:
                actual = ppap_storage.sha256_bytes(ppap_storage.read_bytes(row.locator))
            else:
                source = session.get(PpapArtifactRow, row.locator)
                actual = ppap_storage.sha256_bytes(ppap_storage.read_bytes(source.storage_rel_path)) if source else ""
        except (ValueError, OSError):
            actual = ""
        if actual != row.content_hash:
            return _failure("stale_evidence_file", "已登记原件丢失或内容变化，请重新登记取证并编制")
    if artifact.input_snapshot_hash != _current_snapshot_hash(task, elements, inputs, evidence):
        return _failure("stale_artifact", "输入或适用性已变化，必须重新编制")
    rules = ppap_calc.load_acceptance_rules()
    if artifact.issue_count_json.get("rule_hash") != ppap_storage.sha256_json(rules):
        return _failure("stale_rules", "判定规则已变化，必须重新编制")
    check = ppap_checks.run_all_checks(_build_checks_context(task, elements, inputs, evidence, rules))
    if ppap_checks.blocking_findings(check["findings"]):
        return {**_failure("approval_blocked", "存在阻断项，不能提交或批准"), "blockers": check["findings"]}
    try:
        data = ppap_storage.read_bytes(artifact.storage_rel_path)
    except (OSError, ValueError):
        return _failure("artifact_unavailable")
    if ppap_storage.sha256_bytes(data) != artifact.content_hash:
        return _failure("artifact_hash_mismatch", "文件内容发生变化，不能审核或下载")
    return {"ok": True, "check": check}


def submit_review(task_id: str, *, actor: str, artifact_id: str = "", note: str = "") -> dict[str, Any]:
    if not _text(actor):
        return _failure("actor_required")
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return _failure("task_not_found")
        artifact = session.get(PpapArtifactRow, artifact_id) if artifact_id else _latest_package_artifact(session, task_id)
        if artifact is None or artifact.task_id != task_id or artifact.status != ART_DRAFT:
            return _failure("draft_artifact_required")
        latest = _latest_package_artifact(session, task_id)
        if latest is None or artifact.artifact_id != latest.artifact_id:
            return _failure("latest_artifact_required", "只能提交当前最新编制版本")
        valid = _validate_artifact(session, task, artifact, elements)
        if not valid.get("ok"):
            return valid
        pending = session.query(PpapReviewRow).filter_by(task_id=task_id, artifact_id=artifact.artifact_id,
                                                       decision=REVIEW_SUBMITTED).first()
        if task.package_state == PKG_PENDING_REVIEW and pending:
            return {"success": True, "review": _review_dict(pending), "idempotent_replay": True}
        review = PpapReviewRow(review_id=_gen_id("PREV"), task_id=task_id, artifact_id=artifact.artifact_id,
            artifact_version=artifact.version, input_snapshot_hash=artifact.input_snapshot_hash,
            decision=REVIEW_SUBMITTED, author=artifact.created_by, reviewer=actor,
            reviewer_role="quality_manager", comments=note,
            separation_warning="author_reviewer_same_account" if artifact.created_by == actor else "")
        session.add(review)
        task.package_state = PKG_PENDING_REVIEW
        task.coverage_json = _append_audit(task.coverage_json,
                                          {"action": "review_submitted", "by": actor,
                                           "detail": {"artifact_id": artifact.artifact_id}})
        session.commit()
        return {"success": True, "review": _review_dict(review), "written": "local_ppap_store"}


def decide_review(task_id: str, review_id: str, *, actor: str, decision: str,
                   note: str = "", reviewer_role: str = "quality_manager") -> dict[str, Any]:
    if not _text(actor) or reviewer_role != "quality_manager" or decision not in REVIEW_DECISIONS:
        return _failure("invalid_review_decision")
    if not _text(note):
        return _failure("review_comments_required")
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        request = session.get(PpapReviewRow, review_id)
        if task is None or request is None or request.task_id != task_id:
            return _failure("review_not_found")
        prior = session.query(PpapReviewRow).filter_by(task_id=task_id,
            artifact_id=request.artifact_id).filter(PpapReviewRow.decision.in_(REVIEW_DECISIONS)).order_by(
                PpapReviewRow.created_at.desc()).first()
        if prior:
            if prior.decision == decision and prior.reviewer == actor:
                return {"success": True, "review": _review_dict(prior), "idempotent_replay": True}
            return _failure("review_already_decided")
        if request.decision != REVIEW_SUBMITTED or task.package_state != PKG_PENDING_REVIEW:
            return _failure("review_not_pending")
        artifact = session.get(PpapArtifactRow, request.artifact_id)
        if artifact is None or artifact.task_id != task_id or artifact.status != ART_DRAFT:
            return _failure("draft_artifact_required")
        latest = _latest_package_artifact(session, task_id)
        latest_request = session.query(PpapReviewRow).filter_by(task_id=task_id,
            decision=REVIEW_SUBMITTED).order_by(PpapReviewRow.created_at.desc()).first()
        if latest is None or latest.artifact_id != artifact.artifact_id or latest_request is None or latest_request.review_id != review_id:
            return _failure("review_superseded", "该审核请求已被新的编制或审核请求替代")
        if decision == REVIEW_APPROVED:
            valid = _validate_artifact(session, task, artifact, elements)
            if not valid.get("ok"):
                return valid
        review = PpapReviewRow(review_id=_gen_id("PREV"), task_id=task_id, artifact_id=artifact.artifact_id,
            artifact_version=artifact.version, input_snapshot_hash=artifact.input_snapshot_hash,
            decision=decision, reviewer=actor, reviewer_role=reviewer_role, author=artifact.created_by,
            separation_warning="author_reviewer_same_account" if artifact.created_by == actor else "",
            comments=note)
        session.add(review)
        task.package_state = PKG_APPROVED if decision == REVIEW_APPROVED else PKG_RETURNED
        if decision == REVIEW_APPROVED:
            artifact.status, artifact.frozen_at = ART_APPROVED, _now()
        task.coverage_json = _append_audit(task.coverage_json,
            {"action": "review_decided", "by": actor,
             "detail": {"decision": decision, "artifact_id": artifact.artifact_id,
                        "separation_warning": review.separation_warning}})
        task.updated_at = _now()
        session.commit()
        return {"success": True, "review": _review_dict(review), "artifact": _artifact_dict(artifact),
                "written": "local_ppap_store"}


def archive_task(task_id: str, *, actor: str, artifact_id: str = "", note: str = "") -> dict[str, Any]:
    if not _text(actor):
        return _failure("actor_required")
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        artifact = session.get(PpapArtifactRow, artifact_id) if artifact_id else _latest_package_artifact(session, task_id)
        if task is None or artifact is None or artifact.task_id != task_id:
            return _failure("artifact_not_found")
        if artifact.status == ART_ARCHIVED:
            return {"success": True, "artifact": _artifact_dict(artifact), "idempotent_replay": True}
        if artifact.status != ART_APPROVED or not artifact.frozen_at:
            return _failure("approved_artifact_required")
        checked = read_artifact(task_id, artifact.artifact_id)
        if not checked.get("ok"):
            return checked
        artifact.status = ART_ARCHIVED
        latest = _latest_package_artifact(session, task_id)
        if latest and latest.artifact_id == artifact.artifact_id:
            task.package_state = PKG_ARCHIVED
        task.coverage_json = _append_audit(task.coverage_json,
            {"action": "archived", "by": actor, "detail": {"artifact_id": artifact.artifact_id, "note": note}})
        task.updated_at = _now()
        session.commit()
        return {"success": True, "artifact": _artifact_dict(artifact), "written": "local_ppap_store"}


def record_customer_state(task_id: str, *, actor: str, state: str,
                           reference: str = "", note: str = "") -> dict[str, Any]:
    if not _text(actor) or state not in CUSTOMER_STATES or not _text(reference):
        return _failure("invalid_customer_state", "处理状态、操作者及实际客户记录引用必填")
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return _failure("task_not_found")
        mode_problem = ppap_mode.validate_customer_state(ppap_mode.task_mode(task))
        if mode_problem:
            return mode_problem
        mode_problem = _validate_task_provenance(session, task)
        if mode_problem:
            return mode_problem
        artifact = _latest_package_artifact(session, task_id)
        if task.package_state not in (PKG_APPROVED, PKG_ARCHIVED) or artifact is None or artifact.status not in (ART_APPROVED, ART_ARCHIVED):
            return _failure("internal_approval_required")
        task.customer_state = state
        task.coverage_json = _append_audit(task.coverage_json,
            {"action": "customer_state_recorded", "by": actor,
             "detail": {"state": state, "reference": reference, "note": note,
                        "artifact_id": artifact.artifact_id}})
        task.updated_at = _now()
        session.commit()
        return {"success": True, "task": _task_dict(task), "written": "local_ppap_store"}


def list_versions(task_id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        if session.get(PpapTaskRow, task_id) is None:
            return _failure("task_not_found")
        rows = _load_artifact_rows(session, task_id)
        return {"ok": True, "task_id": task_id, "items": [_artifact_dict(row) for row in rows], "total": len(rows)}


def read_artifact(task_id: str, artifact_id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        row = session.get(PpapArtifactRow, artifact_id)
        if row is None or row.task_id != task_id:
            return _failure("artifact_not_found")
        try:
            path = ppap_storage.resolve_within(ppap_storage.storage_root(), row.storage_rel_path)
            data = path.read_bytes()
        except (ValueError, OSError):
            return _failure("artifact_unavailable")
        if ppap_storage.sha256_bytes(data) != row.content_hash:
            return _failure("artifact_hash_mismatch")
        return {"ok": True, "artifact": _artifact_dict(row), "content": data,
                "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def list_runs(task_id: str, *, limit: int = 50) -> dict[str, Any]:
    with SessionLocal() as session:
        if session.get(PpapTaskRow, task_id) is None:
            return _failure("task_not_found")
        rows = session.query(PpapCompileRunRow).filter_by(task_id=task_id).order_by(
            PpapCompileRunRow.started_at.desc()).limit(_clamp_limit(limit)).all()
        return {"ok": True, "task_id": task_id, "items": [_run_dict(row, include_steps=True) for row in rows]}


def get_input_assistance(task_id: str, *, dataset: str, part_side: str = "",
                         characteristic_id: str = "", record_key: str = "") -> dict[str, Any]:
    """Read local confirmed inputs only; never trigger collection or persistence."""
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return _failure("task_not_found")
        problem = _validate_task_provenance(session, task)
        if problem:
            return {**problem, "read_only": True}
        return ppap_assistance.build_input_assistance(_task_dict(task),
            [_input_dict(row) for row in _load_input_rows(session, task_id)],
            dataset=dataset, part_side=part_side, characteristic_id=characteristic_id, record_key=record_key)


def assess_source_change_impact(task_id: str, *, compare_artifact_id: str = "") -> dict[str, Any]:
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        if task is None:
            return _failure("task_not_found")
        inputs, evidence = _load_input_rows(session, task_id), _load_evidence_rows(session, task_id)
        artifact = session.get(PpapArtifactRow, compare_artifact_id) if compare_artifact_id else _latest_package_artifact(session, task_id)
        if compare_artifact_id and (artifact is None or artifact.task_id != task_id or artifact.scope != SCOPE_PACKAGE):
            return _failure("artifact_not_found")
        current = _snapshot_document(task, elements, inputs, evidence)
        current_hash = ppap_storage.sha256_json(current)
        run = session.get(PpapCompileRunRow, artifact.run_id) if artifact else None
        previous = _as_dict(_as_dict(run.summary_json).get("snapshot")) if run else {}
        changed = [key for key in ("task", "business_object", "template", "elements", "inputs", "evidence", "provenance")
                   if current.get(key) != previous.get(key)]
        changes = ppap_assistance.source_changes(previous, current,
            [_input_dict(row) for row in inputs], [_evidence_dict(row, include_payload=True) for row in evidence])
        for key in ("compilation_engine_version", "export_version"):
            if current.get(key) != previous.get(key):
                changed.append(key)
                changes.append({"section": key, "field_path": key, "old_present": key in previous,
                    "new_present": key in current, "old_value": previous.get(key), "new_value": current.get(key),
                    "old_available": True, "new_available": True, "old_source": {}, "new_source": {},
                    "change_type": "modified" if key in previous else "added"})
        rules_changed = bool(artifact) and artifact.issue_count_json.get("rule_hash") != ppap_storage.sha256_json(
            ppap_calc.load_acceptance_rules())
        if rules_changed:
            changed.append("acceptance_rules")
            changes.append({"section": "acceptance_rules", "field_path": "rule_hash",
                "old_present": "rule_hash" in artifact.issue_count_json, "new_present": True,
                "old_value": artifact.issue_count_json.get("rule_hash"),
                "new_value": ppap_storage.sha256_json(ppap_calc.load_acceptance_rules()),
                "old_available": True, "new_available": True, "old_source": {}, "new_source": {},
                "change_type": "modified", "notice": "仅比较已登记的规则指纹，不恢复历史阈值。"})
        affected, impact_reasons = ppap_assistance.affected_elements(changes,
            [_element_dict(row, include_content=False) for row in elements],
            [_evidence_dict(row) for row in evidence])
        if changed and not changes:
            # Hash-only historical differences cannot be mapped safely. Keep
            # the old section warning and state the conservative scope.
            affected = [row.element_id for row in elements if row.requirement != ppap_template.REQ_NOT_APPLICABLE]
            impact_reasons = [{"element_id": element, "reason": "历史仅有指纹，变化无法精确映射；保守核对全部适用条目。"}
                             for element in affected]
        return {"ok": True, "task_id": task_id, "has_artifact": artifact is not None,
                "changed": bool(changed or changes), "changed_sections": changed,
                "comparison_artifact_id": artifact.artifact_id if artifact else "",
                "comparison_version": artifact.version if artifact else None,
                "changes": changes, "impact_reasons": impact_reasons,
                "current_snapshot_hash": current_hash,
                "artifact_snapshot_hash": artifact.input_snapshot_hash if artifact else "",
                "affected_elements": affected,
                "notice": "比较已登记的来源快照，未自动刷新远端或写入任务。", "read_only": True}


def query_applicability(task_id: str) -> dict[str, Any]:
    result = get_task(task_id, include_content=False)
    if not result.get("ok"):
        return result
    return {"ok": True, "task_id": task_id, "applicability": result["applicability"],
            "items": [{key: row.get(key) for key in ("element_id", "title", "requirement", "applicability_basis",
                                                     "suggested_requirement", "applicability_condition")}
                      for row in result["elements"]], "read_only": True}


def query_status(task_id: str) -> dict[str, Any]:
    result = get_task(task_id, include_content=False)
    if not result.get("ok"):
        return result
    return {"ok": True, "task": result["task"], "elements": result["elements"],
            "runs": result["runs"], "read_only": True}


# ----------------------------------------------------------------------
# 接入层安全收口：保留阶段 A 实现，补足作用域、来源指纹与失败传播。
# 这些函数只写 ppap_*；不会向 ERP/MES 写入。
# ----------------------------------------------------------------------
_stage_a_readiness = _element_readiness
_stage_a_snapshot = _snapshot_document
_stage_a_register_evidence = register_human_evidence
_stage_a_upsert_input = upsert_input
_stage_a_classify_read = _classify_read


def _element_readiness(task_row, spec, input_rows, evidence_rows):
    scoped = [row for row in evidence_rows if
        spec.element_id in (row.element_ids_json or []) or
        (not row.element_ids_json and row.source_type not in HUMAN_EVIDENCE_TYPES)]
    result = _stage_a_readiness(task_row, spec, input_rows, [row for row in scoped if not row.truncated])
    result["truncated_evidence"] = sorted({row.source_type for row in scoped if row.truncated})
    return result


def _snapshot_document(task_row, element_rows, input_rows, evidence_rows):
    snapshot = _stage_a_snapshot(task_row, element_rows, input_rows, evidence_rows)
    snapshot["task"].update({key: getattr(task_row, key) for key in ("owner", "reviewer", "due_date")})
    snapshot["task"]["data_mode"] = ppap_mode.task_mode(task_row)
    snapshot["compilation_engine_version"] = COMPILATION_ENGINE_VERSION
    snapshot["export_version"] = ppap_export.EXPORT_VERSION
    snapshot["provenance"] = {
        "inputs": [{"input_id": row.input_id, "source_type": row.source_type, "source_ref": row.source_ref,
                    "dataset": row.dataset, "record_key": row.record_key, "part_side": row.part_side,
                    "element_id": row.element_id or "", "confirmed_by": row.confirmed_by or "",
                    "confirmed_at": _iso(row.confirmed_at), "payload": dict(row.payload_json or {})}
                   for row in sorted(input_rows, key=lambda item: item.input_id)],
        "evidence": [{"evidence_id": row.evidence_id, "locator": row.locator, "source_type": row.source_type,
                      "source_id": row.source_id, "payload": dict(row.payload_json or {}),
                      "source_version": row.source_version or "", "read_status": row.read_status,
                      "truncated": bool(row.truncated), "content_hash": row.content_hash or "",
                      "element_ids": sorted(row.element_ids_json or [])}
                     for row in sorted(evidence_rows, key=lambda item: item.evidence_id)]}
    return snapshot


def upsert_input(task_id, *, dataset, payload, actor, part_side="", record_key="",
                 source_type="user_confirmation", source_ref="", element_id=""):
    if not _text(actor) or not _text(source_ref) or not _text(source_type):
        return _failure("source_confirmation_required", "输入必须有来源类型、来源引用和会话确认人")
    return _stage_a_upsert_input(task_id, dataset=dataset, payload=payload, actor=actor,
        part_side=part_side, record_key=record_key, source_type=source_type,
        source_ref=source_ref, element_id=element_id)


def register_human_evidence(task_id, *, source_type, source_id, summary, locator="", actor,
                            note="", element_ids=(), document_version=""):
    if not _text(actor):
        return _failure("actor_required")
    if not isinstance(element_ids, (tuple, list)) or any(
            not isinstance(item, str) or item not in ppap_template.ELEMENT_IDS for item in element_ids):
        return _failure("invalid_evidence_binding", "原件绑定必须是明确的有效条目编号列表")
    bindings = sorted(set(element_ids))
    if any(source_type not in ppap_template.element_spec(item).evidence_types for item in bindings):
        return _failure("invalid_evidence_binding", "该来源类型不符合所选条目的证据契约")
    original_task = get_task(task_id, include_content=False)
    if not original_task.get("ok"):
        return _failure("task_not_found")
    mode = ppap_mode.task_mode(original_task["task"])
    mode_problem = ppap_mode.validate_evidence_source(mode, source_type, source_id, locator)
    if mode_problem:
        return mode_problem
    actual_hash, actual_locator = "", locator
    if source_type == ppap_template.EV_INTERNAL_PACKAGE:
        with SessionLocal() as session:
            artifact = session.get(PpapArtifactRow, locator)
            if artifact is None:
                return _failure("artifact_not_found")
            source_task_id = artifact.task_id
            mode_problem = _validate_package_reference(session, mode, artifact.artifact_id)
            if mode_problem:
                return mode_problem
        verified = read_artifact(source_task_id, locator)
        if not verified.get("ok"):
            return verified
        actual_hash = verified["artifact"]["content_hash"]
    elif source_type == ppap_template.EV_USER_UPLOAD:
        try:
            actual_locator = locator.replace("\\", "/")
            actual_hash = ppap_storage.sha256_bytes(ppap_storage.read_bytes(actual_locator))
        except (ValueError, OSError):
            actual_hash = ""
    result = _stage_a_register_evidence(task_id, source_type=source_type, source_id=source_id,
        summary=summary, locator=locator, actor=actor, note=note)
    if not result.get("success"):
        return result
    with SessionLocal() as session:
        row = session.query(PpapEvidenceRow).filter_by(task_id=task_id,
            dedup_key=f"human.{source_type}:{_text(source_id) or _text(locator)}").one()
        row.element_ids_json = bindings
        row.locator = actual_locator
        if actual_hash:
            row.content_hash = actual_hash
            # 文件指纹不是图纸版本；文档版次只能取人工明确提供的值。
            row.source_version = _text(document_version)
        else:
            row.read_status, row.content_hash = READ_EMPTY, ""
            row.error_json = {"code": "content_not_verified"}
        task = session.get(PpapTaskRow, task_id)
        task.coverage_json = _append_audit(task.coverage_json, {
            "action": "evidence_scope_confirmed", "by": actor,
            "detail": {"evidence_id": row.evidence_id, "element_ids": bindings,
                       "content_hash": row.content_hash, "content_verified": bool(actual_hash)}})
        session.commit()
        result.update(_evidence_dict(row))
    result["content_verified"] = bool(actual_hash)
    result["scope_confirmed"] = bool(bindings)
    return result


def _classify_read(collector_id, payload):
    if collector_id == "openmes.inspections" and isinstance(payload, Mapping) and (
            _as_dict(payload.get("gate_details")).get("inspections_scope") == "unavailable"):
        return READ_ERROR, "工单批次关联读取失败，不能把未取得的检验记录当成0条", {"code": "inspection_scope_unavailable"}
    return _stage_a_classify_read(collector_id, payload)


async def collect_evidence(task_id, *, source_types=None, actor="", note=""):
    """受控取证：先核对父来源状态，再派生子来源，并保留本次真实调用链。"""
    if not _text(actor):
        return _failure("actor_required")
    with SessionLocal() as session:
        task = session.get(PpapTaskRow, task_id)
        if task is None:
            return _failure("task_not_found")
        mode_problem = ppap_mode.validate_collect(ppap_mode.task_mode(task))
        if mode_problem:
            return mode_problem
        mode = ppap_mode.task_mode(task)
        mode_problem = ppap_mode.validate_task_data(mode, _task_mode_values(task))
        if mode_problem:
            return mode_problem
        context = _context_from_business(task.business_object_json or {})
        template_id, template_version = task.template_id, task.template_version
    usable, rejected = _validate_source_types(source_types)
    if not usable:
        return {**_failure("no_readable_source"), "rejected": rejected}
    planned_ids = {c.collector_id for c in COLLECTORS if c.source_type in usable}
    planned_ids.update(c.parent for c in COLLECTORS if c.collector_id in planned_ids and c.parent)
    if context.get("work_order_no") and not context.get("work_order_id"):
        planned_ids.add("openmes.work_order_lookup")
    collected, skipped, call_chain, results = [], [], [], {}
    for collector in COLLECTORS:
        if collector.collector_id not in planned_ids:
            continue
        missing = [key for key in collector.requires if not context.get(key)]
        if missing:
            skipped.append({"collector_id": collector.collector_id, "source_type": collector.source_type,
                            "reason": "missing_business_keys", "missing_business_keys": missing,
                            "message": "业务对象缺少明确编号，未调用来源"})
            continue
        if collector.parent:
            parent = results.get(collector.parent)
            if parent is None:
                skipped.append({"collector_id": collector.collector_id, "source_type": collector.source_type,
                                "reason": "parent_unavailable"})
                continue
            if parent[0] == READ_ERROR:
                payload = {"error": "父来源读取失败，子来源无法取得", "parent": collector.parent}
            else:
                payload = {"__items": _as_list(parent[1].get(collector.slice_key)),
                           "__slice_of": collector.parent, "__slice_key": collector.slice_key,
                           "gate_details": parent[1].get("gate_details", {})}
        else:
            try:
                payload = _jsonable(await READERS[collector.collector_id](context))
                if isinstance(payload, Mapping) and payload.get("error"):
                    # 失败对象只保存安全的来源事实，不保留可能包含凭据的远端异常文本。
                    payload = {"error": "来源接口报告读取失败", "collector_id": collector.collector_id}
            except Exception as exc:
                payload = {"error": "来源请求未完成", "exception_type": type(exc).__name__}
        mode_problem = ppap_mode.validate_evidence_source(mode, collector.source_type,
            _collector_source_id(collector, context), collector.collector_id, payload)
        if mode_problem:
            status, summary, error = READ_ERROR, mode_problem["error"], {"code": mode_problem["code"]}
        else:
            status, summary, error = _classify_read(collector.collector_id, payload)
        if not isinstance(payload, Mapping):
            payload = {}
        truncated = _truncated_for(collector.collector_id, payload)
        results[collector.collector_id] = (status, payload)
        if collector.collector_id == "openmes.work_order_lookup" and status == READ_OK:
            context["work_order_id"] = _text(payload.get("work_order_id"))
        call_chain.append({"step": "derive" if collector.parent else "read",
                           "target": collector.collector_id, "result": status,
                           "truncated": truncated, "detail": summary})
        collected.append(_store_evidence(task_id, collector=collector,
            source_id=_collector_source_id(collector, context), payload=payload, read_status=status,
            summary=summary, error_json=error, truncated=truncated, actor=actor, note=note))
    with SessionLocal() as session:
        task, elements = _load_task_rows(session, task_id)
        inputs, evidence = _load_input_rows(session, task_id), _load_evidence_rows(session, task_id)
        coverage = _compute_coverage(task, elements, inputs, evidence)
        snapshot_hash = _current_snapshot_hash(task, elements, inputs, evidence)
        run = PpapCompileRunRow(run_id=_gen_id("PCR"), task_id=task_id,
            trigger="evidence_collect", request_key=_gen_id("evidence"), input_snapshot_hash=snapshot_hash,
            template_id=template_id, template_version=template_version, rule_version=ppap_template.RULE_VERSION,
            status=RUN_FAILED if any(row["read_status"] == READ_ERROR for row in collected) else RUN_COMPILED,
            steps_json=[{"step": "collect_evidence", "status": "COMPLETED", "count": len(collected)}],
            call_chain_json=call_chain, summary_json={"collected": len(collected), "skipped": len(skipped),
                                                     "evidence": coverage["evidence"]},
            requested_by=actor, finished_at=_now())
        session.add(run)
        task.coverage_json = _append_audit(coverage, {"action": "evidence_collected", "by": actor,
            "detail": {"run_id": run.run_id, "collected": [row["collector_id"] for row in collected]}})
        task.run_state, task.updated_at = _compute_run_state(task, elements, inputs, evidence), _now()
        session.commit()
        return {"success": True, "task_id": task_id, "collected": collected, "skipped": skipped,
            "rejected": rejected, "call_chain": call_chain, "coverage": coverage, "task": _task_dict(task),
            "run": _run_dict(run, include_steps=True), "authority": "openmes/erp/local(只读)",
            "written": "local_ppap_store"}


def _verify_persisted_result(result):
    """用独立数据库会话回读成功写入，不能用未过期的内存对象冒充验证。"""
    if not result.get("success"):
        return result
    mappings = (
        ("task", "task_id", PpapTaskRow, _task_dict),
        ("artifact", "artifact_id", PpapArtifactRow, _artifact_dict),
        ("run", "run_id", PpapCompileRunRow, lambda row: _run_dict(row, include_steps=True)),
        ("review", "review_id", PpapReviewRow, _review_dict),
        ("issue", "issue_id", PpapIssueRow, _issue_dict),
    )
    try:
        with SessionLocal() as read_session:
            verified = False
            for key, id_key, model, serialize in mappings:
                expected = result.get(key)
                if not isinstance(expected, Mapping) or not expected.get(id_key):
                    continue
                row = read_session.get(model, expected[id_key])
                actual = serialize(row) if row else {}
                if row is None or any(actual.get(field) != value for field, value in expected.items()
                                      if field in actual):
                    return _failure("read_back_failed", "本次写入已提交，但独立回读不一致，请按记录编号核对")
                verified = True
            for id_key, model in (("task_id", PpapTaskRow), ("input_id", PpapInputRow),
                                  ("evidence_id", PpapEvidenceRow)):
                if result.get(id_key):
                    if read_session.get(model, result[id_key]) is None:
                        return _failure("read_back_failed", "本次写入未能独立回读对应记录")
                    verified = True
            if not verified:
                return _failure("read_back_failed", "成功结果缺少可独立回读的记录编号")
    except Exception:
        return _failure("read_back_failed", "本次写入已提交，但数据库回读不可用，请核对运行及任务记录")
    return {**result, "read_back_verified": True}


def _with_local_readback(function):
    from functools import wraps
    from inspect import iscoroutinefunction
    if iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(*args, **kwargs):
            return _verify_persisted_result(await function(*args, **kwargs))
        return asynchronous
    @wraps(function)
    def synchronous(*args, **kwargs):
        return _verify_persisted_result(function(*args, **kwargs))
    return synchronous


for _mutation in ("create_task", "update_task", "confirm_applicability", "collect_evidence",
                  "register_human_evidence", "upsert_input", "compile_task", "run_checks",
                  "resolve_issue", "submit_review", "decide_review", "archive_task", "record_customer_state"):
    globals()[_mutation] = _with_local_readback(globals()[_mutation])
