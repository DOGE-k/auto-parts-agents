"""跨智能体协同事件服务（P1 第一片：质量异常事件）。

按 AI_HANDOFF_PLAN 第 6 节要求实现事件协作的最小闭环：

- 触发：质量问题经人工审批真实登记到 OpenMES 后，自动产生一条
  "quality_issue_raised" 协同事件（事件编号 EVT-…）；
- 去重：dedup_key 唯一约束 + 触发时查重，同一工单同一问题不重复协同；
- 协同（只读）：质量文档智能体影响分析 → 跟单智能体生产/交期 →
  采购智能体供应商风险（经由已保存报价反查；任何一环缺数据如实记录
  数据缺口，不猜测、不补数）；
- 失败记录与重试上限：核心协同失败置 FAILED 并记 error_json，
  failure_count 达 max_retries 后拒绝再重试；
- 人工接管：任何状态下可标记 MANUAL_HANDLED（记录操作者与备注），
  前端联动跳转既有 NCR 处置面板；
- 边界：本服务不写 ERPNext/OpenMES，不调用任何审批/写入函数；
  事件结论只是"智能体只读协同的建议"，处置仍走既有审批门禁。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.persistence.database import SessionLocal
from app.persistence.models import CollaborationEventRow
from app.services import real_order

logger = logging.getLogger(__name__)

EVENT_TYPE_QUALITY_ISSUE = "quality_issue_raised"

EVENT_STATUS_PENDING = "PENDING"
EVENT_STATUS_PROCESSING = "PROCESSING"
EVENT_STATUS_COMPLETED = "COMPLETED"
EVENT_STATUS_FAILED = "FAILED"
EVENT_STATUS_MANUAL_HANDLED = "MANUAL_HANDLED"

DEFAULT_MAX_RETRIES = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_event_id() -> str:
    return f"EVT-{uuid4().hex[:12].upper()}"


def _event_summary(row: CollaborationEventRow) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "event_type": row.event_type,
        "dedup_key": row.dedup_key,
        "status": row.status,
        "payload": row.payload_json or {},
        "failure_count": row.failure_count,
        "max_retries": row.max_retries,
        "taken_over_by": row.taken_over_by,
        "taken_over_at": row.taken_over_at.isoformat() if row.taken_over_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
        "error": row.error_json,
        "has_result": row.result_json is not None,
        # 列表摘要直接携带完整结果（单条结果体量小，避免前端展开二次请求；
        # 2026-10-01 页面走查实测：列表缺 result 会让"查看协同结论"展开为空）
        "result": row.result_json,
    }


def list_events(limit: int = 20, status: str | None = None) -> dict[str, Any]:
    """事件列表（新→旧）。"""
    with SessionLocal() as session:
        query = select(CollaborationEventRow).order_by(
            CollaborationEventRow.created_at.desc()
        )
        if status:
            query = query.where(CollaborationEventRow.status == status)
        rows = session.scalars(query.limit(min(limit, 100))).all()
        return {
            "items": [_event_summary(r) for r in rows],
            "count": len(rows),
            "data_source": "local_collaboration_events",
        }


def get_event(event_id: str) -> dict[str, Any] | None:
    """事件详情（含完整协同结果）。"""
    with SessionLocal() as session:
        row = session.get(CollaborationEventRow, event_id)
        if row is None:
            return None
        return _event_summary(row)


async def trigger_quality_issue_event(
    work_order_id: str,
    issue_id: str,
    *,
    title: str = "",
    severity: str = "",
    source: str = "issue_registration",
) -> dict[str, Any]:
    """质量异常事件触发入口（登记成功后调用；幂等）。

    同一 (work_order_id, issue_id) 已有未接管事件时直接返回既有事件，
    不重复协同。
    """
    wo_id = str(work_order_id).strip()
    iss_id = str(issue_id).strip()
    dedup_key = f"{EVENT_TYPE_QUALITY_ISSUE}:{wo_id}:{iss_id}"
    with SessionLocal() as session:
        existing = session.scalars(
            select(CollaborationEventRow).where(
                CollaborationEventRow.dedup_key == dedup_key,
                CollaborationEventRow.status != EVENT_STATUS_MANUAL_HANDLED,
            )
        ).first()
        if existing is not None:
            summary = _event_summary(existing)
            summary["deduplicated"] = True
            return summary
        row = CollaborationEventRow(
            event_id=_new_event_id(),
            event_type=EVENT_TYPE_QUALITY_ISSUE,
            dedup_key=dedup_key,
            status=EVENT_STATUS_PENDING,
            payload_json={
                "work_order_id": wo_id,
                "issue_id": iss_id,
                "title": title,
                "severity": severity,
                "source": source,
            },
            max_retries=DEFAULT_MAX_RETRIES,
        )
        session.add(row)
        session.commit()
        event_id = row.event_id
    return await process_event(event_id)


async def process_event(event_id: str) -> dict[str, Any]:
    """执行事件协同（只读三智能体），写回结果或失败记录。"""
    with SessionLocal() as session:
        row = session.get(CollaborationEventRow, event_id)
        if row is None:
            raise ValueError(f"协同事件 {event_id} 不存在")
        if row.status == EVENT_STATUS_MANUAL_HANDLED:
            summary = _event_summary(row)
            summary["error"] = {"message": "事件已人工接管，不再自动协同"}
            return summary
        payload = dict(row.payload_json or {})
        row.status = EVENT_STATUS_PROCESSING
        session.commit()

    work_order_id = str(payload.get("work_order_id", ""))
    data_gaps: list[dict[str, str]] = []
    try:
        # ① 质量文档智能体：影响分析（质量问题清单/门禁/生产交叉/结论）
        quality_impact = await real_order.assess_quality_impact(work_order_id)
        # ② 跟单智能体：真实生产进度与交期
        tracking = await real_order.track_order(work_order_id)
        # ③ 采购智能体：供应商风险（经由 ERP 关联与已保存报价反查）
        procurement_risk, procurement_gaps = await _procurement_risk(work_order_id)
        data_gaps.extend(procurement_gaps)
        if isinstance(quality_impact, dict):
            for gap in quality_impact.get("data_gaps") or []:
                detail = gap.get("detail") if isinstance(gap, dict) else str(gap)
                data_gaps.append({"dimension": "quality", "detail": detail})
        if isinstance(tracking, dict) and tracking.get("eta_status") == "DATA_MISSING":
            for gap in tracking.get("eta_data_gaps") or []:
                detail = gap.get("detail") if isinstance(gap, dict) else str(gap)
                data_gaps.append({"dimension": "tracking", "detail": detail})

        result = {
            "quality_impact": _subset(quality_impact, (
                "work_order_id", "work_order_no", "open_issues_count", "open_records",
                "quality_gate_passed", "missing_documents", "impact_conclusions",
                "handling_options",
            )),
            "tracking": _subset(tracking, (
                "work_order_id", "work_order_no", "status", "completion_rate",
                "completed_qty", "quantity", "due_date", "eta", "eta_status",
                "eta_basis", "risks",
            )),
            "procurement_risk": procurement_risk,
            "conclusions": _build_conclusions(quality_impact, tracking, procurement_risk),
            "data_gaps": data_gaps,
            "generated_at": _now().isoformat(),
            "authority": "ERPNext + OpenMES（只读协同，未写入）",
        }
        with SessionLocal() as session:
            row = session.get(CollaborationEventRow, event_id)
            row.status = EVENT_STATUS_COMPLETED
            row.result_json = result
            row.error_json = None
            row.processed_at = _now()
            session.commit()
            return _event_summary(row)
    except Exception as exc:
        with SessionLocal() as session:
            row = session.get(CollaborationEventRow, event_id)
            row.failure_count = (row.failure_count or 0) + 1
            row.status = EVENT_STATUS_FAILED
            row.error_json = {"message": f"{type(exc).__name__}: {str(exc)[:400]}", "at": _now().isoformat()}
            row.processed_at = _now()
            session.commit()
            return _event_summary(row)


async def retry_event(event_id: str, operator: str) -> dict[str, Any]:
    """人工重试（FAILED 且未达重试上限）。"""
    with SessionLocal() as session:
        row = session.get(CollaborationEventRow, event_id)
        if row is None:
            raise ValueError(f"协同事件 {event_id} 不存在")
        if row.status != EVENT_STATUS_FAILED:
            raise ValueError(f"事件当前状态 {row.status} 不是 FAILED，无需重试")
        if (row.failure_count or 0) >= (row.max_retries or DEFAULT_MAX_RETRIES):
            raise ValueError(
                f"已达到重试上限（{row.max_retries} 次）；请人工接管处理"
            )
    return await process_event(event_id)


def takeover_event(event_id: str, operator: str, note: str = "") -> dict[str, Any]:
    """人工接管：停止自动协同，记录操作者与备注（处置走既有审批门禁）。"""
    with SessionLocal() as session:
        row = session.get(CollaborationEventRow, event_id)
        if row is None:
            raise ValueError(f"协同事件 {event_id} 不存在")
        if row.status == EVENT_STATUS_MANUAL_HANDLED:
            summary = _event_summary(row)
            summary["deduplicated"] = True
            return summary
        row.status = EVENT_STATUS_MANUAL_HANDLED
        row.taken_over_by = (operator or "").strip()[:120]
        row.taken_over_at = _now()
        result = dict(row.result_json or {})
        result["manual_takeover"] = {
            "operator": row.taken_over_by,
            "at": row.taken_over_at.isoformat(),
            "note": (note or "").strip()[:500],
            "hint": "处置请在 NCR 面板走审批门禁（登记处置→批准→写回）",
        }
        row.result_json = result
        session.commit()
        return _event_summary(row)


async def _procurement_risk(work_order_id: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """采购维度只读协同：工单 → ERP 关联 → 已保存报价 → 采购分析。

    任何一环缺失/失败都如实返回数据缺口，不猜测、不编造。
    """
    gaps: list[dict[str, str]] = []
    try:
        mes = real_order.get_mes_adapter()
        raw = await mes.get_work_order_raw(work_order_id)
        erp_order_id = str(raw.get("customer_order_no") or "").strip()
    except Exception as exc:
        return None, [{"dimension": "procurement", "detail": f"读取 MES 工单关联失败：{exc}"}]
    if not erp_order_id:
        return None, [{
            "dimension": "procurement",
            "detail": "MES 工单未建立 customer_order_no 关联，无法反查报价做供应商风险分析",
        }]
    try:
        found = await real_order.find_quotation_by_erp_order(erp_order_id)
    except Exception as exc:
        return None, [{"dimension": "procurement", "detail": f"按订单 {erp_order_id} 反查报价失败：{exc}"}]
    quotation_id = (found or {}).get("quotation_id", "")
    if not quotation_id:
        return None, [{
            "dimension": "procurement",
            "detail": f"ERP 订单 {erp_order_id} 没有已保存报价，跳过供应商风险分析（需先走报价流程）",
        }]
    try:
        plan = await real_order.analyze_procurement(quotation_id)
    except Exception as exc:
        return None, [{
            "dimension": "procurement",
            "detail": f"报价 {quotation_id} 采购分析不可用：{exc}",
        }]
    net_req = plan.get("net_requirement") or {}
    options = []
    for opt in (plan.get("supplier_options") or [])[:5]:
        options.append({
            "option_id": opt.get("option_id", ""),
            "supplier_name": opt.get("supplier_name", ""),
            "total_cost": opt.get("total_cost"),
            "currency": opt.get("currency", ""),
            "lead_time_days": opt.get("lead_time_days"),
            "coverage": opt.get("coverage"),
            "is_recommended": opt.get("is_recommended", False),
        })
    return {
        "quotation_id": quotation_id,
        "plan_id": plan.get("plan_id", ""),
        "status": plan.get("status", ""),
        "shortage_evaluable": net_req.get("shortage_evaluable"),
        "has_shortage": net_req.get("has_shortage"),
        "shortage_count": net_req.get("shortage_count", 0),
        "shortage_items": net_req.get("shortage_items", []),
        "supplier_options": options,
        "note": "只读协同的供应商视角（非执行方案）；如需采购仍走方案审批门禁",
    }, gaps


def _subset(data: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {k: data.get(k) for k in keys}


def _build_conclusions(
    quality_impact: Any, tracking: Any, procurement_risk: dict[str, Any] | None
) -> list[str]:
    """确定性汇总三维度结论（只复述真实数据，不加预测）。"""
    conclusions: list[str] = []
    qi = quality_impact if isinstance(quality_impact, dict) else {}
    open_count = qi.get("open_issues_count")
    if open_count is not None:
        conclusions.append(
            f"质量维度：{open_count} 项未关闭质量问题，质量门禁{'通过' if qi.get('quality_gate_passed') else '未通过'}"
            + (f"（缺文档：{'、'.join(qi.get('missing_documents') or [])}）" if qi.get("missing_documents") else "")
        )
    tk = tracking if isinstance(tracking, dict) else {}
    if tk.get("completion_rate") is not None:
        conclusions.append(
            f"生产维度：完成率 {tk.get('completion_rate')}%（{tk.get('completed_qty')}/{tk.get('quantity')}），"
            f"ETA 口径 {tk.get('eta_status') or '未知'}"
        )
    if procurement_risk is None:
        conclusions.append("采购维度：数据不足，未做供应商风险分析（见数据缺口）")
    else:
        if procurement_risk.get("has_shortage"):
            conclusions.append(
                f"采购维度：存在 {procurement_risk.get('shortage_count')} 项缺料，"
                f"共 {len(procurement_risk.get('supplier_options') or [])} 个供应商选项可评估（详见供应商风险）"
            )
        elif procurement_risk.get("shortage_evaluable") is False:
            conclusions.append("采购维度：该物料无 BOM，缺料评估不可用（需先补录 ERP 主数据）")
        else:
            conclusions.append("采购维度：当前库存充足，无缺料")
    conclusions.append("本事件为只读协同结论；处置与写入仍需人工走审批门禁")
    return conclusions
