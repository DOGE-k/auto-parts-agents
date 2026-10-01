"""协同问答的确定性上下文层：信号提取、意图槽位、合并与追问。

设计原则（用户确认的业务规则，2026-10-01）：

1. 上一轮已确认的客户、物料、订单号、工单号、数量和交期默认沿用，并在
   回答中回显；用户输入的新值优先；上下文有歧义时先询问，不猜测。
2. "数量改成 3000"默认表示沿用当前客户、物料和交期，重新执行一次只读
   报价分析，不自动写入 ERP，也不自动审批；已有报价审批、采购方案或
   ERP 草稿必须标记为需要重新确认。
3. "选第二个方案"按当前回答 supplier_options 的展示顺序解释（从 1 开
   始）；执行必须保存稳定 option_id 与方案快照；方案顺序变化、不存在或
   已过期时先提示重新选择；执行仍走人工审批门禁。
4. 缺少必填信息时先追问，不得猜测；不把自然语言中的编号误当数据库 ID。

本模块全部为确定性规则（正则 + 显式映射），不调用大模型，可独立测试。
中文数字序号只支持"第一…第十"的方案选择场景。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# 编号模式（全部来自项目已验证的真实编号格式，不做猜测）
_ERP_ORDER_RE = re.compile(r"\bSAL-ORD-\d{4}-\d{4,6}\b")
_PO_ORDER_RE = re.compile(r"\bPUR-ORD-\d{4}-\d{4,6}\b")
_WORK_ORDER_RE = re.compile(r"\b(?:TEST_)?WO[-_][A-Za-z0-9\-_]*\d[A-Za-z0-9\-_]*\b")
_QUOTATION_RE = re.compile(r"\bQUO-[0-9A-F]{8,16}\b")
_PLAN_RE = re.compile(r"\bPROC-[0-9A-F]{8,16}\b")
_ITEM_RE = re.compile(r"\b[A-Z]{2}-\d{3,5}\b")
_QUANTITY_CHANGE_RE = re.compile(
    r"(?:数量|Qty|qty)\s*(?:改成|改为|变更为|调整为|换成)\s*([\d,]+)"
    r"|(?:改成|改为|变更为|调整为|换成)\s*([\d,]+)\s*件"
)
_STANDALONE_QTY_RE = re.compile(r"([\d,]+)\s*(?:件|个|只|套|pcs|PCS)")
_ORDINAL_RE = re.compile(r"第\s*([一二三四五六七八九十\d]{1,3})\s*(?:个)?(?:采购)?方案")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "quotation": ("报价", "价格", "多少钱", "单价", "成本", "报个价", "费用"),
    "procurement": ("采购", "缺料", "净需求", "供应商", "下采购单", "物料方案"),
    "tracking": ("进度", "什么时候能做完", "何时能做完", "交期", "发运", "能发货", "发货", "延期", "eta", "加急", "提前"),
    "quality": ("质量", "质量问题", "ncr", "检验", "sop", "control plan", "处置", "质量异常", "不合格"),
}

# 各意图完成一次真实查询所必需的槽位（AI_HANDOFF_PLAN P0-2）
REQUIRED_SLOTS: dict[str, dict[str, str]] = {
    "quotation": {
        "customer_id": "报价必须挂在真实 ERP 客户下",
        "item_code": "报价必须基于真实 ERP 物料价格与 BOM",
        "quantity": "价格与缺料计算依赖数量",
    },
    "procurement": {
        "quotation_id|erp_order_id": "采购分析需要报价或销售订单作为需求来源",
    },
    "tracking": {
        "erp_order_id|work_order_no|work_order_id": "跟单进度需要 ERP 订单号或 MES 工单号",
    },
    "quality": {
        "work_order_no|work_order_id": "质量资料包按 MES 工单归集",
    },
}

# 实体上下文中允许携带的字段（白名单：绝不持久化凭据/令牌/密码）
CONTEXT_FIELDS = (
    "customer_id",
    "customer_name",
    "item_code",
    "quantity",
    "delivery_date",
    "erp_order_id",
    "erp_draft_id",
    "purchase_order_id",
    "work_order_id",
    "work_order_no",
    "quotation_id",
    "plan_id",
    "selected_option_id",
)


def assistant_retention_days() -> int | None:
    """会话原始数据保留天数：ASSISTANT_RETENTION_DAYS，默认 90；0=永久。"""
    raw = os.getenv("ASSISTANT_RETENTION_DAYS", "90").strip()
    if raw in {"", "0"}:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return 90


def retention_expiry(now: datetime | None = None) -> datetime | None:
    days = assistant_retention_days()
    if days is None:
        return None
    now = now or datetime.now(timezone.utc)
    return now + timedelta(days=days)


@dataclass
class Signals:
    """从一条用户消息中确定性提取的新值/指令。"""

    erp_order_id: str = ""
    purchase_order_id: str = ""
    work_order_no: str = ""
    quotation_id: str = ""
    plan_id: str = ""
    item_code: str = ""
    quantity_change: int | None = None
    quantity_mentioned: int | None = None
    ordinal_selection: int | None = None
    delivery_date: str = ""
    intents: list[str] = field(default_factory=list)

    def has_any_entity(self) -> bool:
        return bool(
            self.erp_order_id
            or self.purchase_order_id
            or self.work_order_no
            or self.quotation_id
            or self.plan_id
            or self.item_code
        )


def _first(match: re.Match[str], text: str) -> str:
    return match.group(0).strip() if match else ""


def _parse_cn_number(raw: str) -> int:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return _CN_NUM.get(raw, 0)


def extract_signals(text: str) -> Signals:
    """从用户消息做确定性提取；歧义（同字段多个不同编号）时全部保留由上层追问。"""
    signals = Signals()

    orders = _ERP_ORDER_RE.findall(text)
    if orders:
        unique = sorted(set(orders))
        if len(unique) == 1:
            signals.erp_order_id = unique[0]

    pos = _PO_ORDER_RE.findall(text)
    if len(set(pos)) == 1:
        signals.purchase_order_id = pos[0]

    wos = [wo for wo in _WORK_ORDER_RE.findall(text) if not wo.startswith("WO-2026-")]
    if wos:
        unique = sorted(set(wos))
        if len(unique) == 1:
            signals.work_order_no = unique[0]

    quos = _QUOTATION_RE.findall(text)
    if len(set(quos)) == 1:
        signals.quotation_id = quos[0]

    plans = _PLAN_RE.findall(text)
    if len(set(plans)) == 1:
        signals.plan_id = plans[0]

    items = _ITEM_RE.findall(text)
    if len(set(items)) == 1:
        signals.item_code = items[0]

    change = _QUANTITY_CHANGE_RE.search(text)
    if change:
        raw = (change.group(1) or change.group(2) or "").replace(",", "")
        if raw.isdigit():
            signals.quantity_change = int(raw)
    if signals.quantity_change is None:
        standalone = _STANDALONE_QTY_RE.search(text)
        if standalone:
            raw = standalone.group(1).replace(",", "")
            if raw.isdigit():
                signals.quantity_mentioned = int(raw)

    ordinal = _ORDINAL_RE.search(text)
    if ordinal:
        value = _parse_cn_number(ordinal.group(1))
        if 1 <= value <= 10:
            signals.ordinal_selection = value

    delivery = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if delivery:
        signals.delivery_date = delivery.group(1)

    lower = text.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            signals.intents.append(intent)
    return signals


def classify_primary_intent(signals: Signals) -> str:
    """主意图：quotation > procurement > tracking > quality（缺料追问通常伴随价格）。"""
    for intent in ("quotation", "procurement", "tracking", "quality"):
        if intent in signals.intents:
            return intent
    return ""


def merge_context(
    context: dict[str, Any], signals: Signals
) -> tuple[dict[str, Any], list[str]]:
    """新值覆盖旧值，返回 (合并后的上下文, 更新字段列表)。

    只接受 CONTEXT_FIELDS 白名单内的字段；歧义编号（提取时已滤掉多值）
    不会进入，缺失歧义时由上层按"沿用+回显+允许纠正"处理。
    """
    merged = {k: v for k, v in (context or {}).items() if k in CONTEXT_FIELDS}
    updates: list[str] = []
    new_values: dict[str, Any] = {}
    if signals.erp_order_id:
        new_values["erp_order_id"] = signals.erp_order_id
    if signals.purchase_order_id:
        new_values["purchase_order_id"] = signals.purchase_order_id
    if signals.work_order_no:
        new_values["work_order_no"] = signals.work_order_no
    if signals.quotation_id:
        new_values["quotation_id"] = signals.quotation_id
    if signals.plan_id:
        new_values["plan_id"] = signals.plan_id
    if signals.item_code:
        new_values["item_code"] = signals.item_code
    if signals.quantity_change is not None:
        new_values["quantity"] = signals.quantity_change
    elif signals.quantity_mentioned is not None:
        new_values["quantity"] = signals.quantity_mentioned
    if signals.delivery_date:
        new_values["delivery_date"] = signals.delivery_date

    for key, value in new_values.items():
        if merged.get(key) != value:
            merged[key] = value
            updates.append(key)
    return merged, updates


def format_context_echo(context: dict[str, Any]) -> str:
    """生成"当前沿用…"回显文案；字段显示名用业务语言。"""
    labels = {
        "customer_id": "客户",
        "customer_name": "客户",
        "item_code": "物料",
        "quantity": "数量",
        "delivery_date": "交期",
        "erp_order_id": "订单",
        "erp_draft_id": "ERP 草稿",
        "purchase_order_id": "采购订单",
        "work_order_no": "工单",
        "work_order_id": "工单ID",
        "quotation_id": "报价",
        "plan_id": "采购方案",
        "selected_option_id": "已选方案",
    }
    parts = []
    for key in CONTEXT_FIELDS:
        value = (context or {}).get(key)
        if value in (None, "", []):
            continue
        parts.append(f"{labels.get(key, key)}={value}")
    if not parts:
        return ""
    return "当前沿用" + "、".join(parts) + "；如果需要修改请直接说明。"


def missing_slots(intent: str, context: dict[str, Any]) -> list[dict[str, str]]:
    """按意图检查必填槽位；返回缺失项及为什么需要/补充后会调用哪个智能体。"""
    agent_by_intent = {
        "quotation": "报价智能体（quotation.analyze_real）",
        "procurement": "采购智能体（procurement.analyze_real）",
        "tracking": "跟单智能体（tracking.lookup_order_link / track_real）",
        "quality": "质量文档智能体（quality.get_real_package）",
    }
    spec = REQUIRED_SLOTS.get(intent)
    if not spec:
        return []
    missing: list[dict[str, str]] = []
    for slot_keys, why in spec.items():
        satisfied = any((context or {}).get(k) for k in slot_keys.split("|"))
        if satisfied:
            continue
        first_key = slot_keys.split("|")[0]
        missing.append({
            "slot": first_key,
            "alternatives": slot_keys.split("|"),
            "why": why,
            "agent": agent_by_intent.get(intent, ""),
        })
    return missing


def format_clarify_answer(intent: str, missing: list[dict[str, str]], echo: str) -> str:
    label = {
        "quotation": "报价",
        "procurement": "采购缺料",
        "tracking": "生产进度/交期",
        "quality": "质量",
    }.get(intent, "这个问题")
    lines = [f"要回答{label}问题，我还缺少以下信息（不猜测，先向你确认）："]
    for i, item in enumerate(missing, start=1):
        slot_label = {
            "customer_id": "客户（哪家客户）",
            "item_code": "物料（哪个物料编码）",
            "quantity": "数量（多少件）",
            "quotation_id": "报价编号（QUO-…）",
            "erp_order_id": "销售订单号（SAL-ORD-…）",
            "work_order_no": "MES 工单编号（WO-…）",
            "work_order_id": "工单编号或数字 ID",
        }.get(item["slot"], item["slot"])
        lines.append(f"{i}. {slot_label} —— {item['why']}；补充后我会调用{item['agent']}查真实数据。")
    if echo:
        lines.append(echo)
    return "\n".join(lines)


def validate_ordinal_selection(
    ordinal: int,
    active_plan: dict[str, Any] | None,
    current_plan: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """校验"选第 N 个方案"：返回 (选中的选项快照, 错误提示)。

    规则：序号按 active_plan.supplier_options 展示顺序（1 开始）；方案不
    存在/已过期/顺序变化时要求重新选择。
    """
    if not active_plan or not (active_plan.get("supplier_options") or []):
        return None, (
            "当前会话没有可选择的供应商方案。请先问一次缺料方案"
            "（例如「SAL-ORD-2026-00023 缺料了怎么办」），再选择第几个方案。"
        )
    options = active_plan["supplier_options"]
    if ordinal < 1 or ordinal > len(options):
        return None, (
            f"序号超出范围：当前方案共有 {len(options)} 个选项（第 1~{len(options)} 个），"
            "请重新说明要选哪一个。"
        )
    chosen = options[ordinal - 1]
    plan_id = active_plan.get("plan_id", "")
    if current_plan is None:
        return None, (
            f"方案 {plan_id} 已不存在（可能已被重新分析覆盖）。为避免选到过期方案，"
            "请重新发起一次缺料方案提问，再选择第几个方案。"
        )
    current_options = [
        {
            "option_id": opt.get("option_id", ""),
            "supplier_name": opt.get("supplier_name", ""),
            "total_cost": opt.get("total_cost"),
            "currency": opt.get("currency", ""),
            # 快照完整性（2026-10-01 接手缺口②）：交期与覆盖变化同样要求
            # 重新选择——字段均来自现有 proposal supplier_options 契约。
            "lead_time_days": opt.get("lead_time_days"),
            "lead_time_source": opt.get("lead_time_source", ""),
            "coverage": opt.get("coverage"),
        }
        for opt in (current_plan.get("supplier_options") or [])
    ]
    snapshot_options = [
        {
            "option_id": opt.get("option_id", ""),
            "supplier_name": opt.get("supplier_name", ""),
            "total_cost": opt.get("total_cost"),
            "currency": opt.get("currency", ""),
            "lead_time_days": opt.get("lead_time_days"),
            "lead_time_source": opt.get("lead_time_source", ""),
            "coverage": opt.get("coverage"),
        }
        for opt in options
    ]
    if current_options != snapshot_options:
        return None, (
            f"方案 {plan_id} 的供应商选项与选择时快照不一致（顺序或内容已变化）。"
            "为避免误解，请基于最新方案重新选择。"
        )
    return chosen, None
