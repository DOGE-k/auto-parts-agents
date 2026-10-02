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

import hashlib
import json as _json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.persistence.database import SessionLocal
from app.persistence.models import CollaborationEventRow
from app.services import real_order

logger = logging.getLogger(__name__)

EVENT_TYPE_QUALITY_ISSUE = "quality_issue_raised"
EVENT_TYPE_SHORTAGE = "material_shortage"
EVENT_TYPE_OVERDUE = "production_overdue"
EVENT_TYPE_AT_RISK = "production_at_risk"

_EVENT_TYPE_LABELS = {
    EVENT_TYPE_QUALITY_ISSUE: "质量异常协同",
    EVENT_TYPE_SHORTAGE: "关键物料短缺协同",
    EVENT_TYPE_OVERDUE: "生产延期预警协同",
    EVENT_TYPE_AT_RISK: "生产临期风险协同",
}

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
        "event_type_label": _EVENT_TYPE_LABELS.get(row.event_type, row.event_type),
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


async def _trigger_with_dedup(
    base_key: str,
    make_row,
) -> dict[str, Any]:
    """事件触发的公共幂等入口（2026-10-01 接手缺口③修复）。

    - 同 base_key 下存在未接管事件 → 直接返回既有（deduplicated）；
    - 已有事件全部人工接管后同一业务事实再次发生 → 产生**新事件**，
      dedup_key 加 "#N" 后缀保持唯一约束不被破坏（不改约束，避免
      SQLite/PG 跨库重建表迁移）；
    - 并发竞态撞唯一约束时回读未接管事件返回，不把 IntegrityError
      泄漏给登记/采购分析/跟单等原始业务操作。
    """
    import sqlalchemy.exc

    with SessionLocal() as session:
        existing = session.scalars(
            select(CollaborationEventRow).where(
                CollaborationEventRow.dedup_key.like(f"{base_key}%"),
                CollaborationEventRow.status != EVENT_STATUS_MANUAL_HANDLED,
            ).order_by(CollaborationEventRow.created_at.desc())
        ).first()
        if existing is not None:
            summary = _event_summary(existing)
            summary["deduplicated"] = True
            return summary
        prior_count = len(
            session.scalars(
                select(CollaborationEventRow.dedup_key).where(
                    CollaborationEventRow.dedup_key.like(f"{base_key}%")
                )
            ).all()
        )
        dedup_key = base_key if prior_count == 0 else f"{base_key}#{prior_count + 1}"
        row = make_row(dedup_key)
        session.add(row)
        try:
            session.commit()
        except sqlalchemy.exc.IntegrityError:
            session.rollback()
            raced = session.scalars(
                select(CollaborationEventRow).where(
                    CollaborationEventRow.dedup_key.like(f"{base_key}%"),
                    CollaborationEventRow.status != EVENT_STATUS_MANUAL_HANDLED,
                )
            ).first()
            if raced is not None:
                summary = _event_summary(raced)
                summary["deduplicated"] = True
                return summary
            raise
        event_id = row.event_id
    return await process_event(event_id)


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
    base_key = f"{EVENT_TYPE_QUALITY_ISSUE}:{wo_id}:{iss_id}"

    def make_row(dedup_key: str) -> CollaborationEventRow:
        return CollaborationEventRow(
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

    return await _trigger_with_dedup(base_key, make_row)


async def process_event(event_id: str) -> dict[str, Any]:
    """执行事件协同（按事件类型分发；全程只读），写回结果或失败记录。"""
    with SessionLocal() as session:
        row = session.get(CollaborationEventRow, event_id)
        if row is None:
            raise ValueError(f"协同事件 {event_id} 不存在")
        if row.status == EVENT_STATUS_MANUAL_HANDLED:
            summary = _event_summary(row)
            summary["error"] = {"message": "事件已人工接管，不再自动协同"}
            return summary
        payload = dict(row.payload_json or {})
        event_type = row.event_type
        row.status = EVENT_STATUS_PROCESSING
        session.commit()

    if event_type == EVENT_TYPE_SHORTAGE:
        return await _finish_event(event_id, await _process_shortage(payload))
    if event_type == EVENT_TYPE_OVERDUE:
        return await _finish_event(event_id, await _process_overdue(payload))
    if event_type == EVENT_TYPE_AT_RISK:
        return await _finish_event(event_id, await _process_at_risk(payload))
    return await _finish_event(event_id, await _process_quality_issue(payload))


async def _finish_event(event_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
    """把处理器结果（或异常）写回事件行。outcome 含 result/error 二选一。"""
    error = outcome.get("error")
    with SessionLocal() as session:
        row = session.get(CollaborationEventRow, event_id)
        if error is not None:
            row.failure_count = (row.failure_count or 0) + 1
            row.status = EVENT_STATUS_FAILED
            row.error_json = error
        else:
            row.status = EVENT_STATUS_COMPLETED
            row.result_json = outcome.get("result") or {}
            row.error_json = None
        row.processed_at = _now()
        session.commit()
        return _event_summary(row)


async def _process_quality_issue(payload: dict[str, Any]) -> dict[str, Any]:
    """质量异常事件：质量影响 → 生产交期 → 供应商风险（三维度只读协同）。"""
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
        return {"result": result}
    except Exception as exc:
        return {"error": {"message": f"{type(exc).__name__}: {str(exc)[:400]}", "at": _now().isoformat()}}


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




# ========== P1 扩展：关键物料短缺事件（事实触发：采购分析发现真实缺料） ==========

def _shortage_signature(net_requirement: dict[str, Any]) -> str:
    """缺料内容签名：同报价同缺料画面去重；数量变化产生新签名。"""
    items = sorted(
        (str(i.get("item_id", "")), str(i.get("net_requirement", "")))
        for i in (net_requirement.get("shortage_items") or [])
        if isinstance(i, dict)
    )
    raw = _json.dumps(items, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


async def trigger_shortage_event(plan: dict[str, Any]) -> dict[str, Any] | None:
    """采购分析发现真实缺料时触发（analyze_procurement 钩子调用）。

    去重：报价 + 缺料内容签名；同画面不重复协同，数量/物料变化才产生新事件。
    无缺料时跳过（返回 None）。
    """
    net_req = plan.get("net_requirement") or {}
    if not net_req.get("has_shortage"):
        return None
    quotation_id = str(plan.get("quotation_id", ""))
    signature = _shortage_signature(net_req)
    dedup_key = f"{EVENT_TYPE_SHORTAGE}:{quotation_id}:{signature}"

    shortage_items = [
        {
            "item_id": si.get("item_id", ""),
            "net_requirement": str(si.get("net_requirement", "")),
            "warehouse_qty": str(si.get("warehouse_qty", "")),
            "purchase_qty": str(si.get("purchase_qty", "")),
            "price_status": si.get("price_status", ""),
        }
        for si in (net_req.get("shortage_items") or [])
        if isinstance(si, dict)
    ]
    recommended = str(plan.get("recommended_option_id") or "")
    options = [
        {
            "option_id": o.get("option_id", ""),
            "supplier_name": o.get("supplier_name", ""),
            "lead_time_days": o.get("lead_time_days"),
            "coverage": o.get("coverage"),
            "total_cost": o.get("total_cost"),
            "currency": o.get("currency", ""),
        }
        for o in (plan.get("supplier_options") or [])
        if isinstance(o, dict)
    ]
    payload = {
        "plan_id": str(plan.get("plan_id", "")),
        "quotation_id": quotation_id,
        "recommended_option_id": recommended,
        "shortage_items": shortage_items,
        "supplier_options": options,
        "quotation_status": plan.get("quotation_status", ""),
        "source": "procurement_analyze",
    }
    def make_row(dedup_key: str) -> CollaborationEventRow:
        return CollaborationEventRow(
            event_id=_new_event_id(),
            event_type=EVENT_TYPE_SHORTAGE,
            dedup_key=dedup_key,
            status=EVENT_STATUS_PENDING,
            payload_json=payload,
            max_retries=DEFAULT_MAX_RETRIES,
        )

    return await _trigger_with_dedup(dedup_key, make_row)


async def _find_work_order_for_quotation(quotation_id: str) -> tuple[str, list[dict[str, str]]]:
    """报价 → ERP 订单 → MES 工单（正式关联只读反查；缺失/失败如实记缺口）。"""
    from app.services.order_linkage import get_order_mes_link

    quotation = real_order.get_quotation(quotation_id) or {}
    erp_order_id = str(
        quotation.get("erp_draft_id") or quotation.get("source_erp_order_id") or ""
    ).strip()
    if not erp_order_id:
        return "", [{
            "dimension": "delivery",
            "detail": f"报价 {quotation_id} 尚未创建 ERP 销售订单草稿，无法反查关联工单",
        }]
    try:
        link = await get_order_mes_link(erp_order_id)
    except Exception as exc:
        return "", [{
            "dimension": "delivery",
            "detail": f"订单 {erp_order_id} 关联查询失败：{exc}",
        }]
    if str(link.get("status", "")) != "LINKED":
        return "", [{
            "dimension": "delivery",
            "detail": f"ERP 订单 {erp_order_id} 未建立 MES 工单正式关联（customer_order_no）",
        }]
    links = (link.get("association") or {}).get("links") or [{}]
    wo_id = str((links[0].get("mes_record") or {}).get("record_id", ""))
    return wo_id, []


async def _process_shortage(payload: dict[str, Any]) -> dict[str, Any]:
    """缺料事件协同：供应商方案（分析已产出）→ 推荐方案成本影响 → 交期影响。

    全部只读技能；任一维度缺数据如实记缺口。
    """
    try:
        plan_id = str(payload.get("plan_id", ""))
        quotation_id = str(payload.get("quotation_id", ""))
        recommended = str(payload.get("recommended_option_id") or "")
        options = payload.get("supplier_options") or []
        gaps: list[dict[str, str]] = []

        cost_assessment = None
        if recommended:
            try:
                cost = await real_order.assess_cost_impact(plan_id, recommended)
                if isinstance(cost, dict) and cost.get("status") == "DATA_MISSING":
                    gaps.append({
                        "dimension": "cost",
                        "detail": "推荐方案成本评估返回数据缺失（价格记录不全），不做估算",
                    })
                else:
                    cost_assessment = _subset(cost, (
                        "plan_id", "option_id", "supplier_name", "revenue", "currency",
                        "material_cost_baseline", "material_cost_with_option",
                        "material_cost_delta", "per_unit_surcharge",
                        "material_margin_before", "material_margin_after",
                        "calculation_basis",
                    ))
            except Exception as exc:
                gaps.append({"dimension": "cost", "detail": f"成本影响评估失败：{exc}"})
        else:
            gaps.append({
                "dimension": "cost",
                "detail": "采购分析未产出可推荐供应商（价格不完整或无覆盖），成本影响评估跳过",
            })

        delivery_assessment = None
        wo_id, wo_gaps = await _find_work_order_for_quotation(quotation_id)
        gaps.extend(wo_gaps)
        reco_option = next((o for o in options if o.get("option_id") == recommended), None)
        lead_days = reco_option.get("lead_time_days") if reco_option else None
        if wo_id and isinstance(lead_days, (int, float)) and lead_days > 0:
            ready = (date.today() + timedelta(days=int(lead_days))).isoformat()
            try:
                delivery = await real_order.assess_delivery_impact(wo_id, ready)
                delivery_assessment = _subset(delivery, (
                    "work_order_id", "work_order_no", "due_date", "material_ready_date",
                    "buffer_days", "verdict", "conclusion", "completion_rate",
                ))
            except Exception as exc:
                gaps.append({"dimension": "delivery", "detail": f"交期影响评估失败：{exc}"})

        conclusions = [
            f"采购维度：缺料 {len(payload.get('shortage_items') or [])} 项，"
            f"共 {len(options)} 个供应商选项（方案 {plan_id}），推荐 {recommended or '无（价格不完整）'}"
        ]
        if cost_assessment:
            conclusions.append(
                f"成本维度（报价智能体）：推荐方案材料成本变化 {cost_assessment.get('material_cost_delta')} "
                f"{cost_assessment.get('currency', '')}（口径：{cost_assessment.get('calculation_basis', '')}）"
            )
        if delivery_assessment:
            conclusions.append(f"交期维度（跟单智能体）：{delivery_assessment.get('conclusion', '')}")
        conclusions.append(
            "本事件为只读协同结论；是否采用方案、审批与 PO 草稿仍需人工在方案卡片走审批门禁"
        )
        result = {
            "shortage": {
                "quotation_id": quotation_id,
                "plan_id": plan_id,
                "recommended_option_id": recommended,
                "shortage_items": payload.get("shortage_items") or [],
                "supplier_options": options,
            },
            "cost_assessment": cost_assessment,
            "delivery_assessment": delivery_assessment,
            "conclusions": conclusions,
            "data_gaps": gaps,
            "generated_at": _now().isoformat(),
            "authority": "ERPNext + OpenMES（只读协同，未写入）",
        }
        return {"result": result}
    except Exception as exc:
        return {"error": {"message": f"{type(exc).__name__}: {str(exc)[:400]}", "at": _now().isoformat()}}


# ========== P1 扩展：生产延期事件（事实触发：工单已过交期且未完成） ==========

_DONE_STATUSES = {"DONE", "COMPLETED", "CLOSED", "CANCELLED"}


def _parse_due_date(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            datetime.strptime(text, fmt)
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def trigger_overdue_event_if_needed(track: dict[str, Any]) -> dict[str, Any] | None:
    """跟单读取后的事实型延期检查（track_order 钩子调用）。

    只在"工单已过交期且未完成"这一**事实**成立时触发；"提前 N 天预警"
    类阈值属于业务规则，未获用户确认前不做。去重：每工单每天一条。
    """
    try:
        status = str(track.get("status", "")).upper()
        completion = float(track.get("completion_rate") or 0.0)
        due = _parse_due_date(track.get("due_date"))
        if due is None or status in _DONE_STATUSES or completion >= 100.0:
            return None
        if due >= date.today():
            return None
        wo_id = str(track.get("work_order_id", ""))
        wo_no = str(track.get("work_order_no", ""))
        dedup_key = f"{EVENT_TYPE_OVERDUE}:{wo_id or wo_no}:{date.today().isoformat()}"
        def make_row(row_dedup_key: str) -> CollaborationEventRow:
            return CollaborationEventRow(
                event_id=_new_event_id(),
                event_type=EVENT_TYPE_OVERDUE,
                dedup_key=row_dedup_key,
                status=EVENT_STATUS_PENDING,
                payload_json={
                    "work_order_id": wo_id,
                    "work_order_no": wo_no,
                    "status": track.get("status", ""),
                    "completion_rate": track.get("completion_rate"),
                    "completed_qty": track.get("completed_qty", ""),
                    "quantity": track.get("quantity", ""),
                    "due_date": track.get("due_date", ""),
                    "eta_status": track.get("eta_status", ""),
                    "source": "track_order",
                },
                max_retries=DEFAULT_MAX_RETRIES,
            )

        # 延期事件包装语义：首次触发返回事件摘要；同日重复（deduplicated）返回 None
        result = await _trigger_with_dedup(dedup_key, make_row)
        return None if result.get("deduplicated") else result
    except Exception:
        logger.exception("延期事件检查失败（不影响跟单查询）")
        return None


# ========== P1 扩展：生产临期风险事件（阈值规则经用户确认：2026-10-02） ==========
# 规则版本 at_risk_v1：距交期 0-3 天、未完成、完成率 <50% 时预警。
# 阈值不是系统默认值，是用户显式确认的业务规则；改动必须先经用户重新确认。
AT_RISK_RULE_VERSION = "at_risk_v1"
AT_RISK_DAYS_BEFORE_DUE = 3
AT_RISK_COMPLETION_THRESHOLD = 50.0


async def trigger_at_risk_event_if_needed(track: dict[str, Any]) -> dict[str, Any] | None:
    """跟单读取后的临期风险检查（track_order 钩子调用，与延期检查同点位）。

    与事实型延期事件的边界：已过交期（due < 今天）由 production_overdue 负责；
    本事件只覆盖"尚未逾期但已临期"的窗口（today <= due <= today+3）。
    去重：每工单每天一条，复用 _trigger_with_dedup 幂等入口。
    """
    try:
        status = str(track.get("status", "")).upper()
        completion = float(track.get("completion_rate") or 0.0)
        due = _parse_due_date(track.get("due_date"))
        if due is None or status in _DONE_STATUSES or completion >= AT_RISK_COMPLETION_THRESHOLD:
            return None
        today = date.today()
        if due < today or due > today + timedelta(days=AT_RISK_DAYS_BEFORE_DUE):
            return None
        wo_id = str(track.get("work_order_id", ""))
        wo_no = str(track.get("work_order_no", ""))
        days_left = (due - today).days
        dedup_key = f"{EVENT_TYPE_AT_RISK}:{wo_id or wo_no}:{today.isoformat()}"

        def make_row(row_dedup_key: str) -> CollaborationEventRow:
            return CollaborationEventRow(
                event_id=_new_event_id(),
                event_type=EVENT_TYPE_AT_RISK,
                dedup_key=row_dedup_key,
                status=EVENT_STATUS_PENDING,
                payload_json={
                    "work_order_id": wo_id,
                    "work_order_no": wo_no,
                    "status": track.get("status", ""),
                    "completion_rate": track.get("completion_rate"),
                    "completed_qty": track.get("completed_qty", ""),
                    "quantity": track.get("quantity", ""),
                    "due_date": track.get("due_date", ""),
                    "days_left": days_left,
                    "eta_status": track.get("eta_status", ""),
                    "rule_version": AT_RISK_RULE_VERSION,
                    "source": "track_order",
                },
                max_retries=DEFAULT_MAX_RETRIES,
            )

        result = await _trigger_with_dedup(dedup_key, make_row)
        return None if result.get("deduplicated") else result
    except Exception:
        logger.exception("临期风险事件检查失败（不影响跟单查询）")
        return None


async def _process_at_risk(payload: dict[str, Any]) -> dict[str, Any]:
    """临期风险事件协同：事实汇总 + 如实边界（是否加急/调整排程需人工决策）。"""
    try:
        due = _parse_due_date(payload.get("due_date"))
        days_left = (due - date.today()).days if due else payload.get("days_left")
        completion = payload.get("completion_rate")
        conclusions = [
            f"生产维度：工单 {payload.get('work_order_no') or payload.get('work_order_id')} "
            f"距交期还有 {days_left if days_left is not None else '?'} 天，"
            f"完成率 {completion}%（低于预警阈值 {AT_RISK_COMPLETION_THRESHOLD:.0f}%，"
            f"规则 {payload.get('rule_version') or AT_RISK_RULE_VERSION}，已过交期则转延期事件）"
        ]
        if payload.get("eta_status") == "DATA_MISSING":
            conclusions.append("ETA 口径：DATA_MISSING（无实际速率记录，系统不做固定天数预测）")
        conclusions.append(
            "是否加急、调整排程或通知客户需人工决策；"
            "相关写入（如加急采购）仍须走审批门禁，系统不代承诺客户"
        )
        result = {
            "tracking": {
                "work_order_id": payload.get("work_order_id", ""),
                "work_order_no": payload.get("work_order_no", ""),
                "status": payload.get("status", ""),
                "completion_rate": completion,
                "completed_qty": payload.get("completed_qty", ""),
                "quantity": payload.get("quantity", ""),
                "due_date": payload.get("due_date", ""),
                "eta_status": payload.get("eta_status", ""),
            },
            "days_left": days_left,
            "rule_version": payload.get("rule_version") or AT_RISK_RULE_VERSION,
            "conclusions": conclusions,
            "data_gaps": [],
            "generated_at": _now().isoformat(),
            "authority": "OpenMES（只读协同，未写入）",
        }
        return {"result": result}
    except Exception as exc:
        return {"error": {"message": f"{type(exc).__name__}: {str(exc)[:400]}", "at": _now().isoformat()}}


async def _process_overdue(payload: dict[str, Any]) -> dict[str, Any]:
    """延期事件协同：事实汇总 + 如实边界（是否通知客户/如何追赶需人工决策）。"""
    try:
        due = _parse_due_date(payload.get("due_date"))
        overdue_days = (date.today() - due).days if due else None
        completion = payload.get("completion_rate")
        conclusions = [
            f"生产维度：工单 {payload.get('work_order_no') or payload.get('work_order_id')} "
            f"已过交期 {overdue_days if overdue_days is not None else '?'} 天仍未完成"
            f"（当前完成率 {completion}%，状态 {payload.get('status', '')}）"
        ]
        if payload.get("eta_status") == "DATA_MISSING":
            conclusions.append("ETA 口径：DATA_MISSING（无实际速率记录，系统不做固定天数预测）")
        conclusions.append(
            "是否通知客户、如何追赶（加急/换供应商/调整排程）需人工决策；"
            "相关写入（如加急采购）仍须走审批门禁，系统不代承诺客户"
        )
        result = {
            "tracking": {
                "work_order_id": payload.get("work_order_id", ""),
                "work_order_no": payload.get("work_order_no", ""),
                "status": payload.get("status", ""),
                "completion_rate": completion,
                "completed_qty": payload.get("completed_qty", ""),
                "quantity": payload.get("quantity", ""),
                "due_date": payload.get("due_date", ""),
                "eta_status": payload.get("eta_status", ""),
            },
            "overdue_days": overdue_days,
            "conclusions": conclusions,
            "data_gaps": [],
            "generated_at": _now().isoformat(),
            "authority": "OpenMES（只读协同，未写入）",
        }
        return {"result": result}
    except Exception as exc:
        return {"error": {"message": f"{type(exc).__name__}: {str(exc)[:400]}", "at": _now().isoformat()}}
