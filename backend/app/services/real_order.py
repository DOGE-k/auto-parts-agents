"""
真实订单业务链服务。

基于真实 ERPNext 和 OpenMES 数据，跑通报价→审批→ERP草稿→跟单→质量→发运门禁
的完整垂直闭环。所有数据都标明来源系统和原始记录编号。

与 scenarios.py 的区别：
- scenarios.py 使用 MockERP/MockMES 和固定 fixture，仅用于测试/回放
- 本模块使用真实适配器，APP_ADAPTER_MODE=real 时连接真实系统
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.adapters.factory import (
    get_adapter_mode,
    get_erp_adapter,
    get_mes_adapter,
    set_approval_verifier,
)
logger = logging.getLogger(__name__)


# ========== 审批注册表（数据库持久化，重启不丢失） ==========
import functools

from app.persistence.database import SessionLocal
from app.persistence.models import (
    RealAgentRunRow,
    RealApprovalRow,
    RealProcurementPlanRow,
    RealQuotationRow,
)


def _to_jsonable(obj: Any) -> Any:
    """递归转换为 JSON 可序列化结构（Decimal/datetime → str）。"""
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _save_agent_run(
    run_id: str,
    agent_type: str,
    operation: str,
    input_data: dict[str, Any],
    status: str,
    result: Any,
    error: Any,
    started_at: datetime,
) -> None:
    """持久化一次 Agent 运行（输入/结果/错误）。"""
    try:
        result_data = _to_jsonable(result) if result is not None else None
        summary = ""
        if isinstance(result_data, dict):
            summary = str(
                result_data.get("status")
                or result_data.get("success")
                or result_data.get("quality_gate_passed")
                or result_data.get("can_ship")
                or ""
            )[:200]
        with SessionLocal() as session:
            session.add(RealAgentRunRow(
                run_id=run_id,
                agent_type=agent_type,
                operation=operation,
                result_status=status,
                result_summary=summary,
                input_json=_to_jsonable(input_data),
                result_json=result_data,
                error_json=_to_jsonable(error) if error else None,
                started_at=started_at,
                finished_at=_utc_now(),
            ))
            session.commit()
    except Exception:
        # 运行记录失败不影响业务调用本身，但必须留痕
        logger.exception("Agent 运行记录写入失败 run_id=%s", run_id)


def _agent_run(agent_type: str, operation: str):
    """装饰器：记录真实 Agent 每次运行的输入、决策依据、结果和错误。

    结果体中包含 evidence / authority / data_source 等字段，可追溯到
    所用的真实 ERP/MES 记录编号。
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            run_id = _gen_id("RUN")
            input_data = {
                "args": [_to_jsonable(a) for a in args],
                "kwargs": _to_jsonable(kwargs),
            }
            started_at = _utc_now()
            try:
                result = await func(*args, **kwargs)
                _save_agent_run(run_id, agent_type, operation, input_data, "ok", result, None, started_at)
                return result
            except Exception as e:
                _save_agent_run(
                    run_id, agent_type, operation, input_data, "error", None,
                    {"type": type(e).__name__, "message": str(e)[:1000]}, started_at,
                )
                raise
        return wrapper
    return decorator


