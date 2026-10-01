"""任务型协同问答的会话与业务任务服务（P0）。

职责：
- 会话/业务任务的建立与沿用（assistant_sessions / business_tasks）；
- 用户消息的确定性解析（assistant_context：新值覆盖、沿用回显、歧义追问）；
- 确定性处理器：
  * "数量改成 3000" → 沿用客户/物料/交期重新只读报价分析（不写 ERP、
    不审批），并把已有报价审批/采购方案/ERP 草稿/工单标记为需要重新确认；
  * "选第二个方案" → 按 supplier_options 展示顺序映射稳定 option_id 并
    保存快照；方案不存在/过期/顺序变化时先要求重新选择；
  * 必填槽位缺失 → 确定性追问（缺什么/为什么/补充后调用哪个智能体）；
- 其余问题交给协调者（LLM），并把沿用上下文与页面上下文一并注入；
- 保留策略：原始消息与协调者运行记录按 ASSISTANT_RETENTION_DAYS（默认
  90 天）过期惰性清理；业务任务摘要/状态/关联永久保留；凭据/令牌按铁律
  从不进入任何持久化内容。

写入边界：本服务只读真实 ERP/MES；analyze_quotation 生成的是本地报价记
录（与 8 步流程一致的业务留痕），绝不调用任何审批/写 ERP/MES 函数。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from app.persistence.database import SessionLocal
from app.persistence.models import (
    AssistantMessageRow,
    AssistantSessionRow,
    BusinessTaskRow,
    RealAgentRunRow,
)
from app.services import real_order
from app.services.assistant_context import (
    CONTEXT_FIELDS,
    Signals,
    classify_primary_intent,
    extract_signals,
    format_clarify_answer,
    format_context_echo,
    merge_context,
    missing_slots,
    retention_expiry,
    validate_ordinal_selection,
)

logger = logging.getLogger(__name__)

# 前端页面流程上下文键 → 可沿用实体字段（页面当前状态作为上下文来源之一）
PAGE_KEY_MAP = {
    "当前流程ERP订单号": "erp_draft_id",
    "当前流程MES工单id": "work_order_id",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def purge_expired_assistant_data() -> int:
    """惰性清理过期会话数据（原始消息 + 协调者运行记录）。

    业务任务、审批、方案、ERP/MES 写入记录及其回读/幂等证据永久保留，
    不在本清理范围内。返回删除行数（仅日志用途）。
    """
    now = _now()
    removed = 0
    try:
        with SessionLocal() as session:
            removed += session.execute(
                delete(AssistantMessageRow).where(AssistantMessageRow.expires_at.is_not(None))
                .where(AssistantMessageRow.expires_at < now)
            ).rowcount or 0
            removed += session.execute(
                delete(RealAgentRunRow).where(RealAgentRunRow.expires_at.is_not(None))
                .where(RealAgentRunRow.expires_at < now)
            ).rowcount or 0
            session.commit()
    except Exception:
        logger.exception("会话保留期清理失败（不影响本次问答）")
        return 0
    return removed


def _get_or_create_session(session_id: str | None, question: str) -> tuple[AssistantSessionRow, bool]:
    with SessionLocal() as session:
        if session_id:
            row = session.get(AssistantSessionRow, session_id)
            if row is not None:
                row.last_active_at = _now()
                session.commit()
                return row, False
        row = AssistantSessionRow(
            session_id=_new_id("ASST"),
            title=question.strip()[:200],
        )
        session.add(row)
        session.commit()
        return row, True


def _get_or_create_task(session_row: AssistantSessionRow) -> BusinessTaskRow:
    with SessionLocal() as session:
        if session_row.business_task_id:
            row = session.get(BusinessTaskRow, session_row.business_task_id)
            if row is not None:
                return row
        row = BusinessTaskRow(
            task_id=_new_id("TASK"),
            title=session_row.title[:200],
            entity_context={},
            stale_downstream=[],
        )
        session.add(row)
        session.commit()
        session_row.business_task_id = row.task_id
        session.add(session_row)
        session.commit()
        return row


def _save_task(task: BusinessTaskRow) -> None:
    with SessionLocal() as session:
        task.updated_at = _now()
        session.merge(task)
        session.commit()


def _save_message(session_id: str, role: str, content: str) -> None:
    with SessionLocal() as session:
        session.add(AssistantMessageRow(
            session_id=session_id,
            role=role,
            content=content,
            expires_at=retention_expiry(),
        ))
        session.commit()


def _task_from_row(row: BusinessTaskRow) -> BusinessTaskRow:
    """detach 后仍可访问的对象（SessionLocal with 块外使用）。"""
    return row


def _stale_from_context(entity_context: dict[str, Any], new_quantity: int) -> list[dict[str, str]]:
    """数量变更后，按已有实体标记需要重新确认的下游结果。"""
    stale: list[dict[str, str]] = []
    reason = f"数量已改为 {new_quantity}，需要基于新数量重新确认"
    if entity_context.get("quotation_id"):
        stale.append({"type": "quotation", "id": entity_context["quotation_id"], "reason": reason + "（原报价及其审批状态不再适用）"})
    if entity_context.get("plan_id"):
        stale.append({"type": "procurement_plan", "id": entity_context["plan_id"], "reason": reason + "（缺料清单与供应商方案会随数量变化）"})
    if entity_context.get("erp_draft_id"):
        stale.append({"type": "erp_so_draft", "id": entity_context["erp_draft_id"], "reason": reason + "（ERP 销售订单草稿数量未变，需人工确认是否作废重开）"})
    if entity_context.get("purchase_order_id"):
        stale.append({"type": "erp_po_draft", "id": entity_context["purchase_order_id"], "reason": reason})
    if entity_context.get("work_order_id") or entity_context.get("work_order_no"):
        stale.append({
            "type": "mes_work_order",
            "id": entity_context.get("work_order_no") or entity_context.get("work_order_id"),
            "reason": reason + "（MES 工单计划数量与报价数量可能不再一致）",
        })
    return stale


def _update_context_from_call_chain(entity_context: dict[str, Any], call_chain: list[dict[str, Any]]) -> None:
    """从协调者调用链的入参确定性更新实体上下文（不解析结果体，不猜测）。

    只映射白名单字段：工具入参是什么就记录什么。
    """
    arg_field_map = {
        "erp_order_id": "erp_order_id",
        "work_order_no": "work_order_no",
        "work_order_id": "work_order_id",
        "quotation_id": "quotation_id",
        "plan_id": "plan_id",
        "item_code": "item_code",
        "customer_id": "customer_id",
        "delivery_date": "delivery_date",
    }
    quantity_tools = {"quotation.analyze_real"}
    for step in call_chain or []:
        if step.get("status") != "ok":
            continue
        args = step.get("arguments") or {}
        if not isinstance(args, dict):
            continue
        for arg_key, ctx_key in arg_field_map.items():
            value = args.get(arg_key)
            if value not in (None, ""):
                entity_context[ctx_key] = value
        if step.get("skill_id") in quantity_tools and args.get("quantity") is not None:
            entity_context["quantity"] = args["quantity"]


def _build_applied_context(
    merged: dict[str, Any], updates: list[str], page_context: dict[str, Any] | None
) -> str:
    """回显文案 = 页面流程上下文（如有）+ 任务沿用上下文。"""
    echo_parts: list[str] = []
    page_echo = format_context_echo(page_context or {})
    if page_echo:
        echo_parts.append(page_echo.replace("；如果需要修改请直接说明。", ""))
    task_echo = format_context_echo(merged)
    if task_echo:
        echo_parts.append(task_echo)
    if not echo_parts:
        return ""
    return "；".join(echo_parts)


def _quantity_change_response(
    question: str,
    task: BusinessTaskRow,
    merged: dict[str, Any],
    updates: list[str],
    stale: list[dict[str, str]],
    analysis: dict[str, Any] | None,
    analysis_error: str | None,
) -> tuple[str, dict[str, Any]]:
    """数量变更的确定性回答（不写 ERP、不审批）。"""
    lines = [
        f"已按新数量 {merged.get('quantity')} 重新执行一次**只读**报价分析"
        f"（沿用客户={merged.get('customer_id') or '未提供'}、物料={merged.get('item_code') or '未提供'}"
        f"、交期={merged.get('delivery_date') or '未提供'}）。",
    ]
    if analysis_error:
        lines.append(f"本次重新分析未完成：{analysis_error}")
        lines.append("未写入 ERP，也未做任何审批。")
    elif analysis is not None:
        status = analysis.get("status", "ok")
        if status == "DATA_MISSING":
            missing = analysis.get("missing_data") or []
            lines.append("报价结果：**数据缺失，无法自动报价**（不做任何编造）。")
            for item in missing[:6]:
                detail = item.get("detail") if isinstance(item, dict) else str(item)
                lines.append(f"- {detail}")
        else:
            unit_price = analysis.get("unit_price")
            currency = analysis.get("currency", "")
            total = analysis.get("total_price")
            delivery = analysis.get("delivery_estimate") or {}
            lines.append(
                f"新报价：单价 {unit_price} {currency}，总价 {total} {currency}"
                f"（报价编号 {analysis.get('quotation_id', '')}，状态 {analysis.get('status', 'DRAFT')}，未审批）。"
            )
            if delivery:
                lines.append(f"交付估算：{delivery.get('conclusion') or delivery.get('basis') or '见详情'}。")
        lines.append("已写入 ERP 吗？**没有**。已自动审批吗？**没有**。如需继续，请在页面走报价审批 → ERP 草稿 → 采购分析流程。")
    if stale:
        lines.append("**以下已有结果需要重新确认（不能继续沿用）**：")
        for item in stale:
            label = {
                "quotation": "报价",
                "procurement_plan": "采购方案",
                "erp_so_draft": "ERP 销售订单草稿",
                "erp_po_draft": "ERP 采购订单草稿",
                "mes_work_order": "MES 工单",
            }.get(item["type"], item["type"])
            lines.append(f"- {label} {item['id']}：{item['reason']}")
    return "\n".join(lines), analysis or {}


def _selection_response(
    ordinal: int,
    chosen: dict[str, Any],
    active_plan: dict[str, Any],
) -> str:
    lines = [
        f"已按当前方案（{active_plan.get('plan_id', '')}）选项展示顺序选择**第 {ordinal} 个方案**：",
        f"- 供应商：{chosen.get('supplier_name', '')}（稳定选项号 {chosen.get('option_id', '')}）",
        f"- 采购总价：{chosen.get('total_cost', '价格数据不完整')} {chosen.get('currency', '')}",
        f"- 交期：{chosen.get('lead_time_days', '缺数据') if chosen.get('lead_time_days') is not None else '缺数据'} 天"
        f"（来源：{chosen.get('lead_time_source', 'missing')}）",
        "",
        "这一步只是**选择确认**，不是执行：真实写入仍需在方案卡片上点"
        "「选择此方案并起草 PO」并经过人工审批门禁（审批 → 写回 → 回读 → 幂等）。",
    ]
    return "\n".join(lines)


async def handle_ask(
    question: str,
    coordinator_factory,
    session_id: str | None = None,
    page_context: dict[str, Any] | None = None,
    on_step=None,
) -> dict[str, Any]:
    """协同问答主入口：确定性层（会话/上下文/槽位/指令）+ 协调者兜底。

    coordinator_factory：仅在实际需要 LLM 协同时才调用的惰性构造器；
    DeepSeek 未配置时确定性路径（追问/数量变更/方案选择）仍可用。
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    purge_expired_assistant_data()

    session_row, _created = _get_or_create_session(session_id, question)
    task = _get_or_create_task(session_row)
    entity_context = dict(task.entity_context or {})

    # 页面流程上下文映射为可沿用字段（仅在任务上下文尚未有值时补充）
    page_norm: dict[str, Any] = {}
    for key, value in (page_context or {}).items():
        mapped = PAGE_KEY_MAP.get(key, key)
        if mapped in CONTEXT_FIELDS and value not in (None, ""):
            page_norm[mapped] = value
    base_context = dict(entity_context)
    for key, value in page_norm.items():
        if not base_context.get(key):
            base_context[key] = value

    signals: Signals = extract_signals(question)
    merged, updates = merge_context(base_context, signals)
    task.entity_context = merged
    echo = _build_applied_context(merged, updates, page_norm)

    result: dict[str, Any] = {
        "question": question,
        "session_id": session_row.session_id,
        "business_task_id": task.task_id,
        "context_updates": updates,
        "applied_context": echo,
        "needs_input": False,
        "missing_slots": [],
        "stale_downstream": list(task.stale_downstream or []),
        "authority": "ERPNext + OpenMES（各步骤结果含各自 authority/evidence）",
        "assembled_at": _now().isoformat(),
    }

    def _finish(answer: str, handled_by: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        result["answer"] = answer
        result["handled_by"] = handled_by
        if extra:
            result.update(extra)
        # 契约保证：前端依赖 call_chain/rounds/tool_count 字段恒存在
        # （2026-10-01 页面走查实测：确定性回答缺 call_chain 会让
        # AssistantPanel 的 call_chain.map() 崩溃降级）。
        result.setdefault("call_chain", [])
        result.setdefault("rounds", 0)
        result.setdefault("tool_count", 0)
        result.setdefault("coordination_run_id", "")
        task.summary = f"最近问题：{question[:120]} | 沿用：{echo[:160]}"
        _save_task(task)
        _save_message(session_row.session_id, "user", question)
        _save_message(session_row.session_id, "assistant", answer)
        return result

    # ---- 确定性指令 1：数量变更 → 只读重新报价 + 下游标记 ----
    if signals.quantity_change is not None:
        # 客户/物料/交期可从任务沿用的已保存报价记录确定性解析（真实存储
        # 数据，不是猜测）；解析后仍缺失才追问。
        if (not merged.get("customer_id") or not merged.get("item_code")) and merged.get("quotation_id"):
            carried = real_order.get_quotation(str(merged["quotation_id"])) or {}
            carried_values = (
                ("customer_id", (carried.get("customer") or {}).get("customer_id", "")),
                ("item_code", (carried.get("item") or {}).get("item_id", "")),
                ("delivery_date", carried.get("delivery_date", "")),
            )
            for key, value in carried_values:
                if not merged.get(key) and value:
                    merged[key] = value
            task.entity_context = merged
        if not (merged.get("customer_id") and merged.get("item_code")):
            missing = missing_slots("quotation", merged)
            return _finish(
                format_clarify_answer("quotation", missing, echo),
                "clarify",
                {"needs_input": True, "missing_slots": missing},
            )
        stale = _stale_from_context(entity_context, signals.quantity_change)
        task.stale_downstream = stale
        task.status = "RECONFIRMATION_REQUIRED" if stale else "ACTIVE"
        analysis: dict[str, Any] | None = None
        analysis_error: str | None = None
        try:
            analysis = await real_order.analyze_quotation(
                str(merged["customer_id"]),
                str(merged["item_code"]),
                int(signals.quantity_change),
                delivery_date=merged.get("delivery_date") or None,
                source_erp_order_id=merged.get("erp_order_id") or None,
            )
            if isinstance(analysis, dict) and analysis.get("quotation_id"):
                merged["quotation_id"] = analysis["quotation_id"]
                task.entity_context = merged
        except Exception as exc:  # 集成错误如实转告，不伪造报价
            analysis_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        answer, _ = _quantity_change_response(
            question, task, merged, updates, stale, analysis, analysis_error
        )
        return _finish(answer, "deterministic_quantity_change", {
            "stale_downstream": stale,
        })

    # ---- 确定性指令 2：选第 N 个方案 → 展示顺序映射 + 快照校验 ----
    if signals.ordinal_selection is not None:
        current_plan = None
        plan_id = (task.active_plan or {}).get("plan_id", "")
        if plan_id:
            current_plan = real_order.get_procurement_plan(plan_id)
        chosen, error = validate_ordinal_selection(
            signals.ordinal_selection, task.active_plan, current_plan
        )
        if error or chosen is None:
            return _finish(error or "无法识别所选方案。", "deterministic_selection", {
                "needs_input": True,
            })
        task.selected_option_id = chosen.get("option_id", "")
        active_plan = dict(task.active_plan or {})
        active_plan["selected_option"] = chosen
        active_plan["selected_ordinal"] = signals.ordinal_selection
        task.active_plan = active_plan
        answer = _selection_response(signals.ordinal_selection, chosen, active_plan)
        return _finish(answer, "deterministic_selection", {
            "pending_selection": {
                "plan_id": active_plan.get("plan_id", ""),
                "option_id": chosen.get("option_id", ""),
                "option_snapshot": chosen,
            },
        })

    # ---- 确定性指令 3：意图明确且信息不足 → 追问（不调用大模型） ----
    intent = classify_primary_intent(signals)
    concrete = signals.has_any_entity() or signals.quantity_mentioned is not None or bool(
        {k: v for k, v in merged.items() if k in ("erp_order_id", "work_order_no", "work_order_id", "quotation_id", "item_code")}
    )
    if intent and concrete:
        missing = missing_slots(intent, merged)
        if missing:
            return _finish(
                format_clarify_answer(intent, missing, echo),
                "clarify",
                {"needs_input": True, "missing_slots": missing},
            )

    # ---- 其余：协调者（LLM 动态协同） ----
    ask_context: dict[str, Any] = {}
    for key, value in page_norm.items():
        if value not in (None, ""):
            ask_context[f"页面_{key}"] = value
    for key, value in merged.items():
        if value not in (None, ""):
            ask_context[f"沿用_{key}"] = value
    ask_context["business_task_id"] = task.task_id
    coordinator = coordinator_factory()
    try:
        coordinator_result = await coordinator.ask(
            question,
            context=ask_context,
            on_step=on_step,
            run_meta={
                "session_id": session_row.session_id,
                "business_task_id": task.task_id,
            },
        )
    finally:
        aclose = getattr(coordinator, "aclose", None)
        if aclose is not None:
            await aclose()

    _update_context_from_call_chain(merged, coordinator_result.get("call_chain") or [])
    task.entity_context = merged

    proposal = coordinator_result.get("proposal_options")
    if isinstance(proposal, dict) and proposal.get("plan_id"):
        task.active_plan = {
            "plan_id": proposal.get("plan_id", ""),
            "quotation_id": proposal.get("quotation_id", ""),
            "supplier_options": proposal.get("supplier_options") or [],
            "shortage": proposal.get("shortage") or {},
            "collected_at": _now().isoformat(),
            "run_id": coordinator_result.get("coordination_run_id", ""),
        }

    if task.stale_downstream:
        task.status = "RECONFIRMATION_REQUIRED"

    coordinator_extras = {
        "call_chain": coordinator_result.get("call_chain") or [],
        "rounds": coordinator_result.get("rounds", 0),
        "tool_count": coordinator_result.get("tool_count", 0),
        "coordination_run_id": coordinator_result.get("coordination_run_id", ""),
    }
    if proposal:
        coordinator_extras["proposal_options"] = proposal
    if coordinator_result.get("empty_answer_recovered"):
        coordinator_extras["empty_answer_recovered"] = True
    return _finish(coordinator_result.get("answer", ""), "coordinator", coordinator_extras)