def list_agent_runs(agent_type: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """查询 Agent 运行记录（读数据库，不含完整结果体）。"""
    with SessionLocal() as session:
        q = session.query(RealAgentRunRow).order_by(RealAgentRunRow.started_at.desc())
        if agent_type:
            q = q.filter(RealAgentRunRow.agent_type == agent_type)
        rows = q.limit(min(limit, 200)).all()
        return [
            {
                "run_id": r.run_id,
                "agent_type": r.agent_type,
                "operation": r.operation,
                "result_status": r.result_status,
                "result_summary": r.result_summary,
                "input": r.input_json,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]


def get_agent_run(run_id: str) -> dict[str, Any] | None:
    """获取一次 Agent 运行的完整记录（含结果体与证据，可追溯 ERP/MES 记录编号）。"""
    with SessionLocal() as session:
        r = session.get(RealAgentRunRow, run_id)
        if r is None:
            return None
        return {
            "run_id": r.run_id,
            "agent_type": r.agent_type,
            "operation": r.operation,
            "result_status": r.result_status,
            "result_summary": r.result_summary,
            "input": r.input_json,
            "result": r.result_json,
            "error": r.error_json,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }


async def _approval_verifier(approval_id: str, approved_by: str) -> bool:
    """审批校验器：检查审批编号和审批人是否有效。

    这是 ERPNext 草稿写入的安全门禁，只有经过审批的操作才能写入 ERP。
    """
    record = get_approval(approval_id)
    if not record:
        logger.warning("审批校验失败：审批编号不存在 %s", approval_id)
        return False
    if record.get("approved_by") != approved_by:
        logger.warning("审批校验失败：审批人不匹配 %s vs %s",
                       record.get("approved_by"), approved_by)
        return False
    if not record.get("approved", False):
        logger.warning("审批校验失败：审批未通过 %s", approval_id)
        return False
    return True


def init_approval_system() -> None:
    """初始化审批系统，将校验器注册到适配器工厂。"""
    set_approval_verifier(_approval_verifier)
    logger.info("真实订单审批系统已初始化（数据库持久化）")


def _register_approval(
    approval_id: str,
    approved: bool,
    approved_by: str,
    reference_type: str,
    reference_id: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """登记一条审批记录（写入数据库）。"""
    with SessionLocal() as session:
        session.add(RealApprovalRow(
            approval_id=approval_id,
            approved=approved,
            approved_by=approved_by,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes or "",
        ))
        session.commit()
    return {
        "approval_id": approval_id,
        "approved": approved,
        "approved_by": approved_by,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "notes": notes,
        "created_at": _utc_now().isoformat(),
    }


def get_approval(approval_id: str) -> dict[str, Any] | None:
    """获取审批记录（读数据库）。"""
    with SessionLocal() as session:
        row = session.get(RealApprovalRow, approval_id)
        if row is None:
            return None
        return {
            "approval_id": row.approval_id,
            "approved": row.approved,
            "approved_by": row.approved_by,
            "reference_type": row.reference_type,
            "reference_id": row.reference_id,
            "notes": row.notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def list_approvals() -> list[dict[str, Any]]:
    """列出所有审批记录（读数据库）。"""
    with SessionLocal() as session:
        rows = session.query(RealApprovalRow).order_by(RealApprovalRow.created_at).all()
        return [
            {
                "approval_id": r.approval_id,
                "approved": r.approved,
                "approved_by": r.approved_by,
                "reference_type": r.reference_type,
                "reference_id": r.reference_id,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12].upper()}"


# ========== 报价 Agent ==========

@_agent_run("quotation", "analyze")
async def analyze_quotation(
    customer_id: str,
    item_code: str,
    quantity: int,
    delivery_date: str | None = None,
    source_erp_order_id: str | None = None,
) -> dict[str, Any]:
    """报价 Agent：读取真实 ERP/MES 数据，生成报价方案。

    数据来源（全部真实记录，占位计算已删除）：
    - 客户/物料/价格/BOM/库存: ERPNext
    - 交付期参考: 真实库存 或 MES 工单排程（due_date），无数据时明确报缺失
    - 数量折扣: 人工配置参数（显式标注，非 ERP 数据）
    - source_erp_order_id: 调用方明确给出的 ERP 订单号（声明式上下文，
      供问答链反查报价；缺失时为空，不做推断）
    """
    erp = get_erp_adapter()
    mes = get_mes_adapter()
    mode = get_adapter_mode()

    missing_data: list[dict[str, str]] = []

    # 1. 读取客户
    customer = await erp.get_customer(customer_id)

    # 2. 读取物料
    item = await erp.get_item(item_code)

    # 3. 读取价格
    today = _utc_now().strftime("%Y-%m-%d")
    prices = await erp.get_prices(item_code, today)

    # 4. 读取 BOM
    bom = await erp.get_bom(item_code)

    # 5. 读取库存（BOM 子项 + 成品）
    inventory_items = [item_code]
    if bom.get("found"):
        for bom_item in bom.get("items", []):
            inventory_items.append(bom_item["item_code"])
    inventory = await erp.get_inventory(inventory_items)

    # 6. 计算报价 —— 只使用真实价格记录
    base_price = Decimal("0")
    price_source = ""
    pricing_basis = ""
    if prices:
        selling_prices = [p for p in prices if "Selling" in p.get("price_list", "")]
        if selling_prices:
            base_price = Decimal(str(selling_prices[0]["unit_price"]))
            price_source = selling_prices[0]["price_id"]
        else:
            base_price = Decimal(str(prices[0]["unit_price"]))
            price_source = prices[0]["price_id"]
        pricing_basis = "erp_selling_price"

    if base_price == 0 and bom.get("found"):
        # 无 Selling 价格：用 BOM 子项的真实 Buying 价格计算成本，不再使用
        # 固定子项成本/固定加成（此前为 qty*10 与 ×1.3 占位，已删除）。
        bom_cost = Decimal("0")
        for bom_item in bom.get("items", []):
            sub_code = bom_item["item_code"]
            sub_prices = await erp.get_prices(sub_code, today)
            buying = [p for p in sub_prices if "Buying" in p.get("price_list", "")]
            if buying:
                bom_cost += Decimal(str(bom_item["qty_per_product"])) * Decimal(str(buying[0]["unit_price"]))
            else:
                missing_data.append({
                    "field": f"bom_sub_item_price:{sub_code}",
                    "detail": "BOM 子项缺少真实 Buying 价格记录（Item Price），无法完成成本估算",
                })
        if not missing_data:
            base_price = bom_cost
            price_source = "bom_cost_from_real_item_prices"
            pricing_basis = "erp_bom_cost_no_markup"
        else:
            pricing_basis = "missing"

    if base_price == 0:
        missing_data.append({
            "field": f"selling_price:{item_code}",
            "detail": "缺少价格数据，无法自动报价（无 Selling 价格记录且 BOM 子项价格不完整）",
        })

    # 数量折扣：已按用户决策删除（ERP 无对应业务数据，未经确认的折扣数字
    # 不得用于正式报价）。报价一律使用 ERP 真实价格原价。
    # 待折扣规则确认或 ERP 价格表支持后再恢复。
    unit_price = base_price
    total_price = (base_price * Decimal(quantity)).quantize(Decimal("0.01")) if base_price > 0 else Decimal("0")

    # 7. 检查库存是否足够
    finished_goods_stock = Decimal("0")
    for inv in inventory:
        if inv["item_id"] == item_code:
            finished_goods_stock = Decimal(str(inv["actual_qty"]))
            break

    stock_sufficient = finished_goods_stock >= Decimal(quantity)

    # 8. 交付估算 —— 只基于真实数据（库存 / MES 工单排程），删除固定公式
    if stock_sufficient:
        delivery_estimate = {
            "basis": "stock_available",
            "source": "ERPNext Bin（真实库存）",
            "estimated_days": 0,
            "note": f"成品真实库存 {finished_goods_stock} ≥ 订单数量，可按库存发货",
        }
    else:
        delivery_estimate = await _estimate_delivery_from_mes(mes, item_code)
        if delivery_estimate["basis"] == "missing":
            missing_data.append({
                "field": f"lead_time:{item_code}",
                "detail": delivery_estimate["note"],
            })

    # 9. 组装证据列表
    evidence = [
        {
            "source": customer.get("authority", "unknown"),
            "record_type": "customer",
            "record_id": customer.get("customer_id", ""),
            "summary": f"客户: {customer.get('customer_name', customer_id)}",
        },
        {
            "source": item.get("authority", "unknown"),
            "record_type": "item",
            "record_id": item.get("item_id", ""),
            "summary": f"物料: {item.get('item_name', item_code)} ({item.get('stock_uom', '')})",
        },
    ]
    if prices:
        evidence.append({
            "source": prices[0].get("authority", "unknown"),
            "record_type": "item_price",
            "record_id": price_source,
            "summary": f"价格依据: {pricing_basis}，基础价 {base_price}（无数量折扣：折扣规则未确认，报价按真实价格原价）",
        })

    if bom.get("found"):
        evidence.append({
            "source": bom.get("authority", "unknown"),
            "record_type": "bom",
            "record_id": bom.get("bom_id", ""),
            "summary": f"BOM: {len(bom.get('items', []))} 个子项",
        })

    for inv in inventory:
        evidence.append({
            "source": inv.get("authority", "unknown"),
            "record_type": "inventory",
            "record_id": f"{inv['item_id']}@{inv['warehouse']}",
            "summary": f"库存: {inv['actual_qty']} {inv['warehouse']}",
        })

    result = {
        "quotation_id": _gen_id("QUO"),
        "status": "DRAFT" if base_price > 0 else "DATA_MISSING",
        "adapter_mode": mode,
        "customer": customer,
        "item": item,
        "quantity": quantity,
        "delivery_date": delivery_date,
        # 声明式订单上下文（阶段七）：仅当调用方明确给出 ERP 订单号时记录，
        # 用于问答链反查；不做事后推断。
        "source_erp_order_id": source_erp_order_id or "",
        "base_price": str(base_price),
        "unit_price": str(unit_price),
        "total_price": str(total_price),
        "currency": (prices[0].get("currency", "CNY") if prices else "CNY"),
        "pricing_basis": pricing_basis,
        "quantity_discount_factor": 1.0,
        "quantity_discount_source": "disabled_pending_business_rule",
        "delivery_estimate": delivery_estimate,
        "bom": bom,
        "inventory": inventory,
        "stock_sufficient": stock_sufficient,
        "missing_data": missing_data,
        "evidence": evidence,
        "prices": prices,
        "created_at": _utc_now().isoformat(),
    }
    # 保存到报价存储
    save_quotation(result)
    return result


async def _estimate_delivery_from_mes(mes, item_code: str) -> dict[str, Any]:
    """基于真实 MES 工单排程估算交付参考，无数据时如实返回缺失。

    逻辑：查找该物料的未完成真实工单，用最早 due_date 距今天数作参考。
    不使用固定工期公式（此前 15+2n 占位已删除）。
    """
    try:
        work_orders = await mes.get_work_orders_strict({"limit": 100})
    except Exception:
        work_orders = []
    candidates = [
        wo for wo in work_orders
        if wo.get("product_id") == item_code
        and wo.get("due_date")
        and wo.get("status") not in ("COMPLETED", "DONE", "CANCELLED", "CLOSED")
        and not str(wo.get("work_order_no", "")).startswith("DEMO_")
    ]
    if candidates:
        earliest = min(wo["due_date"] for wo in candidates)
        due = earliest[:10]
        try:
            due_dt = datetime.fromisoformat(due)
            days = max(0, (due_dt.date() - _utc_now().date()).days)
        except ValueError:
            days = None
        wo_no = candidates[0].get("work_order_no", "")
        return {
            "basis": "mes_work_order_schedule",
            "source": f"OpenMES 工单 {wo_no}（due_date={due}）",
            "estimated_days": days,
            "note": f"需安排生产；参考真实 MES 工单 {wo_no} 的到期日 {due}",
        }
    return {
        "basis": "missing",
        "source": "",
        "estimated_days": None,
        "note": "数据缺失：无法估算交付期。ERP Item.lead_time_days 未配置（当前=0），MES 中也没有该物料的未完成工单排程。需在 ERP 补录 lead_time_days 或在 MES 建立工单。",
    }


# ========== 跟单 Agent ==========

@_agent_run("tracking", "track")
async def track_order(work_order_id: str) -> dict[str, Any]:
    """跟单 Agent：读取真实 MES 工单数据，计算 ETA 和风险。"""
    mes = get_mes_adapter()

    # 0. 严格探测 MES 可达性：不可达时如实返回 MES_UNREACHABLE，
    #    绝不把"连不上"误报成"工单不存在"。
    try:
        await mes.get_work_orders_strict({"limit": 1})
    except Exception as exc:
        return {
            "work_order_id": work_order_id,
            "work_order_no": "",
            "status": "MES_UNREACHABLE",
            "quantity": "0",
            "completed_qty": "0",
            "completion_rate": 0.0,
            "due_date": "",
            "eta": None,
            "eta_status": "DATA_MISSING",
            "eta_basis": "mes_unreachable",
            "observed_rate": None,
            "eta_data_gaps": [{"field": "mes", "detail": f"OpenMES 不可达：{exc}"}],
            "risks": [{"level": "high", "message": "MES 不可达，无法读取生产数据"}],
            "progress": [],
            "quality_issues": [],
            "line_name": "",
            "authority": "OpenMES",
            "data_source": "openmes_api",
            "calculated_at": _utc_now().isoformat(),
        }

    # 1. 读取工单详情和进度
    progress = await mes.get_operation_progress(work_order_id)
    work_orders = await mes.get_work_orders({"limit": 100})

    # 找到对应工单
    wo = None
    for w in work_orders:
        if w.get("work_order_id") == work_order_id:
            wo = w
            break

    if not wo and progress:
        wo = {
            "work_order_id": work_order_id,
            "status": "UNKNOWN",
            "quantity": progress[0].get("planned_qty", "0"),
            "completed_qty": progress[0].get("completed_qty", "0"),
        }

    if not wo:
        return {
            "work_order_id": work_order_id,
            "status": "NOT_FOUND",
            "eta": None,
            "eta_status": "DATA_MISSING",
            "eta_basis": "work_order_not_found",
            "observed_rate": None,
            "eta_data_gaps": [{"field": "work_order", "detail": "工单在 MES 中不存在（MES 可达），无法读取实际生产速率"}],
            "risks": [{"level": "high", "message": "工单不存在"}],
            "progress": [],
            "authority": "OpenMES",
        }

    # 2. 计算完成率
    planned = Decimal(str(wo.get("quantity", 0)))
    completed = Decimal(str(wo.get("completed_qty", 0)))
    completion_rate = float(completed / planned * 100) if planned > 0 else 0.0

    # 3. 风险评估
    risks = []
    due_date = wo.get("due_date", "")
    if wo.get("status") == "BLOCKED":
        risks.append({"level": "high", "message": "工单已阻塞"})
    elif completion_rate < 10 and due_date:
        # 如果离交期很近但完成率很低
        risks.append({"level": "medium", "message": "生产进度偏低，需关注交期风险"})

    # 4. 质量问题风险
    quality_records = await mes.get_quality_records({"work_order_id": work_order_id})
    open_quality_issues = [q for q in quality_records if q.get("status") != "CLOSED" and q.get("status") != "RESOLVED"]
    if open_quality_issues:
        risks.append({
            "level": "high",
            "message": f"存在 {len(open_quality_issues)} 个未关闭质量问题",
        })

    # 5. ETA 估算。只允许使用真实执行记录推导速率；due_date 是客户交期，
    # 不是生产 ETA，不能在没有速率时冒充预测结果。
    batches: list[dict[str, Any]] = []
    batch_error = ""
    try:
        batches = await mes.get_work_order_batches(work_order_id)
    except Exception as exc:
        batch_error = str(exc)
        logger.warning("读取 OpenMES 批次速率数据失败 (wo=%s): %s", work_order_id, exc)
    eta_result = _estimate_eta_from_observed_rate(
        planned=planned,
        completed=completed,
        progress=progress,
        batches=batches,
        status=str(wo.get("status", "")),
        now=_utc_now(),
    )
    if batch_error and eta_result["eta_status"] == "DATA_MISSING":
        eta_result["data_gaps"].append({
            "field": "batches",
            "detail": f"OpenMES 批次执行记录读取失败：{batch_error}",
        })

    return {
        "work_order_id": work_order_id,
        "work_order_no": wo.get("work_order_no", ""),
        "status": wo.get("status", ""),
        "quantity": str(planned),
        "completed_qty": str(completed),
        "completion_rate": round(completion_rate, 2),
        "due_date": due_date,
        "eta": eta_result["eta"],
        "eta_status": eta_result["eta_status"],
        "eta_basis": eta_result["eta_basis"],
        "observed_rate": eta_result["observed_rate"],
        "eta_data_gaps": eta_result["data_gaps"],
        "risks": risks,
        "progress": progress,
        "quality_issues": quality_records,
        "line_name": wo.get("line_name", ""),
        "authority": "OpenMES",
        "data_source": "openmes_api",
        "calculated_at": _utc_now().isoformat(),
    }


def _parse_observed_time(value: Any) -> datetime | None:
    """Parse an OpenMES timestamp while rejecting empty/planned-only values."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return number if number > 0 else None


def _rate_sample(row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Build one observed rate sample from an operation/batch execution row."""
    qty = next(
        (candidate for key in ("completed_qty", "passed_qty", "produced_qty")
         if (candidate := _positive_decimal(row.get(key))) is not None),
        None,
    )
    if qty is None:
        return None

    elapsed_minutes = next(
        (candidate for key in ("actual_elapsed_minutes", "actual_run_minutes")
         if (candidate := _positive_decimal(row.get(key))) is not None),
        None,
    )
    started = _parse_observed_time(row.get("actual_start_at") or row.get("started_at"))
    ended = _parse_observed_time(row.get("actual_end_at") or row.get("completed_at"))
    if elapsed_minutes is None and started is not None:
        end = ended or now
        seconds = (end - started).total_seconds()
        if seconds > 0:
            elapsed_minutes = Decimal(str(seconds / 60))
    if elapsed_minutes is None or elapsed_minutes <= 0:
        return None

    rate = qty / elapsed_minutes * Decimal("60")
    if rate <= 0:
        return None
    return {
        "quantity": str(qty),
        "elapsed_minutes": round(float(elapsed_minutes), 2),
        "units_per_hour": round(float(rate), 4),
        "started_at": started.isoformat() if started else "",
        "completed_at": ended.isoformat() if ended else "",
        "source": row.get("operation_id") or row.get("step_id") or row.get("batch_id") or "",
    }


def _estimate_eta_from_observed_rate(
    *,
    planned: Decimal,
    completed: Decimal,
    progress: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    status: str,
    now: datetime,
) -> dict[str, Any]:
    """Estimate completion from observed MES execution rate only.

    The latest valid operation sample is used because sequential operations may
    report the same quantity more than once. A due date or planned start is
    deliberately excluded from this calculation.
    """
    missing = []
    if planned <= 0:
        missing.append({"field": "planned_qty", "detail": "工单缺少 planned_qty，无法计算剩余产量"})
    if completed < 0:
        missing.append({"field": "produced_qty", "detail": "工单 produced_qty 无效"})
    if planned > 0 and completed >= planned and status.upper() in {"DONE", "COMPLETED", "CLOSED"}:
        return {
            "eta": None,
            "eta_status": "COMPLETED",
            "eta_basis": "observed_completion",
            "observed_rate": None,
            "data_gaps": [],
        }

    rows: list[dict[str, Any]] = list(progress)
    # Batch steps are a fallback when the work-order snapshot has no execution
    # timing. They are flattened without summing quantities across steps.
    for batch in batches:
        for step in batch.get("steps") or []:
            if isinstance(step, dict):
                rows.append({**step, "batch_id": batch.get("batch_id", "")})
        if not batch.get("steps"):
            rows.append(batch)

    samples = [sample for row in rows if isinstance(row, dict) and (sample := _rate_sample(row, now))]
    if not samples:
        missing.append({
            "field": "observed_production_rate",
            "detail": "OpenMES 没有同时包含实际产量和实际耗时/开始时间的执行记录，禁止用 due_date 或固定天数推算 ETA",
        })
        return {
            "eta": None,
            "eta_status": "DATA_MISSING",
            "eta_basis": "missing_observed_rate",
            "observed_rate": None,
            "data_gaps": missing,
        }

    sample = samples[-1]
    # Recompute from the unrounded sample values; the displayed rate is rounded
    # for readability and must not shift the ETA by seconds.
    rate = Decimal(str(sample["quantity"])) / Decimal(str(sample["elapsed_minutes"])) * Decimal("60")
    remaining = max(Decimal("0"), planned - completed)
    if remaining <= 0:
        return {
            "eta": sample.get("completed_at") or now.isoformat(),
            "eta_status": "RATE_BASED",
            "eta_basis": "observed_production_rate",
            "observed_rate": sample,
            "data_gaps": [],
        }
    hours = remaining / rate
    eta = now + timedelta(seconds=float(hours) * 3600)
    return {
        "eta": eta.isoformat(),
        "eta_status": "RATE_BASED",
        "eta_basis": "observed_production_rate",
        "observed_rate": {
            **sample,
            "remaining_qty": str(remaining),
            "estimated_remaining_hours": round(float(hours), 2),
        },
        "data_gaps": [],
    }


# ========== 质量文档 Agent ==========

@_agent_run("quality", "package")
async def quality_package(work_order_id: str) -> dict[str, Any]:
    """质量文档 Agent：读取真实质量记录与质量资料，生成质量资料包。

    OpenMES 真实接口能力（2026-09-28 源码+实测确认）：
    - 质量问题 / NCR：GET /api/v1/erp/quality/issues（有真实数据）✓
    - 检验记录：GET /api/v1/inspections（接口存在，当前 0 条数据）
    - 工程文档（SOP/Control Plan 类载体）：GET /api/v1/work-orders/{id}/engineering-documents
      （冻结快照可能缺少 document_type，按文档 ID 读取真实详情补齐）
    - 质量放行状态：**OpenMES 无对应 API** → 如实标注"当前系统不支持"，不伪造放行事件
    """
    mes = get_mes_adapter()

    # 1. 质量问题（真实记录）
    quality_records = await mes.get_quality_records({"work_order_id": work_order_id})

    # 2. 工程文档与检验记录（检验按工单批次 lot 关联，避免全局计数失真）
    documents = await mes.get_work_order_documents(work_order_id)
    inspections_all = await mes.get_inspections({"limit": 50})
    try:
        batch_rows = await mes.get_work_order_batches(work_order_id)
        batch_lots = {str(b.get("lot_number") or "") for b in batch_rows if isinstance(b, dict)}
        inspections_scope = "work_order_batch_lots"
    except Exception as exc:
        logger.warning("读取工单批次失败，检验维度无法按 lot 关联 (wo=%s): %s", work_order_id, exc)
        batch_lots = set()
        inspections_scope = "unavailable"
    if batch_lots:
        # 检验 lot 与批次 lot 可能精确相等，也可能是批次 lot 的前缀扩展
        # （如 TEST_LOT_PAGE_9-IQC-01）；两种都算关联。
        def _lot_related(lot: str) -> bool:
            return any(lot == bl or lot.startswith(bl + "-") for bl in batch_lots if bl)

        inspections = [i for i in inspections_all if _lot_related(str(i.get("lot_number") or ""))]
    else:
        # 批次不可得时不猜关联：如实返回空集，由上层标注缺口
        inspections = []

    # 3. SOP / Control Plan 完整性：基于真实工程文档记录判断
    required_docs = ["SOP", "Control Plan"]
    missing_docs = []
    doc_types_present = {d.get("doc_type", "") for d in documents}
    for doc_type in required_docs:
        if not any(doc_type.lower() in (t or "").lower() for t in doc_types_present):
            missing_docs.append(doc_type)

    # 4. 未关闭质量问题
    open_issues = [q for q in quality_records
                   if q.get("status") not in ("CLOSED", "RESOLVED")]

    # 5. 当前系统不支持的能力（如实标注，不伪造）
    unsupported_capabilities = [
        {
            "capability": "质量放行状态",
            "status": "NOT_SUPPORTED",
            "reason": "OpenMES 无质量放行 API（仅有工程文档 release 与检验 disposition，"
                      "均不是订单级质量放行），无法读取或生成放行事件",
        },
        {
            "capability": "NCR 完整处置闭环",
            "status": "AVAILABLE_WITH_APPROVAL",
            "reason": "disposition、纠正措施读取和 close 前置校验已接入；实际写回仍需质量角色、两步审批和 OpenMES 回读确认",
        },
    ]

    # 6. 质量门禁判断（基于真实数据；文档缺失含"接口存在但无数据"的情况）
    quality_gate_passed = len(open_issues) == 0 and len(missing_docs) == 0

    gate_details = {
        "open_quality_issues": len(open_issues),
        "missing_documents": missing_docs,
        "engineering_document_count": len(documents),
        "inspection_count": len(inspections),
        "inspections_total": len(inspections_all),
        "inspections_scope": inspections_scope,
        "unsupported_capabilities": [u["capability"] for u in unsupported_capabilities if u["status"] == "NOT_SUPPORTED"],
        "passed": quality_gate_passed,
        "reason": "" if quality_gate_passed else (
            f"未关闭质量问题 {len(open_issues)} 个，"
            f"缺失文档 {len(missing_docs)} 个（按 OpenMES 工单冻结文档及详情核验）"
        ),
    }

    return {
        "work_order_id": work_order_id,
        "quality_records": quality_records,
        "documents": documents,
        "inspections": inspections,
        "open_issues_count": len(open_issues),
        "missing_documents": missing_docs,
        "unsupported_capabilities": unsupported_capabilities,
        "quality_gate_passed": quality_gate_passed,
        "gate_details": gate_details,
        "authority": "OpenMES",
        "data_source": "openmes_api",
        "assembled_at": _utc_now().isoformat(),
    }


def request_quality_issue_resolution(
    issue_id: str,
    requested_by: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """建立质量问题处理审批，不触碰 OpenMES。"""
    if not issue_id.strip() or not requested_by.strip():
        return {"success": False, "error": "缺少 issue_id 或 requested_by"}
    return {
        "success": True,
        "approval": _register_approval(
            approval_id=_gen_id("QAPPR"),
            approved=False,
            approved_by=requested_by,
            reference_type="quality_issue_resolution",
            reference_id=issue_id,
            notes=notes or "",
        ),
        "written": False,
        "authority": "local_approval_store",
    }


def approve_quality_issue_resolution(approval_id: str, approved_by: str) -> dict[str, Any]:
    """在本地审批库中批准质量问题处理；仍不调用 OpenMES。"""
    with SessionLocal() as session:
        row = session.get(RealApprovalRow, approval_id)
        if row is None or row.reference_type != "quality_issue_resolution":
            return {"success": False, "error": "质量问题审批记录不存在"}
        if row.approved_by != approved_by:
            return {"success": False, "error": "审批人不匹配"}
        row.approved = True
        session.commit()
    return {"success": True, "approval": get_approval(approval_id), "written": False}


@_agent_run("quality", "resolve_issue")
async def resolve_quality_issue(
    work_order_id: str,
    issue_id: str,
    resolution_notes: str,
    approval_id: str,
    approved_by: str,
) -> dict[str, Any]:
    """审批后请求 OpenMES 处理质量问题，并回读确认。

    这是测试环境也必须遵守的写入门禁：未批准时不调用 OpenMES；批准后
    先确认 issue 属于指定工单，再调用真实 API，最后重新读取质量包。
    """
    approval = get_approval(approval_id)
    if (
        not approval
        or approval.get("reference_type") != "quality_issue_resolution"
        or str(approval.get("reference_id")) != str(issue_id)
    ):
        return {"success": False, "error": "质量问题审批记录不存在，拒绝写入"}
    if not await _approval_verifier(approval_id, approved_by):
        return {"success": False, "error": "质量问题审批未通过，拒绝写入"}
    if not resolution_notes.strip():
        return {"success": False, "error": "缺少 resolution_notes，拒绝写入"}

    quality = await quality_package(work_order_id)
    issue = next((q for q in quality.get("quality_records", []) if str(q.get("record_id")) == str(issue_id)), None)
    if issue is None:
        return {"success": False, "error": "质量问题不属于指定工单，拒绝写入 OpenMES"}

    mes = get_mes_adapter()
    write_result = await mes.resolve_quality_issue(str(issue_id), resolution_notes)
    after = await quality_package(work_order_id)
    read_back = next(
        (q for q in after.get("quality_records", []) if str(q.get("record_id")) == str(issue_id)),
        None,
    )
    verified = read_back is not None and read_back.get("status") in {"RESOLVED", "CLOSED"}
    return {
        "success": verified,
        "status": "RESOLVED" if verified else "WRITE_UNVERIFIED",
        "written": True,
        "approval": approval,
        "write_result": write_result,
        "read_back": read_back,
        "read_back_verified": verified,
        "quality_package": after,
        "authority": "OpenMES",
        "data_source": "openmes_api",
    }


# ========== NCR 处置、纠正措施与关闭门禁 ==========

_QUALITY_DISPOSITIONS = {"scrap", "rework", "return_to_supplier", "use_as_is"}
_QUALITY_ACTION_TYPES = {"corrective", "preventive", "containment"}


def _quality_issue_from_package(package: dict[str, Any], issue_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in package.get("quality_records", [])
         if str(item.get("record_id")) == str(issue_id)),
        None,
    )


def _quality_change_notes(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _quality_change_payload(approval: dict[str, Any], expected_type: str) -> dict[str, Any] | None:
    if not approval or approval.get("reference_type") != expected_type or not approval.get("approved"):
        return None
    try:
        payload = json.loads(approval.get("notes") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def request_quality_issue_disposition(
    issue_id: str,
    work_order_id: str,
    requested_by: str,
    *,
    disposition: str,
    non_conforming_qty: str | int | float | None = None,
    root_cause: str = "",
    containment_action: str = "",
    nc_source: str | None = None,
) -> dict[str, Any]:
    """校验 NCR 处置数据并只建立审批记录，不写 OpenMES。"""
    if not issue_id.strip() or not work_order_id.strip() or not requested_by.strip():
        return {"success": False, "error": "缺少 issue_id、work_order_id 或 requested_by"}
    if disposition not in _QUALITY_DISPOSITIONS:
        return {"success": False, "error": "disposition 必须是 scrap/rework/return_to_supplier/use_as_is"}
    if not root_cause.strip() or not containment_action.strip():
        return {"success": False, "error": "NCR 处置必须提供 root_cause 和 containment_action"}
    if nc_source and nc_source not in {"internal", "external", "supplier"}:
        return {"success": False, "error": "nc_source 无效"}
    payload = {
        "issue_id": issue_id,
        "work_order_id": work_order_id,
        "disposition": disposition,
        "non_conforming_qty": non_conforming_qty,
        "root_cause": root_cause.strip(),
        "containment_action": containment_action.strip(),
        "nc_source": nc_source,
    }
    approval_id = _gen_id("QDISP")
    approval = _register_approval(
        approval_id=approval_id,
        approved=False,
        approved_by=requested_by,
        reference_type="quality_issue_disposition",
        reference_id=issue_id,
        notes=_quality_change_notes(payload),
    )
    return {"success": True, "approval": approval, "written": False, "authority": "local_approval_store"}


def approve_quality_issue_disposition(approval_id: str, approved_by: str) -> dict[str, Any]:
    """批准 NCR 处置；审批人由真实身份依赖在 API 层解析。"""
    with SessionLocal() as session:
        row = session.get(RealApprovalRow, approval_id)
        if row is None or row.reference_type != "quality_issue_disposition":
            return {"success": False, "error": "NCR 处置审批记录不存在"}
        row.approved = True
        row.approved_by = approved_by
        session.commit()
    return {"success": True, "approval": get_approval(approval_id), "written": False}


@_agent_run("quality", "set_disposition")
def _same_quantity(a: Any, b: Any) -> bool:
    """按数值比较数量（1800.0 / "1800" / "1800.00" 视为相等），解析失败退回字符串。"""
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:
        return str(a) == str(b)


async def set_quality_issue_disposition(
    issue_id: str,
    approval_id: str,
    approved_by: str,
    expected_work_order_id: str | None = None,
) -> dict[str, Any]:
    """通过审批将 NCR 处置写入 OpenMES，并严格回读验证。"""
    approval = get_approval(approval_id)
    payload = _quality_change_payload(approval or {}, "quality_issue_disposition")
    if (
        payload is None
        or str(payload.get("issue_id")) != str(issue_id)
        or (expected_work_order_id is not None
            and str(payload.get("work_order_id")) != str(expected_work_order_id))
    ):
        return {"success": False, "error": "NCR 处置审批不存在、未批准或对象不匹配"}
    if not await _approval_verifier(approval_id, approved_by):
        return {"success": False, "error": "NCR 处置审批未通过，拒绝写入"}
    quality = await quality_package(str(payload["work_order_id"]))
    issue = _quality_issue_from_package(quality, issue_id)
    if issue is None:
        return {"success": False, "error": "质量问题不属于指定工单，拒绝写入 OpenMES"}
    if (
        issue.get("disposition") == payload["disposition"]
        and issue.get("root_cause") == payload["root_cause"]
        and issue.get("containment_action") == payload["containment_action"]
        and (payload.get("nc_source") in (None, "") or issue.get("nc_source") == payload.get("nc_source"))
    ):
        return {
            "success": True,
            "status": "DISPOSITION_RECORDED",
            "written": False,
            "idempotent": True,
            "approval": approval,
            "read_back": issue,
            "read_back_verified": True,
            "quality_package": quality,
            "authority": "OpenMES",
            "data_source": "openmes_api",
        }
    mes = get_mes_adapter()
    write_result = await mes.set_quality_issue_disposition(
        issue_id,
        disposition=payload["disposition"],
        non_conforming_qty=payload.get("non_conforming_qty"),
        root_cause=payload["root_cause"],
        containment_action=payload["containment_action"],
        nc_source=payload.get("nc_source"),
    )
    after = await quality_package(str(payload["work_order_id"]))
    read_back = _quality_issue_from_package(after, issue_id)
    verified = bool(
        read_back
        and read_back.get("disposition") == payload["disposition"]
        and read_back.get("root_cause") == payload["root_cause"]
        and read_back.get("containment_action") == payload["containment_action"]
        and (
            payload.get("non_conforming_qty") is None
            or "non_conforming_qty" not in read_back
            # 审批载荷（JSON number → 1800.0）与 OpenMES 回读（"1800"/"1800.00"）
            # 的数量格式不同，按数值比较，禁止用字符串比较把格式差异误判为回读不一致。
            or _same_quantity(read_back.get("non_conforming_qty"), payload.get("non_conforming_qty"))
        )
        and (
            payload.get("nc_source") in (None, "")
            or "nc_source" not in read_back
            or read_back.get("nc_source") == payload.get("nc_source")
        )
    )
    return {
        "success": verified,
        "status": "DISPOSITION_RECORDED" if verified else "WRITE_UNVERIFIED",
        "written": True,
        "approval": approval,
        "write_result": write_result,
        "read_back": read_back,
        "read_back_verified": verified,
        "quality_package": after,
        "authority": "OpenMES",
        "data_source": "openmes_api",
        **({} if verified else {"error": "NCR 处置已写入 OpenMES，但回读比对不一致，请人工核对"}),
    }


@_agent_run("quality", "closure_check")
async def assess_quality_issue_closure(work_order_id: str, issue_id: str) -> dict[str, Any]:
    """读取并验证 NCR 关闭前置条件，不执行写入。"""
    quality = await quality_package(work_order_id)
    issue = _quality_issue_from_package(quality, issue_id)
    if issue is None:
        return {"status": "error", "error": "质量问题不属于指定工单", "work_order_id": work_order_id, "issue_id": issue_id}
    mes = get_mes_adapter()
    try:
        actions = await mes.get_quality_issue_actions(issue_id)
        actions_error = ""
    except Exception as exc:
        actions = []
        actions_error = str(exc)
    checks = {
        "resolved": str(issue.get("status", "")).upper() in {"RESOLVED", "CLOSED"},
        "disposition_recorded": str(issue.get("disposition", "")).lower() in _QUALITY_DISPOSITIONS,
        "root_cause_recorded": bool(str(issue.get("root_cause", "")).strip()),
        "containment_action_recorded": bool(str(issue.get("containment_action", "")).strip()),
        "corrective_actions_verified": not actions_error and all(
            str(action.get("status", "")).lower() == "verified" for action in actions
        ),
    }
    return {
        "status": "ok",
        "work_order_id": work_order_id,
        "issue_id": issue_id,
        "issue": issue,
        "actions": actions,
        "checks": checks,
        "closure_ready": all(checks.values()),
        "data_gap": actions_error or None,
        "authority": "OpenMES",
        "data_source": "openmes_api",
    }


def request_quality_issue_close(issue_id: str, work_order_id: str, requested_by: str) -> dict[str, Any]:
    if not issue_id.strip() or not work_order_id.strip() or not requested_by.strip():
        return {"success": False, "error": "缺少 issue_id、work_order_id 或 requested_by"}
    payload = {"issue_id": issue_id, "work_order_id": work_order_id}
    approval = _register_approval(
        approval_id=_gen_id("QCLOSE"),
        approved=False,
        approved_by=requested_by,
        reference_type="quality_issue_close",
        reference_id=issue_id,
        notes=_quality_change_notes(payload),
    )
    return {"success": True, "approval": approval, "written": False, "authority": "local_approval_store"}


def approve_quality_issue_close(approval_id: str, approved_by: str) -> dict[str, Any]:
    with SessionLocal() as session:
        row = session.get(RealApprovalRow, approval_id)
        if row is None or row.reference_type != "quality_issue_close":
            return {"success": False, "error": "NCR 关闭审批记录不存在"}
        row.approved = True
        row.approved_by = approved_by
        session.commit()
    return {"success": True, "approval": get_approval(approval_id), "written": False}


@_agent_run("quality", "close_issue")
async def close_quality_issue_with_approval(
    issue_id: str,
    approval_id: str,
    approved_by: str,
    expected_work_order_id: str | None = None,
) -> dict[str, Any]:
    approval = get_approval(approval_id)
    payload = _quality_change_payload(approval or {}, "quality_issue_close")
    if (
        payload is None
        or str(payload.get("issue_id")) != str(issue_id)
        or (expected_work_order_id is not None
            and str(payload.get("work_order_id")) != str(expected_work_order_id))
    ):
        return {"success": False, "error": "NCR 关闭审批不存在、未批准或对象不匹配"}
    if not await _approval_verifier(approval_id, approved_by):
        return {"success": False, "error": "NCR 关闭审批未通过，拒绝写入"}
    check = await assess_quality_issue_closure(str(payload["work_order_id"]), issue_id)
    if not check.get("closure_ready"):
        return {"success": False, "error": "NCR 关闭前置校验未通过", "closure_check": check}
    current_issue = check.get("issue") or {}
    if str(current_issue.get("status", "")).upper() == "CLOSED":
        return {
            "success": True,
            "status": "CLOSED",
            "written": False,
            "idempotent": True,
            "approval": approval,
            "read_back": current_issue,
            "read_back_verified": True,
            "quality_package": await quality_package(str(payload["work_order_id"])),
            "authority": "OpenMES",
            "data_source": "openmes_api",
        }
    mes = get_mes_adapter()
    write_result = await mes.close_quality_issue(issue_id)
    after = await quality_package(str(payload["work_order_id"]))
    read_back = _quality_issue_from_package(after, issue_id)
    verified = bool(read_back and str(read_back.get("status", "")).upper() == "CLOSED")
    return {
        "success": verified,
        "status": "CLOSED" if verified else "WRITE_UNVERIFIED",
        "written": True,
        "approval": approval,
        "write_result": write_result,
        "read_back": read_back,
        "read_back_verified": verified,
        "quality_package": after,
        "authority": "OpenMES",
        "data_source": "openmes_api",
    }


def get_quality_issue_workflow_states(*, limit: int = 50) -> dict[str, dict[str, Any]]:
    """按 issue 聚合最近的处置/关闭审批（只读，供前端刷新后恢复面板进度）。

    每类审批只取该 issue 最新一条；审批载荷里的 work_order_id/disposition
    一并返回，前端据此恢复表单与按钮状态，避免重复建立审批。
    """
    with SessionLocal() as session:
        rows = (
            session.query(RealApprovalRow)
            .filter(
                RealApprovalRow.reference_type.in_(
                    ["quality_issue_disposition", "quality_issue_close"]
                )
            )
            .order_by(RealApprovalRow.created_at.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = states.setdefault(str(row.reference_id), {})
        key = "disposition" if row.reference_type == "quality_issue_disposition" else "close"
        if key in entry:
            continue  # 已是该类型最新一条
        work_order_id = ""
        disposition = ""
        try:
            payload = json.loads(row.notes or "{}")
            if isinstance(payload, dict):
                work_order_id = str(payload.get("work_order_id") or "")
                disposition = str(payload.get("disposition") or "")
        except ValueError:
            pass
        entry[key] = {
            "approval_id": row.approval_id,
            "approved": bool(row.approved),
            "approved_by": row.approved_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "work_order_id": work_order_id,
            "disposition": disposition,
        }
    return states


# ========== 质量待办队列（阶段九：跨工单 MRB 待办视角） ==========

_TODO_ISSUE_STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED")


def _reported_days_since(reported_at: str, now: datetime) -> int | None:
    """按 reported_at 计算已报告天数；缺失或无法解析时如实返回 None。"""
    value = (reported_at or "").strip()
    if not value:
        return None
    try:
        reported = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if reported.tzinfo is None:
        reported = reported.replace(tzinfo=timezone.utc)
    return max(0, int((now - reported).total_seconds() // 86400))


async def quality_todo_list() -> dict[str, Any]:
    """跨工单未关闭质量问题队列（只读，真实 OpenMES）。

    OPEN/ACKNOWLEDGED/RESOLVED 三态聚合（CLOSED 不进待办）。任何一态
    读取失败直接抛出，不回退空列表冒充"没有待办"。
    """
    mes = get_mes_adapter()
    items = await mes.list_open_issues(statuses=_TODO_ISSUE_STATUSES)
    now = datetime.now(timezone.utc)
    for item in items:
        item["reported_days"] = _reported_days_since(str(item.get("reported_at", "")), now)
    return {
        "items": items,
        "authority": "OpenMES",
        "data_source": "openmes_issues",
    }


# ========== 发运门禁 ==========

@_agent_run("shipping", "gate_check")
async def ship_gate_check(
    work_order_id: str,
    quotation_approved: bool = False,
) -> dict[str, Any]:
    """发运门禁：综合质量状态和审批状态，判断是否允许发运。

    只有同时满足：
    1. 报价/订单已审批
    2. 质量门禁通过
    才允许进入发运判断。
    """
    quality = await quality_package(work_order_id)
    tracking = await track_order(work_order_id)

    quality_passed = quality.get("quality_gate_passed", False)

    # 生产进度判断（至少完成 90% 才能考虑发运）
    completion_rate = tracking.get("completion_rate", 0)
    production_ready = completion_rate >= 90.0

    can_ship = quotation_approved and quality_passed and production_ready

    blocking_reasons = []
    if not quotation_approved:
        blocking_reasons.append("报价/订单未审批")
    if not quality_passed:
        blocking_reasons.append("质量门禁未通过")
    if not production_ready:
        blocking_reasons.append(f"生产进度不足 ({completion_rate}% < 90%)")

    return {
        "work_order_id": work_order_id,
        "work_order_no": tracking.get("work_order_no", ""),
        "can_ship": can_ship,
        "blocking_reasons": blocking_reasons,
        "quotation_approved": quotation_approved,
        "quality_gate_passed": quality_passed,
        "production_ready": production_ready,
        "completion_rate": completion_rate,
        "quality_gate_details": quality.get("gate_details", {}),
        "tracking_summary": {
            "status": tracking.get("status", ""),
            "quantity": tracking.get("quantity", ""),
            "completed_qty": tracking.get("completed_qty", ""),
            "risks": tracking.get("risks", []),
        },
        "authority": "OpenMES",
        "checked_at": _utc_now().isoformat(),
    }


# ========== 方案化协同：成本/交期影响评估（阶段六，全部真实记录） ==========


@_agent_run("quotation", "assess_cost_impact")
async def assess_cost_impact(plan_id: str, option_id: str) -> dict[str, Any]:
    """成本影响评估（报价 Agent）：缺料换供应商方案对订单收入与毛利的影响。

    全部使用真实记录：
    - 订单收入：报价 total_price（真实 ERP Selling 价格）
    - 基准材料成本：采购方案 net_requirement.total_estimated_cost（真实 Buying 价格记录）
    - 方案材料成本：所选供应商方案 total_cost（真实供应商特定价/标准价）
    口径限制（如实标注）：仅材料成本口径；本系统无真实工时/制费数据，
    不做全成本毛利结论。数据缺失时返回 DATA_MISSING，不编造数字。
    """
    plan = get_procurement_plan(plan_id)
    if not plan:
        return {"status": "ERROR", "error": f"采购方案 {plan_id} 不存在"}
    option = next(
        (o for o in plan.get("supplier_options", []) if o.get("option_id") == option_id),
        None,
    )
    if option is None:
        return {
            "status": "ERROR",
            "error": f"方案 {plan_id} 中不存在供应商选项 {option_id}",
            "available_options": [o.get("option_id") for o in plan.get("supplier_options", [])],
        }

    missing: list[dict[str, str]] = []
    quotation = get_quotation(plan.get("quotation_id", ""))
    if not quotation or not quotation.get("total_price"):
        missing.append({
            "field": "quotation_total_price",
            "detail": f"报价 {plan.get('quotation_id', '')} 不存在或无真实成交总价，无法计算利润影响",
        })
    baseline_cost = plan.get("net_requirement", {}).get("total_estimated_cost", "")
    if not baseline_cost:
        missing.append({
            "field": "baseline_material_cost",
            "detail": "缺料物料存在无真实 Buying 价格记录的项，基准材料成本不完整（见方案 data_limitations）",
        })
    option_cost = option.get("total_cost", "")
    if not option.get("total_cost_complete") or not option_cost:
        missing.append({
            "field": "option_material_cost",
            "detail": f"供应商方案 {option_id} 存在无真实价格记录的物料，方案总价不完整",
        })

    if missing:
        return {
            "status": "DATA_MISSING",
            "plan_id": plan_id,
            "option_id": option_id,
            "missing_fields": missing,
            "need": "补录缺失物料的真实 Buying 价格（Item Price）后重新评估",
        }

    try:
        revenue = Decimal(str(quotation["total_price"]))
        baseline = Decimal(str(baseline_cost))
        option_total = Decimal(str(option_cost))
        quantity = int(quotation.get("quantity") or 0)
    except Exception:
        return {"status": "ERROR", "error": "成本数据格式异常，无法计算"}

    delta = option_total - baseline
    margin_before = revenue - baseline
    margin_after = margin_before - delta
    per_unit_surcharge = (delta / Decimal(quantity)).quantize(Decimal("0.01")) if quantity > 0 else None
    rate_before = (margin_before / revenue * 100).quantize(Decimal("0.1")) if revenue > 0 else None
    rate_after = (margin_after / revenue * 100).quantize(Decimal("0.1")) if revenue > 0 else None

    evidence = [
        {
            "source": "ERPNext",
            "record_type": "quotation",
            "record_id": quotation.get("quotation_id", ""),
            "summary": f"报价总收入 {revenue} {quotation.get('currency', 'CNY')}（真实 Selling 价格，数量 {quantity}）",
        },
        {
            "source": "ERPNext",
            "record_type": "material_cost_baseline",
            "record_id": plan.get("plan_id", ""),
            "summary": f"基准材料成本（真实 Buying 价格记录）{baseline}",
        },
    ]
    for item in option.get("items", []):
        if item.get("price_record"):
            evidence.append({
                "source": "ERPNext",
                "record_type": "item_price",
                "record_id": item["price_record"],
                "summary": f"{item['item_id']} 供应商特定价 {item['unit_price']}（{option['supplier_name']}）",
            })

    return {
        "status": "ok",
        "plan_id": plan_id,
        "option_id": option_id,
        "supplier_id": option.get("supplier_id", ""),
        "supplier_name": option.get("supplier_name", ""),
        "covers_all_shortage_items": option.get("covers_all_shortage_items"),
        "revenue": str(revenue),
        "currency": option.get("currency", "CNY"),
        "material_cost_baseline": str(baseline),
        "material_cost_with_option": str(option_total),
        "material_cost_delta": str(delta.quantize(Decimal("0.01"))),
        "per_unit_surcharge": str(per_unit_surcharge) if per_unit_surcharge is not None else "",
        "material_margin_before": str(margin_before.quantize(Decimal("0.01"))),
        "material_margin_after": str(margin_after.quantize(Decimal("0.01"))),
        "material_margin_rate_before_percent": str(rate_before) if rate_before is not None else "",
        "material_margin_rate_after_percent": str(rate_after) if rate_after is not None else "",
        "calculation_basis": "仅材料成本口径（无真实工时/制费数据，不做全成本毛利结论）；价格为真实 Item Price 记录",
        "evidence": evidence,
        "authority": "ERPNext",
        "data_source": "erpnext_api",
    }


@_agent_run("tracking", "assess_delivery_impact")
async def assess_delivery_impact(work_order_id: str, material_ready_date: str) -> dict[str, Any]:
    """交期影响评估（跟单 Agent）：物料到货时间 vs 工单交期。

    material_ready_date 由采购方案 lead_time 推算（今天 + lead_time_days），
    必须是 YYYY-MM-DD。诚实边界：MES 无生产速率/工序排程数据时，无法估算
    剩余产量的完工日期，本评估仅覆盖"物料到货是否晚于交期"这一维度，
    不伪造ETA。
    """
    tracking = await track_order(work_order_id)
    if tracking.get("status") == "ERROR" or tracking.get("data_source") != "openmes_api":
        return {"status": "ERROR", "error": f"无法读取工单 {work_order_id} 的真实 MES 数据"}

    due_date = (tracking.get("due_date") or "").strip()
    if not due_date:
        return {
            "status": "DATA_MISSING",
            "work_order_id": work_order_id,
            "missing_fields": [{"field": "work_order_due_date", "detail": "工单无交期字段，无法做交期影响判断"}],
        }

    def _parse_date(value: str):
        value = value.strip()
        if not value:
            return None
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    due = _parse_date(due_date)
    ready = _parse_date(material_ready_date)
    if due is None or ready is None:
        return {
            "status": "DATA_MISSING",
            "work_order_id": work_order_id,
            "missing_fields": [{
                "field": "material_ready_date",
                "detail": f"material_ready_date 必须是 YYYY-MM-DD（当前值 {material_ready_date!r}）；可由采购方案 lead_time_days 推算",
            }],
        }

    buffer_days = (due - ready).days
    completion_rate = float(tracking.get("completion_rate") or 0)
    quantity = str(tracking.get("quantity", ""))
    completed_qty = str(tracking.get("completed_qty", ""))

    if completion_rate >= 100:
        verdict = "not_needed"
        conclusion = "工单已完工，不再依赖新物料到货"
    elif buffer_days >= 0:
        verdict = "arrival_in_time"
        conclusion = f"物料于交期前 {buffer_days} 天到位，物料维度不构成延期"
    else:
        verdict = "arrival_after_due"
        conclusion = f"物料晚于工单交期 {-buffer_days} 天到位，该方案下订单至少延期 {-buffer_days} 天"

    return {
        "status": "ok",
        "work_order_id": work_order_id,
        "work_order_no": tracking.get("work_order_no", ""),
        "due_date": due.isoformat(),
        "material_ready_date": ready.isoformat(),
        "buffer_days": buffer_days,
        "verdict": verdict,
        "conclusion": conclusion,
        "completion_rate": completion_rate,
        "remaining_qty": str((Decimal(quantity) - Decimal(completed_qty)) if quantity and completed_qty else ""),
        "assessment_scope": "仅物料到货维度；剩余产量完工时间需真实排程/速率数据（当前 MES 数据限制，不伪造 ETA）",
        "evidence": [
            {
                "source": "OpenMES",
                "record_type": "work_order",
                "record_id": tracking.get("work_order_no", work_order_id),
                "summary": f"工单计划 {quantity}，已完工 {completed_qty}（{completion_rate}%），交期 {due.isoformat()}",
            },
            {
                "source": "ERPNext",
                "record_type": "item_lead_time",
                "record_id": "Item.lead_time_days",
                "summary": f"material_ready_date {ready.isoformat()} 由采购方案 lead_time_days 推算（物料级交期）",
            },
        ],
        "authority": "OpenMES + ERPNext",
        "data_source": "openmes_api + erpnext_api",
    }


@_agent_run("quotation", "find_by_erp_order")
async def find_quotation_by_erp_order(erp_order_id: str) -> dict[str, Any]:
    """按 ERP 销售订单号查找关联报价。

    匹配两种真实关联（均非推断）：
    - erp_draft_id：8 步流程为该订单创建草稿时写入
    - source_erp_order_id：问答链报价分析时调用方明确声明的订单上下文
    """
    erp_order_id = (erp_order_id or "").strip()
    if not erp_order_id:
        return {"found": False, "error": "erp_order_id 不能为空"}
    matches = [
        q for q in list_quotations()
        if erp_order_id in {
            (q.get("erp_draft_id") or "").strip(),
            (q.get("source_erp_order_id") or "").strip(),
        }
    ]
    if not matches:
        return {
            "found": False,
            "erp_order_id": erp_order_id,
            "note": "该 ERP 订单没有已保存的真实报价；若需要缺料/成本分析，先在真实业务页面对该订单物料执行报价分析",
        }
    latest = max(matches, key=lambda q: q.get("created_at") or "")
    plan_summary = await find_latest_plan_by_quotation(latest.get("quotation_id", ""))
    return {
        "found": True,
        "quotation_id": latest.get("quotation_id", ""),
        "status": latest.get("status", ""),
        "item_code": (latest.get("item") or {}).get("item_id", ""),
        "quantity": latest.get("quantity"),
        "unit_price": latest.get("unit_price", ""),
        "total_price": latest.get("total_price", ""),
        "currency": latest.get("currency", "CNY"),
        "erp_draft_id": latest.get("erp_draft_id", ""),
        "source_erp_order_id": latest.get("source_erp_order_id", ""),
        "approval_id": latest.get("approval_id"),
        "created_at": latest.get("created_at", ""),
        "matched_count": len(matches),
        "latest_procurement_plan": plan_summary,
        "authority": "local_persisted(报价数据源 ERPNext 真实记录)",
    }


@_agent_run("procurement", "find_by_quotation")
async def find_latest_plan_by_quotation(quotation_id: str) -> dict[str, Any]:
    """按报价编号查找最新采购方案（问答状态联动：报价 → 方案审批/PO 草稿状态）。"""
    quotation_id = (quotation_id or "").strip()
    if not quotation_id:
        return {"found": False, "error": "quotation_id 不能为空"}
    matches = [
        p for p in list_procurement_plans()
        if (p.get("quotation_id") or "").strip() == quotation_id
    ]
    if not matches:
        return {
            "found": False,
            "quotation_id": quotation_id,
            "note": "该报价还没有采购方案；缺料分析后会生成 PROC- 编号的方案记录",
        }
    latest = max(matches, key=lambda p: p.get("created_at") or "")
    return {
        "found": True,
        "plan_id": latest.get("plan_id", ""),
        "status": latest.get("status", ""),
        "selected_option_id": latest.get("selected_option_id"),
        "approval_id": latest.get("approval_id"),
        "po_draft_id": latest.get("po_draft_id"),
        "has_shortage": (latest.get("net_requirement") or {}).get("has_shortage"),
        "created_at": latest.get("created_at", ""),
        "matched_count": len(matches),
        "authority": "local_persisted(方案数据源 ERPNext 真实记录)",
    }


@_agent_run("procurement", "assess_combination")
async def assess_combination(plan_id: str, option_ids: list[str]) -> dict[str, Any]:
    """分单采购组合的确定性评估（采购 Agent）。

    组合多个供应商方案的确定性计算，防止 LLM 自行拼数：
    - 组合成本 = 各选项 total_cost 之和（真实价格记录合计，不做任何折扣/系数假设）
    - 覆盖 = 各选项覆盖缺料物料的并集；同一物料被多个选项重复覆盖时
      明确告警（组合成本含重复采购，需人工剔除）
    - 交期 = 组合中最长 lead_time_days（物料级交期，如实说明非供应商级）
    任一选项价格不完整时返回 DATA_MISSING，不给出可决策的组合总价。
    """
    plan = get_procurement_plan(plan_id)
    if not plan:
        return {"status": "ERROR", "error": f"采购方案 {plan_id} 不存在"}
    ids = [str(o).strip() for o in (option_ids or []) if str(o).strip()]
    if not ids:
        return {"status": "ERROR", "error": "option_ids 不能为空（至少选择一个供应商选项）"}

    options_by_id = {o.get("option_id"): o for o in plan.get("supplier_options", [])}
    missing_options = [i for i in ids if i not in options_by_id]
    if missing_options:
        return {
            "status": "ERROR",
            "error": f"方案 {plan_id} 中不存在选项 {missing_options}",
            "available_options": list(options_by_id.keys()),
        }
    selected = [options_by_id[i] for i in ids]

    unpriced = [o["option_id"] for o in selected if not o.get("total_cost_complete") or not o.get("total_cost")]
    if unpriced:
        return {
            "status": "DATA_MISSING",
            "plan_id": plan_id,
            "option_ids": ids,
            "missing_fields": [{
                "field": "option_total_cost",
                "detail": f"选项 {unpriced} 存在无真实价格记录的物料，组合总价不完整，不能用于决策",
            }],
            "need": "为缺失物料补录真实 Buying 价格（Item Price）后重新评估",
        }

    shortage_items = plan.get("net_requirement", {}).get("shortage_items", []) or []
    shortage_ids = [si["item_id"] for si in shortage_items]

    # 逐物料统计被几个选中选项覆盖：>1 = 重复采购（组合成本含重复金额，需人工剔除）
    coverage_count: dict[str, int] = {}
    for option in selected:
        for item in option.get("items", []):
            coverage_count[item["item_id"]] = coverage_count.get(item["item_id"], 0) + 1
    overlapping = sorted(item for item, count in coverage_count.items() if count > 1)
    covered = sorted(item for item, count in coverage_count.items() if count > 0)
    uncovered = [i for i in shortage_ids if i not in covered]

    combined_cost = sum((Decimal(o["total_cost"]) for o in selected), Decimal("0"))
    lead_times = [int(o["lead_time_days"]) for o in selected if o.get("lead_time_days") is not None]
    max_lead = max(lead_times) if lead_times else None

    evidence = []
    for option in selected:
        for item in option.get("items", []):
            if item.get("price_record"):
                evidence.append({
                    "source": "ERPNext",
                    "record_type": "item_price",
                    "record_id": item["price_record"],
                    "summary": f"{item['item_id']} {option['supplier_name']} 单价 {item['unit_price']}（行小计 {item.get('line_total', '')}）",
                })

    warnings = []
    if overlapping:
        warnings.append(
            f"物料 {overlapping} 被多个选中选项重复覆盖：组合成本含重复采购金额，下单前需人工从选项行项目中剔除重复物料"
        )
    if uncovered:
        warnings.append(f"缺料 {uncovered} 在组合中仍无覆盖，组合不构成完整采购方案")

    return {
        "status": "ok",
        "plan_id": plan_id,
        "option_ids": ids,
        "suppliers": [f"{o['supplier_name']}({o['option_id']})" for o in selected],
        "combined_cost": str(combined_cost.quantize(Decimal("0.01"))),
        "currency": selected[0].get("currency", "CNY"),
        "coverage": {
            "covered_items": covered,
            "uncovered_items": uncovered,
            "shortage_total": len(shortage_ids),
            "complete": not uncovered,
        },
        "overlapping_items": overlapping,
        "max_lead_time_days": max_lead,
        "lead_time_basis": "各选项物料级 Item.lead_time_days 的最大值（非供应商级）",
        "per_option": [
            {
                "option_id": o["option_id"],
                "supplier_name": o["supplier_name"],
                "total_cost": o["total_cost"],
                "lead_time_days": o.get("lead_time_days"),
                "coverage": o.get("coverage", ""),
            }
            for o in selected
        ],
        "warnings": warnings,
        "evidence": evidence,
        "calculation_basis": "确定性计算：组合成本=各选项真实价格记录合计（无折扣/系数假设）；覆盖=并集；交期=最长物料级交期",
        "authority": "ERPNext",
        "data_source": "erpnext_api",
    }


@_agent_run("quality", "assess_impact")
async def assess_quality_impact(work_order_id: str) -> dict[str, Any]:
    """质量异常影响分析（质量文档 Agent，讨论稿例子三）。

    确定性交叉分析真实数据：
    - 质量问题清单（状态/严重度，真实 OpenMES 记录）
    - 批次关联（工单生产批次，追溯维度）
    - 质量门禁状态与原因
    - 生产进度与交期（发运影响）
    数据缺口如实列出（检验记录当前 0 条、SN 维度 MES 无 API），不编造。
    处理选项只指向真实可用通道（resolve 审批写回 / 8 步流程），不承诺
    NCR close/disposition 的可执行通道已接入，但本技能仍保持只读，
    只报告数据缺口和人工审批入口，不自行改变质量状态。
    """
    mes = get_mes_adapter()
    quality = await quality_package(work_order_id)
    tracking = await track_order(work_order_id)

    records = quality.get("quality_records", []) or []
    open_issues = quality.get("open_issues_count", 0)
    gate_passed = quality.get("quality_gate_passed", False)
    gate_reasons = (quality.get("gate_details") or {}).get("missing_documents", [])
    completion_rate = float(tracking.get("completion_rate") or 0)

    # 批次关联（读取失败不伪造为空——记录为数据缺口）
    batches: list[dict[str, Any]] = []
    batch_error = ""
    try:
        batches = await mes.get_work_order_batches(work_order_id)
    except Exception as exc:
        batch_error = f"{type(exc).__name__}: {str(exc)[:200]}"

    impact_conclusions: list[str] = []
    if open_issues > 0:
        impact_conclusions.append(
            f"存在 {open_issues} 项未关闭质量问题，发运被质量门禁阻断；需先解决质量问题（可走已接入的问题 resolve 人工审批通道）"
        )
    if not gate_passed:
        missing_docs = quality.get("missing_documents", []) or []
        if missing_docs:
            impact_conclusions.append(
                f"工程文档缺失（{('、'.join(missing_docs))}），质量门禁未通过；需补录并发布对应文档"
            )
    if completion_rate < 90:
        impact_conclusions.append(
            f"生产完成率 {completion_rate}%（<90%），发运门禁的生产条件未满足"
        )
    if not impact_conclusions:
        impact_conclusions.append("当前无未关闭质量问题、文档齐备且生产完成，质量维度不构成发运阻断")

    data_gaps: list[dict[str, str]] = []
    if not quality.get("inspections"):
        data_gaps.append({
            "field": "inspections",
            "detail": "该工单暂无检验记录（IQC/IPQC 数据未补录），检验维度无法参与影响分析",
        })
    data_gaps.append({
        "field": "sn_traceability",
        "detail": "OpenMES 无 SN 级追溯 API，本分析仅到批次（lot）维度",
    })
    if batch_error:
        data_gaps.append({"field": "batches", "detail": f"批次读取失败：{batch_error}"})
    data_gaps.append({
        "field": "ncr_full_disposition",
        "detail": "本只读影响汇总不自动执行 NCR 写回；可通过 closure-check 校验 disposition、纠正措施与关闭前置条件，写回仍需人工审批",
    })

    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    open_records = [r for r in records if str(r.get("status", "")).upper() not in ("RESOLVED", "CLOSED")]
    open_records.sort(key=lambda r: severity_rank.get(str(r.get("severity", "")).upper(), 9))

    evidence = [
        {
            "source": "OpenMES",
            "record_type": "quality_issues",
            "record_id": ",".join(str(r.get("record_id", "")) for r in records) or "none",
            "summary": f"质量问题 {len(records)} 条（未关闭 {open_issues}）",
        },
        {
            "source": "OpenMES",
            "record_type": "work_order",
            "record_id": tracking.get("work_order_no", work_order_id),
            "summary": f"完成率 {completion_rate}%（{tracking.get('completed_qty', '')}/{tracking.get('quantity', '')}），交期 {tracking.get('due_date', '')}",
        },
    ]

    return {
        "status": "ok",
        "work_order_id": work_order_id,
        "work_order_no": tracking.get("work_order_no", ""),
        "quality_records": [
            {
                k: r.get(k) for k in (
                    "record_id", "title", "severity", "status", "record_type",
                    "work_order_no", "reported_at",
                )
            }
            for r in records
        ],
        "open_issues_count": open_issues,
        "open_records": [
            {k: r.get(k) for k in ("record_id", "title", "severity", "status")}
            for r in open_records
        ],
        "batches": batches,
        "quality_gate_passed": gate_passed,
        "missing_documents": quality.get("missing_documents", []),
        "production": {
            "completion_rate": completion_rate,
            "status": tracking.get("status", ""),
            "due_date": tracking.get("due_date", ""),
        },
        "impact_conclusions": impact_conclusions,
        "handling_options": [
            {
                "option": "解决质量问题",
                "how": "对未关闭问题走 resolve 人工审批写回通道（服务端令牌+审批记录），或人工在 OpenMES 处理",
                "requires_human_confirmation": True,
            },
            {
                "option": "完成 NCR 处置并关闭",
                "how": "先提交 disposition（根因/遏制措施必填），再将所有纠正措施推进到 VERIFIED，最后走 close 审批；每一步均回读 OpenMES",
                "requires_human_confirmation": True,
            },
            {
                "option": "补齐工程文档",
                "how": "上传并发布缺失的 SOP/Control Plan（工单冻结快照在创建时继承已发布文档）",
                "requires_human_confirmation": True,
            },
            {
                "option": "完成生产",
                "how": "按官方批次工序接口报工至 ≥90%（当前工单如有工艺模板与批次）",
                "requires_human_confirmation": True,
            },
        ],
        "data_gaps": data_gaps,
        "evidence": evidence,
        "authority": "OpenMES",
        "data_source": "openmes_api",
    }


@_agent_run("tracking", "find_by_no")
async def find_work_order_by_no(work_order_no: str) -> dict[str, Any]:
    """按 MES 工单编号（如 WO-2026-001）精确查找工单，返回数字 work_order_id。

    用户自然语言中说的是工单编号，而 MES 查询工具使用数字 id；
    本技能做精确编号匹配（不推断），未命中时如实返回并给出可选编号建议。
    """
    work_order_no = (work_order_no or "").strip()
    if not work_order_no:
        return {"found": False, "error": "work_order_no 不能为空"}
    mes = get_mes_adapter()
    work_orders = await mes.get_work_orders_strict({"limit": 100})
    matches = [w for w in work_orders if (w.get("work_order_no") or "").strip() == work_order_no]
    if not matches:
        suggestions = [
            {"work_order_no": w.get("work_order_no"), "customer_order_no": w.get("customer_order_no") or ""}
            for w in work_orders[:10]
        ]
        return {
            "found": False,
            "work_order_no": work_order_no,
            "note": "MES 中不存在该工单编号",
            "existing_work_orders": suggestions,
        }
    w = matches[0]
    return {
        "found": True,
        "work_order_id": str(w.get("work_order_id", "")),
        "work_order_no": w.get("work_order_no", ""),
        "customer_order_no": w.get("customer_order_no") or "",
        "product_id": w.get("product_id", ""),
        "quantity": str(w.get("quantity", "")),
        "status": w.get("status", ""),
        "authority": w.get("authority", "OpenMES"),
        "data_source": w.get("data_source", "openmes_api"),
    }


# ========== 报价存储（数据库持久化，重启不丢失） ==========


def save_quotation(quotation: dict[str, Any]) -> dict[str, Any]:
    """保存报价分析结果（写入数据库）。"""
    qid = quotation.get("quotation_id")
    if not qid:
        qid = _gen_id("QUO")
        quotation["quotation_id"] = qid
    data = _to_jsonable(quotation)
    with SessionLocal() as session:
        row = session.get(RealQuotationRow, qid)
        if row is None:
            row = RealQuotationRow(
                quotation_id=qid,
                status=str(data.get("status", "")),
                adapter_mode=str(data.get("adapter_mode", "")),
                customer_id=str(data.get("customer", {}).get("customer_id", "") or ""),
                item_code=str(data.get("item", {}).get("item_id", "") or ""),
                quantity=int(data.get("quantity", 0) or 0),
                data_json=data,
            )
            session.add(row)
        else:
            row.status = str(data.get("status", ""))
            row.data_json = data
        session.commit()
    return quotation


def get_quotation(quotation_id: str) -> dict[str, Any] | None:
    """获取报价分析结果（读数据库，返回副本）。"""
    with SessionLocal() as session:
        row = session.get(RealQuotationRow, quotation_id)
        if row is None:
            return None
        return copy.deepcopy(row.data_json)


def list_quotations() -> list[dict[str, Any]]:
    """列出所有报价（读数据库）。"""
    with SessionLocal() as session:
        rows = session.query(RealQuotationRow).order_by(RealQuotationRow.created_at).all()
        return [copy.deepcopy(r.data_json) for r in rows]


# ========== 报价审批 ==========

@_agent_run("approval", "approve_quotation")
async def approve_quotation(
    quotation_id: str,
    approved: bool,
    approved_by: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """审批报价。

    审批通过后，生成审批编号，可用于创建 ERP 草稿。
    """
    quotation = get_quotation(quotation_id)
    if not quotation:
        return {
            "success": False,
            "error": f"报价不存在: {quotation_id}",
        }

    # 幂等：同一报价已经完成相同审批时直接回读原审批记录，避免重复产生审批号。
    existing_approval_id = quotation.get("approval_id")
    if existing_approval_id and quotation.get("status") == ("APPROVED" if approved else "REJECTED"):
        existing = get_approval(str(existing_approval_id))
        if existing:
            return {
                "success": True,
                "approval_id": str(existing_approval_id),
                "approved": bool(existing.get("approved")),
                "approved_by": existing.get("approved_by", ""),
                "quotation_id": quotation_id,
                "notes": existing.get("notes"),
                "created_at": existing.get("created_at"),
                "idempotent_replay": True,
            }

    approval_id = _gen_id("APPR")
    record = _register_approval(
        approval_id=approval_id,
        approved=approved,
        approved_by=approved_by,
        reference_type="quotation",
        reference_id=quotation_id,
        notes=notes,
    )

    # 更新报价状态
    quotation["status"] = "APPROVED" if approved else "REJECTED"
    quotation["approval_id"] = approval_id
    quotation["approved_by"] = approved_by
    quotation["approved_at"] = _utc_now().isoformat()
    save_quotation(quotation)

    return {
        "success": True,
        "approval_id": approval_id,
        "approved": approved,
        "approved_by": approved_by,
        "quotation_id": quotation_id,
        "notes": notes,
        "created_at": record["created_at"],
    }


# ========== ERP 销售订单草稿创建 ==========

@_agent_run("erp_write", "create_so_draft")
async def create_erp_sales_order_from_quotation(
    quotation_id: str,
    approval_id: str,
    approved_by: str,
) -> dict[str, Any]:
    """根据已审批的报价，在 ERP 中创建销售订单草稿。

    完整流程：报价分析 → 审批通过 → 创建 ERP 草稿 → 回读确认
    """
    # 1. 验证报价存在
    quotation = get_quotation(quotation_id)
    if not quotation:
        return {
            "success": False,
            "error": f"报价不存在: {quotation_id}",
            "authority": "local",
        }

    # 2. 验证审批
    approval = get_approval(approval_id)
    if not approval:
        return {
            "success": False,
            "error": f"审批记录不存在: {approval_id}",
            "authority": "local",
        }
    if not approval.get("approved"):
        return {
            "success": False,
            "error": "审批未通过，不能创建 ERP 草稿",
            "authority": "local",
        }
    if approval.get("reference_id") != quotation_id:
        return {
            "success": False,
            "error": "审批编号与报价不匹配",
            "authority": "local",
        }

    # 3. 校验报价数据完整性（缺价格不允许创建草稿，不伪造金额）
    if not quotation.get("unit_price") or Decimal(str(quotation.get("unit_price", "0"))) <= 0:
        return {
            "success": False,
            "error": "报价缺少真实价格（DATA_MISSING），不能创建 ERP 销售订单草稿",
            "missing_data": quotation.get("missing_data", []),
            "authority": "local",
        }

    # 4. 构造销售订单草稿（仓库/公司来自真实 ERP 记录）
    erp = get_erp_adapter()
    customer = quotation.get("customer", {})
    item = quotation.get("item", {})

    company = await erp.get_default_company()
    company_name = company.get("company_name", "") if company.get("found") else ""
    if not company_name:
        return {
            "success": False,
            "error": "ERP 中没有真实 Company 记录，无法创建草稿",
            "authority": "local",
        }

    warehouse = await _pick_warehouse(erp, "Stores")
    if not warehouse:
        return {
            "success": False,
            "error": "ERP 中没有可用的 Stores 类型仓库记录，无法创建草稿",
            "authority": "local",
        }

    # 交期必须来自报价输入，缺失时显式拒绝（不用"今天"默认值冒充交期）
    delivery_date = quotation.get("delivery_date")
    if not delivery_date:
        return {
            "success": False,
            "error": "报价缺少交期（delivery_date），不能创建 ERP 销售订单草稿——请补充客户要求交期后重新分析报价",
            "authority": "local",
        }

    draft = {
        "customer_id": customer.get("customer_id", ""),
        "company": company_name,
        "currency": quotation.get("currency", "CNY"),
        "delivery_date": delivery_date,
        "items": [
            {
                "item_code": item.get("item_id", item.get("item_code", "")),
                "qty": quotation.get("quantity", 1),
                "rate": float(quotation.get("unit_price", 0)),
                "uom": item.get("stock_uom", "Nos"),
                "delivery_date": delivery_date,
                "warehouse": warehouse,
            },
        ],
        "approval_id": approval_id,
        "approved_by": approved_by,
    }

    # 5. 创建草稿（适配器内部会调用审批校验器）
    result = await erp.create_sales_order_draft(draft)

    # 6. 如果创建成功，回读确认
    if result.get("draft_id") and result.get("status") == "DRAFT":
        try:
            read_back = await erp.read_back({
                "doctype": "Sales Order",
                "id": result["draft_id"],
            })
            result["read_back"] = read_back
            result["read_back_verified"] = bool(read_back)
        except Exception as e:
            result["read_back_error"] = str(e)
            result["read_back_verified"] = False

    result["company_source"] = "erp_company_record"
    result["warehouse_source"] = "erp_warehouse_record"

    # 7. 更新报价记录
    quotation["erp_draft"] = result
    quotation["erp_draft_id"] = result.get("draft_id", "")
    save_quotation(quotation)

    return {
        "success": result.get("status") == "DRAFT",
        "quotation_id": quotation_id,
        "approval_id": approval_id,
        "draft": result,
        "authority": result.get("authority", "ERPNext"),
    }


async def _pick_warehouse(erp, warehouse_type: str) -> str:
    """从真实 Warehouse 记录中选择仓库。

    选择顺序（基于真实记录，不做硬编码）：
    1. warehouse_type 字段精确匹配（如 "Stores"）
    2. 若 ERP 未配置 warehouse_type（当前真实数据多为 None），
       按仓库命名约定匹配（名称以类型关键词开头）
    都匹配不到时返回空字符串，由调用方明确报错。
    """
    warehouses = await erp.list_warehouses()
    for w in warehouses:
        if (w.get("warehouse_type") or "") == warehouse_type:
            return w.get("warehouse", "")
    for w in warehouses:
        if (w.get("warehouse") or "").startswith(warehouse_type):
            return w.get("warehouse", "")
    return ""


# ==================== 采购 Agent：物料需求与采购方案 ====================


def save_procurement_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """保存采购方案（写入数据库）。"""
    data = _to_jsonable(plan)
    with SessionLocal() as session:
        row = session.get(RealProcurementPlanRow, plan["plan_id"])
        if row is None:
            row = RealProcurementPlanRow(
                plan_id=plan["plan_id"],
                quotation_id=str(data.get("quotation_id", "")),
                status=str(data.get("status", "")),
                adapter_mode=str(data.get("adapter_mode", "")),
                data_json=data,
            )
            session.add(row)
        else:
            row.status = str(data.get("status", ""))
            row.data_json = data
        session.commit()
    return plan


def get_procurement_plan(plan_id: str) -> dict[str, Any] | None:
    """获取采购方案（读数据库，返回副本）。"""
    with SessionLocal() as session:
        row = session.get(RealProcurementPlanRow, plan_id)
        if row is None:
            return None
        return copy.deepcopy(row.data_json)


def list_procurement_plans() -> list[dict[str, Any]]:
    """列出所有采购方案（读数据库）。"""
    with SessionLocal() as session:
        rows = session.query(RealProcurementPlanRow).order_by(RealProcurementPlanRow.created_at).all()
        return [copy.deepcopy(r.data_json) for r in rows]


async def compute_net_requirement(
    item_code: str,
    quantity: int,
) -> dict[str, Any]:
    """计算净物料需求：基于 BOM 展开 + 库存扣减。"""
    erp = get_erp_adapter()

    # 1. 读取 BOM
    bom = await erp.get_bom(item_code)

    # 2. 收集所有需要的物料（BOM 子项）
    bom_items = bom.get("items", []) if bom.get("found") else []
    all_item_codes = [bi["item_code"] for bi in bom_items] + [item_code]

    # 3. 读取库存
    inventory = await erp.get_inventory(all_item_codes)

    # 4. 计算每个子项的毛需求和净需求
    net_requirements = []
    missing_data: list[dict[str, str]] = []
    for bom_item in bom_items:
        item_id = bom_item["item_code"]
        qty_per = Decimal(str(bom_item["qty_per_product"]))
        gross_req = qty_per * Decimal(quantity)

        # 汇总该物料所有仓库的实际库存
        total_stock = Decimal("0")
        for inv in inventory:
            if inv["item_id"] == item_id:
                total_stock += Decimal(str(inv["actual_qty"]))

        net_req = max(Decimal("0"), gross_req - total_stock)
        stock_sufficient = net_req == 0

        # 读取物料详情
        try:
            item_detail = await erp.get_item(item_id)
        except Exception:
            item_detail = {"item_id": item_id, "item_name": item_id, "stock_uom": "Nos"}

        # 读取采购价格：只用真实 Buying 价格记录，不再使用 0.7 系数或固定占位金额
        unit_price: Decimal | None = None
        price_id = ""
        price_status = "missing"
        currency = "CNY"
        try:
            prices = await erp.get_prices(item_id, _utc_now().strftime("%Y-%m-%d"))
            buying_prices = [p for p in prices if "Buying" in p.get("price_list", "")]
            if buying_prices:
                unit_price = Decimal(str(buying_prices[0]["unit_price"]))
                price_id = buying_prices[0]["price_id"]
                price_status = "real_buying_price"
                currency = buying_prices[0].get("currency", "CNY")
        except Exception:
            pass
        if unit_price is None:
            missing_data.append({
                "field": f"buying_price:{item_id}",
                "detail": "物料缺少真实 Buying 价格记录（Item Price），采购金额无法计算",
            })

        moq = Decimal(str(item_detail.get("min_order_qty", "0") or "0"))
        # 采购数量应用最小采购量约束（真实 Item.min_order_qty）
        order_qty = net_req
        qty_basis = "net_requirement"
        if not stock_sufficient and moq > 0 and net_req < moq:
            order_qty = moq
            qty_basis = "min_order_qty"
        lead_time = item_detail.get("lead_time_days")
        net_requirements.append({
            "item_id": item_id,
            "item_name": item_detail.get("item_name", item_id),
            "uom": item_detail.get("purchase_uom") or item_detail.get("stock_uom", "Nos"),
            "gross_requirement": str(gross_req),
            "available_stock": str(total_stock),
            "net_requirement": str(net_req),
            "order_qty": str(order_qty),
            "qty_basis": qty_basis,
            "stock_sufficient": stock_sufficient,
            "unit_price": str(unit_price) if unit_price is not None else "",
            "unit_price_record": price_id,
            "price_status": price_status,
            "currency": currency,
            "min_order_qty": str(moq) if moq > 0 else "",
            "min_order_qty_configured": moq > 0,
            "lead_time_days": lead_time if lead_time else None,
            "suppliers": [s.get("supplier") for s in item_detail.get("suppliers", [])],
            "bom_qty_per": str(qty_per),
            "authority": item_detail.get("authority", "ERPNext"),
        })

    # 5. 检查是否有缺料
    shortage_items = [nr for nr in net_requirements if not nr["stock_sufficient"]]
    has_shortage = len(shortage_items) > 0

    priced = [nr for nr in net_requirements if nr["price_status"] == "real_buying_price"]
    total_cost = sum(
        (Decimal(nr["order_qty"]) * Decimal(nr["unit_price"]) for nr in priced),
        Decimal("0"),
    ) if priced else Decimal("0")
    all_priced = len(priced) == len(net_requirements) and len(net_requirements) > 0

    return {
        "finished_item": item_code,
        "production_quantity": quantity,
        "bom_found": bom.get("found", False),
        "bom_items_count": len(bom_items),
        "net_requirements": net_requirements,
        "shortage_count": len(shortage_items),
        "shortage_items": shortage_items,
        "has_shortage": has_shortage,
        "missing_data": missing_data,
        "total_estimated_cost": str(total_cost.quantize(Decimal("0.01"))) if all_priced else "",
        "total_estimated_cost_complete": all_priced,
        "authority": "ERPNext",
        "inventory": inventory,
        "bom": bom,
    }


@_agent_run("procurement", "analyze")
async def analyze_procurement(
    quotation_id: str,
) -> dict[str, Any]:
    """采购 Agent：基于报价单生成采购方案（真实供应商数据）。

    真实化说明（占位逻辑已删除）：
    - 单价：只用真实 Buying Item Price 记录（含记录编号），缺失时明确标注，
      不再使用 0.7 系数、固定 50 元占位或供应商固定加价系数
    - 交期：ERP 无真实交期数据（Item Supplier 无记录、Item.lead_time_days=0）
      时如实标注 missing，不再使用 7/10/13 天模拟
    - 推荐：确定性规则 lowest_total_cost_v1（总价最低者推荐），
      规则版本显式标注；价格数据不完整时不给出推荐
    """
    erp = get_erp_adapter()
    mode = get_adapter_mode()

    # 1. 获取报价单
    quotation = get_quotation(quotation_id)
    if not quotation:
        return {"success": False, "error": f"报价单 {quotation_id} 不存在"}

    item_code = quotation["item"]["item_id"]
    quantity = quotation["quantity"]

    # 2. 计算净物料需求
    net_req = await compute_net_requirement(item_code, quantity)

    # 3. 如果没有缺料，直接返回
    if not net_req["has_shortage"]:
        plan_id = _gen_id("PROC")
        evidence = [
            {
                "source": net_req.get("authority", "unknown"),
                "record_type": "bom",
                "record_id": net_req.get("bom", {}).get("bom_id", "") if net_req.get("bom") else "",
                "summary": f"BOM: {net_req.get('bom_items_count', 0)} 个子项",
            },
            {
                "source": net_req.get("authority", "unknown"),
                "record_type": "inventory",
                "record_id": f"{len(net_req.get('inventory', []))} 条记录",
                "summary": f"库存: {len(net_req.get('inventory', []))} 条仓位记录，覆盖全部物料需求",
            },
        ]
        plan = {
            "plan_id": plan_id,
            "quotation_id": quotation_id,
            "quotation_status": quotation.get("status", ""),
            "status": "NO_SHORTAGE",
            "adapter_mode": mode,
            "net_requirement": net_req,
            "supplier_options": [],
            "recommended_option_id": None,
            "recommendation_rule": "not_applicable_no_shortage",
            "recommendation": "库存充足，无需采购",
            "data_limitations": [],
            "evidence": evidence,
            "selected_option_id": None,
            "approval_id": None,
            "po_draft": None,
            "created_at": _utc_now().isoformat(),
        }
        save_procurement_plan(plan)
        return plan

    # 4. 真实供应商列表 + 按物料-供应商关系（Item Supplier）过滤
    all_suppliers = await erp.search_suppliers("", 20)
    today = _utc_now().strftime("%Y-%m-%d")

    # 5. 为每个真实供应商生成方案：
    #    只包含该供应商实际供应的缺料物料（来自 Item.supplier_items 真实关系）；
    #    单价优先用供应商特定 Item Price，其次标准 Buying 价，缺失时如实标注；
    #    交期用 Item.lead_time_days（物料级真实数据）。
    supplier_options = []
    for idx, supplier in enumerate(all_suppliers):
        supplier_id = supplier.get("supplier_id", "")
        covered = [si for si in net_req["shortage_items"] if supplier_id in (si.get("suppliers") or [])]
        if not covered:
            continue  # 该供应商不供应任何缺料物料，不生成空方案

        item_breakdown = []
        total_cost = Decimal("0")
        all_priced = True
        option_currency = "CNY"
        lead_times = []
        for si in covered:
            net_qty = Decimal(si["order_qty"])
            # 供应商特定价格（真实 Item Price.supplier 记录）
            unit_price = None
            price_record = ""
            price_basis = "missing"
            try:
                supplier_prices = await erp.get_prices(si["item_id"], today, supplier=supplier_id)
                supplier_buying = [p for p in supplier_prices if "Buying" in p.get("price_list", "")]
                if supplier_buying:
                    unit_price = Decimal(str(supplier_buying[0]["unit_price"]))
                    price_record = supplier_buying[0]["price_id"]
                    price_basis = "supplier_specific_price"
                    option_currency = supplier_buying[0].get("currency", "CNY")
            except Exception:
                pass
            if unit_price is None and si["price_status"] == "real_buying_price":
                # 回退标准价（真实记录，如实标注 basis）
                unit_price = Decimal(si["unit_price"])
                price_record = si["unit_price_record"]
                price_basis = "standard_buying_price_fallback"
                option_currency = si.get("currency", "CNY")
            if unit_price is None:
                all_priced = False
            else:
                total_cost += net_qty * unit_price
            if si.get("lead_time_days"):
                lead_times.append(int(si["lead_time_days"]))
            item_breakdown.append({
                "item_id": si["item_id"],
                "item_name": si["item_name"],
                "quantity": str(net_qty),
                "qty_basis": si.get("qty_basis", "net_requirement"),
                "uom": si["uom"],
                "unit_price": str(unit_price) if unit_price is not None else "",
                "price_basis": price_basis,
                "price_record": price_record,
                "line_total": str((net_qty * unit_price).quantize(Decimal("0.01"))) if unit_price is not None else "",
                "authority": si["authority"],
            })

        covers_all = len(covered) == len(net_req["shortage_items"])
        supplier_options.append({
            "option_id": f"OPT-{idx + 1}",
            "supplier_id": supplier_id,
            "supplier_name": supplier["supplier_name"],
            "supplier_group": supplier.get("supplier_group", ""),
            "lead_time_days": max(lead_times) if lead_times else None,
            "lead_time_source": "erp_item_lead_time_days" if lead_times else "missing",
            "covers_all_shortage_items": covers_all,
            "coverage": f"{len(covered)}/{len(net_req['shortage_items'])}",
            "total_cost": str(total_cost.quantize(Decimal("0.01"))) if all_priced else "",
            "total_cost_complete": all_priced,
            "currency": option_currency,
            "items": item_breakdown,
            "is_recommended": False,
            "recommendation_reason": "",
            "supplier_authority": supplier.get("authority", "ERPNext"),
        })

    uncovered = [si["item_id"] for si in net_req["shortage_items"]
                 if not any(si["item_id"] in [it["item_id"] for it in o["items"]] for o in supplier_options)]

    # 数据可用性说明（当前 ERP 现状，如实告知）
    data_limitations = [
        {
            "field": "supplier_item_relation",
            "detail": "供应商关系来自 Item.supplier_items 真实记录；未供应任何缺料物料的供应商不生成方案"
                      + (f"；缺料 {uncovered} 无任何供应商覆盖，需人工处理" if uncovered else ""),
        },
        {
            "field": "supplier_lead_time",
            "detail": "本 ERP 版本 Item Supplier 子表无 lead_time_days 字段，交期为物料级（Item.lead_time_days），非供应商级",
        },
    ]

    # 6. 推荐规则 lowest_total_cost_v2：优先覆盖全部缺料的方案，再取总价最低
    full_coverage = [o for o in supplier_options if o["covers_all_shortage_items"] and o["total_cost_complete"]]
    partial_priced = [o for o in supplier_options if not o["covers_all_shortage_items"] and o["total_cost_complete"]]
    pool = full_coverage if full_coverage else (partial_priced if partial_priced else [])
    recommended = min(pool, key=lambda o: Decimal(o["total_cost"])) if pool else None
    if recommended:
        recommended["is_recommended"] = True
        scope_desc = "覆盖全部缺料" if recommended["covers_all_shortage_items"] else "覆盖部分缺料（无单一供应商可全覆盖）"
        recommended["recommendation_reason"] = (
            f"规则 lowest_total_cost_v2：{scope_desc}，总价 {recommended['total_cost']} "
            f"{recommended['currency']} 为可比方案中最低（价格来源：真实 Item Price 记录，交期来源：Item.lead_time_days）"
        )
        for o in supplier_options:
            if o["option_id"] != recommended["option_id"] and o["total_cost_complete"]:
                o["recommendation_reason"] = (
                    f"规则 lowest_total_cost_v2：总价 {o['total_cost']} {o['currency']}"
                    + ("、覆盖全部缺料" if o["covers_all_shortage_items"] else f"、仅覆盖 {o['coverage']} 项缺料")
                    + "，未获推荐"
                )
            elif not o["total_cost_complete"]:
                o["recommendation_reason"] = "价格数据不完整，无法比较（缺真实价格记录）"

    shortage_desc = "、".join(f"{si['item_id']}(缺 {si['net_requirement']})" for si in net_req["shortage_items"])
    recommendation = (
        f"检测到 {net_req['shortage_count']} 项缺料（{shortage_desc}）"
        + (f"；按规则 lowest_total_cost_v2 推荐 {recommended['option_id']}（{recommended['supplier_name']}，覆盖 {recommended['coverage']}）"
           if recommended else "；无价格完整的供应商方案，需人工处理")
        + (f"；注意：缺料 {uncovered} 无供应商覆盖" if uncovered else "")
    )

    evidence = [
        {
            "source": net_req.get("authority", "unknown"),
            "record_type": "bom",
            "record_id": net_req["bom"].get("bom_id", "") if net_req.get("bom") else "",
            "summary": f"BOM: {net_req['bom_items_count']} 个子项",
        },
        {
            "source": net_req.get("authority", "unknown"),
            "record_type": "inventory",
            "record_id": f"{len(net_req.get('inventory', []))} 条记录",
            "summary": f"库存: {len(net_req.get('inventory', []))} 条仓位记录",
        },
        {
            "source": "ERPNext",
            "record_type": "supplier_search",
            "record_id": f"{len(all_suppliers)} 家供应商",
            "summary": f"匹配 {len(all_suppliers)} 家供应商（ERP 无物料-供应商关系记录）",
        },
    ]
    for si in net_req["shortage_items"]:
        if si["price_status"] == "real_buying_price":
            evidence.append({
                "source": "ERPNext",
                "record_type": "item_price",
                "record_id": si["unit_price_record"],
                "summary": f"{si['item_id']} 采购单价 {si['unit_price']} {si.get('currency', 'CNY')}（真实 Buying 价格记录）",
            })

    plan_id = _gen_id("PROC")
    plan = {
        "plan_id": plan_id,
        "quotation_id": quotation_id,
        "quotation_status": quotation.get("status", ""),
        "status": "PENDING_APPROVAL",
        "adapter_mode": mode,
        "net_requirement": net_req,
        "supplier_options": supplier_options,
        "recommended_option_id": recommended["option_id"] if recommended else None,
        "recommendation_rule": "lowest_total_cost_v2",
        "recommendation": recommendation,
        "data_limitations": data_limitations,
        "evidence": evidence,
        "selected_option_id": None,
        "approval_id": None,
        "po_draft": None,
        "created_at": _utc_now().isoformat(),
    }
    save_procurement_plan(plan)
    return plan


@_agent_run("approval", "approve_procurement")
async def approve_procurement_plan(
    plan_id: str,
    option_id: str,
    approved: bool,
    approved_by: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """审批采购方案：选择供应商方案并批准/驳回。"""
    plan = get_procurement_plan(plan_id)
    if not plan:
        return {"success": False, "error": f"采购方案 {plan_id} 不存在"}

    # 幂等：已批准/已起草的方案重复点击时回读原审批，不生成第二笔审批记录。
    if plan.get("status") in {"APPROVED", "PO_DRAFT_CREATED"} and plan.get("approval_id"):
        if plan.get("selected_option_id") != option_id:
            return {
                "success": False,
                "error": f"采购方案已批准并选择 {plan.get('selected_option_id')}，不能改选 {option_id}",
            }
        existing = get_approval(str(plan["approval_id"]))
        if existing:
            return {
                "success": True,
                "status": "APPROVED",
                "plan_id": plan_id,
                "approval_id": str(plan["approval_id"]),
                "selected_option_id": plan.get("selected_option_id"),
                "idempotent_replay": True,
            }

    if not approved:
        plan["status"] = "REJECTED"
        plan["rejection_notes"] = notes or ""
        plan["rejected_by"] = approved_by
        plan["rejected_at"] = _utc_now().isoformat()
        save_procurement_plan(plan)
        return {"success": True, "status": "REJECTED", "plan_id": plan_id}

    # 批准：记录选中的供应商方案
    approval_id = _gen_id("APR")
    _register_approval(
        approval_id=approval_id,
        approved=True,
        approved_by=approved_by,
        reference_type="procurement_plan",
        reference_id=plan_id,
        notes=notes or "",
    )

    plan["selected_option_id"] = option_id
    plan["approval_id"] = approval_id
    plan["approved_by"] = approved_by
    plan["approved_at"] = _utc_now().isoformat()
    plan["status"] = "APPROVED"
    plan["approval_notes"] = notes or ""
    save_procurement_plan(plan)

    return {
        "success": True,
        "status": "APPROVED",
        "plan_id": plan_id,
        "approval_id": approval_id,
        "selected_option_id": option_id,
    }


@_agent_run("erp_write", "create_po_draft")
async def create_erp_purchase_order_from_plan(
    plan_id: str,
    approval_id: str,
    approved_by: str,
) -> dict[str, Any]:
    """根据已审批采购方案创建 ERP 采购订单草稿。"""
    # 1. 获取采购方案
    plan = get_procurement_plan(plan_id)
    if not plan:
        return {"success": False, "error": f"采购方案 {plan_id} 不存在"}

    # 草稿已经创建时只回读原单据，防止重复点击在 ERP 产生第二张草稿。
    existing_draft = plan.get("po_draft")
    if plan.get("po_draft_id") and isinstance(existing_draft, dict):
        return {
            "success": existing_draft.get("status") == "DRAFT",
            "plan_id": plan_id,
            "approval_id": plan.get("approval_id") or approval_id,
            "selected_option_id": plan.get("selected_option_id"),
            "draft": existing_draft,
            "authority": existing_draft.get("authority", "ERPNext"),
            "idempotent_replay": True,
        }

    if plan["status"] != "APPROVED":
        return {"success": False, "error": "采购方案尚未批准，不能创建采购订单"}

    erp = get_erp_adapter()

    # 2. 验证审批
    approval = get_approval(approval_id)
    if not approval:
        return {"success": False, "error": f"审批记录不存在: {approval_id}"}
    if not approval.get("approved"):
        return {"success": False, "error": "审批未通过，不能创建采购订单草稿"}

    # 3. 找到选中的供应商方案
    selected_option = None
    for opt in plan.get("supplier_options", []):
        if opt["option_id"] == plan.get("selected_option_id"):
            selected_option = opt
            break

    if not selected_option:
        return {"success": False, "error": "未找到选中的供应商方案"}

    # 4. 组装采购订单草稿（公司/仓库来自真实 ERP 记录；交期缺失时如实标注）
    erp = get_erp_adapter()
    today = _utc_now().strftime("%Y-%m-%d")

    company = await erp.get_default_company()
    company_name = company.get("company_name", "") if company.get("found") else ""
    if not company_name:
        return {"success": False, "error": "ERP 中没有真实 Company 记录，无法创建采购草稿"}

    warehouse = await _pick_warehouse(erp, "Stores")
    if not warehouse:
        return {"success": False, "error": "ERP 中没有可用的 Stores 类型仓库记录，无法创建采购草稿"}

    lead_time_days = selected_option.get("lead_time_days")
    lead_time_source = selected_option.get("lead_time_source", "")
    if lead_time_days:
        from datetime import timedelta
        schedule_date = (_utc_now() + timedelta(days=int(lead_time_days))).strftime("%Y-%m-%d")
        delivery_note = f"交期来源：ERP Item.lead_time_days（真实数据，{lead_time_days} 天）"
    else:
        schedule_date = today
        delivery_note = "交期缺失：ERP 未配置该物料交期（Item.lead_time_days=0），schedule_date 暂填今日，需人工确认后调整"

    unpriced = [it for it in selected_option["items"] if not it.get("unit_price")]
    if unpriced:
        return {
            "success": False,
            "error": f"选中方案存在缺少真实价格的物料（{', '.join(it['item_id'] for it in unpriced)}），不能创建采购草稿",
        }

    draft = {
        "supplier": selected_option["supplier_id"],
        "company": company_name,
        "transaction_date": today,
        "schedule_date": schedule_date,
        "items": [
            {
                "item_code": item["item_id"],
                "item_name": item["item_name"],
                "qty": float(item["quantity"]),
                "rate": float(item["unit_price"]),
                "uom": item["uom"],
                "schedule_date": schedule_date,
                "warehouse": warehouse,
            }
            for item in selected_option["items"]
        ],
        "approval_id": approval_id,
        "approved_by": approved_by,
    }

    # 5. 创建草稿
    result = await erp.create_purchase_order_draft(draft)

    # 6. 回读确认
    if result.get("draft_id") and result.get("status") == "DRAFT":
        try:
            read_back = await erp.read_back({
                "doctype": "Purchase Order",
                "id": result["draft_id"],
            })
            result["read_back"] = read_back
            result["read_back_verified"] = bool(read_back)
        except Exception as e:
            result["read_back_error"] = str(e)
            result["read_back_verified"] = False

    result["company_source"] = "erp_company_record"
    result["warehouse_source"] = "erp_warehouse_record"
    result["delivery_note"] = delivery_note

    # 7. 更新采购方案
    plan["po_draft"] = result
    plan["po_draft_id"] = result.get("draft_id", "")
    plan["status"] = "PO_DRAFT_CREATED"
    save_procurement_plan(plan)

    return {
        "success": result.get("status") == "DRAFT",
        "plan_id": plan_id,
        "approval_id": approval_id,
        "selected_option_id": plan.get("selected_option_id"),
        "draft": result,
        "authority": result.get("authority", "ERPNext"),
    }
