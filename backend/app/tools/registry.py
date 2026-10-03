from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from app.domain.models import AgentType
from app.tools.demo_rules import (
    RULE_VERSION,
    calculate_demo_eta,
    calculate_demo_net_requirement,
    calculate_demo_quote_cost,
    check_demo_ship_gate,
)
from app.services.llm_quotation import extract_rfq_deterministic, compute_complexity_factor, compute_quantity_discount
from app.runtime.identifiers import canonical_hash, stable_id


def _cost_deltas(inputs: dict[str, Any]) -> dict[str, Any]:
    shortage = Decimal(inputs["shortage_quantity"])
    baseline = Decimal(inputs["baseline_unit_price"])
    return {
        "cost_assessment_id": inputs["cost_assessment_id"],
        "parent_order_id": inputs["parent_order_id"],
        "accepted_quote_id": inputs["accepted_quote_id"],
        "accepted_quote_status": inputs["accepted_quote_status"],
        "option_deltas": [
            {
                "option_id": option["option_id"],
                "supplier_id": option["supplier_id"],
                "delta_for_shortage": str(((Decimal(option["unit_price"]) - baseline) * shortage).quantize(Decimal("0.01"))),
            }
            for option in inputs["options"]
        ],
        "note": "独立订单期评估，不改变已接受报价。",
    }


def _complete_check(inputs: dict[str, Any]) -> dict[str, Any]:
    required = set(inputs["required"])
    collected = set(inputs["evidence"])
    return {"complete": required <= collected, "missing": sorted(required - collected)}


def _passthrough(inputs: dict[str, Any]) -> dict[str, Any]:
    return dict(inputs)


def _build_checklist(inputs: dict[str, Any]) -> dict[str, Any]:
    template = inputs.get("template", "FINAL_INSPECTION")
    product_id = inputs.get("product_id", "unknown")
    items = [
        {"doc_type": "SOP", "required": True, "category": "production"},
        {"doc_type": "Control Plan", "required": True, "category": "quality"},
        {"doc_type": "PFMEA", "required": True, "category": "quality"},
        {"doc_type": "IQC Report", "required": True, "category": "incoming"},
        {"doc_type": "IPQC Report", "required": True, "category": "in_process"},
        {"doc_type": "FQC Report", "required": True, "category": "final"},
        {"doc_type": "Material Certificate", "required": False, "category": "material"},
        {"doc_type": "Drawing", "required": True, "category": "technical"},
    ]
    return {
        "checklist_id": f"CL-{product_id}-{template}",
        "template": template,
        "product_id": product_id,
        "items": items,
        "total_required": sum(1 for i in items if i["required"]),
    }


def _collect_evidence(inputs: dict[str, Any]) -> dict[str, Any]:
    available = inputs.get("available_documents", [])
    checklist = inputs.get("checklist", {})
    collected = []
    for doc in available:
        collected.append({
            "doc_type": doc.get("doc_type", ""),
            "doc_id": doc.get("doc_id", ""),
            "status": doc.get("status", "UNKNOWN"),
            "evidence_id": f"ev-doc-{doc.get('doc_id', 'unknown')}",
        })
    return {
        "collected_count": len(collected),
        "evidence_items": collected,
        "evidence_ids": [item["evidence_id"] for item in collected],
        "checklist_id": checklist.get("checklist_id", ""),
    }


def _detect_risk(inputs: dict[str, Any]) -> dict[str, Any]:
    quality_hold = inputs.get("quality_hold", False)
    rejected_qty = int(inputs.get("rejected_qty", 0))
    total = int(inputs.get("total_quantity", 100))
    current_completed = int(inputs.get("current_completed", 0))

    risk_factors = []
    risk_level = "low"

    if quality_hold:
        risk_factors.append("质量冻结")
        risk_level = "medium"

    if rejected_qty > 0:
        rejection_rate = rejected_qty / max(total, 1)
        risk_factors.append(f"不良率 {rejection_rate:.1%}")
        if rejection_rate > 0.05:
            risk_level = "high"
        elif rejection_rate > 0.02:
            risk_level = max(risk_level, "medium") if risk_level != "high" else "high"

    if current_completed < total * 0.5:
        risk_factors.append("进度不足 50%")
        risk_level = max(risk_level, "medium") if risk_level != "high" else "high"

    eta_delay_days = 0
    if quality_hold:
        eta_delay_days = 3

    return {
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "eta_delay_days": eta_delay_days,
        "impact": f"交付可能延迟 {eta_delay_days} 天" if eta_delay_days > 0 else "当前交付可控",
        "work_order_id": inputs.get("work_order_id", ""),
        "quality_hold": quality_hold,
    }


def _analyze_rfq(inputs: dict[str, Any]) -> dict[str, Any]:
    """分析 RFQ 描述，提取结构化信息并给出报价建议。"""
    description = inputs.get("description", "")
    extracted = extract_rfq_deterministic(description)

    # 计算报价建议调整因子
    complexity_factor = compute_complexity_factor(extracted["complexity"])
    quantity_factor = compute_quantity_discount(extracted.get("quantity"))

    # 综合调整因子
    price_adjustment_factor = complexity_factor * quantity_factor

    result = {
        **extracted,
        "complexity_factor": complexity_factor,
        "quantity_discount_factor": quantity_factor,
        "price_adjustment_factor": round(price_adjustment_factor, 4),
        "pricing_suggestion": _build_pricing_suggestion(extracted, price_adjustment_factor),
    }
    return result


def _build_pricing_suggestion(extracted: dict[str, Any], adjustment_factor: float) -> str:
    """生成报价建议文本。"""
    notes = []
    if extracted.get("quantity"):
        notes.append(f"订单量 {extracted['quantity']} 件")
    if extracted.get("material_spec"):
        notes.append(f"材料：{extracted['material_spec']}")
    if extracted.get("complexity") == "high":
        notes.append("工艺复杂度高，建议上浮报价")
    elif extracted.get("complexity") == "low":
        notes.append("工艺简单，可考虑竞争力定价")
    if extracted.get("delivery_days") and extracted["delivery_days"] <= 15:
        notes.append("交期紧张，需考虑加急成本")
    if extracted.get("tolerance_grade"):
        notes.append(f"公差要求 {extracted['tolerance_grade']}")

    if not notes:
        return "标准报价流程，按基础成本核算。"
    return "；".join(notes) + "。"


class ToolRegistry:
    """Closed registry: handlers are explicitly installed and agent scoped."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "quotation.calculate_cost": calculate_demo_quote_cost,
            "quotation.create_draft": _passthrough,
            "quotation.create_cost_assessment": _cost_deltas,
            "quotation.analyze_rfq": _analyze_rfq,
            "procurement.calculate_net_requirement": calculate_demo_net_requirement,
            "procurement.compare_supply_plans": _passthrough,
            "procurement.create_po_draft": _passthrough,
            "tracking.calculate_eta": calculate_demo_eta,
            "tracking.check_ship_gate": lambda value: check_demo_ship_gate(
                quality_released=bool(value["quality_released"]),
                document_package_approved=bool(value["document_package_approved"]),
            ),
            "tracking.detect_risk": _detect_risk,
            "quality.check_completeness": _complete_check,
            "quality.build_package_draft": _passthrough,
            "quality.build_checklist": _build_checklist,
            "quality.collect_evidence": _collect_evidence,
        }
        self._owners = {
            AgentType.QUOTATION: {name for name in self._handlers if name.startswith("quotation.")},
            AgentType.PROCUREMENT: {name for name in self._handlers if name.startswith("procurement.")},
            AgentType.TRACKING: {name for name in self._handlers if name.startswith("tracking.")},
            AgentType.QUALITY_DOCUMENT: {name for name in self._handlers if name.startswith("quality.")},
        }

    def invoke(self, agent_type: AgentType, capability_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if capability_id not in self._owners[agent_type]:
            raise PermissionError(f"{agent_type.value} Agent 无此工具白名单：{capability_id}")
        handler = self._handlers.get(capability_id)
        if handler is None:
            raise LookupError(f"ToolRegistry 未注册能力：{capability_id}")
        output = handler(inputs)
        output = {**output, "rule_version": output.get("rule_version", RULE_VERSION)}
        output["tool_call_id"] = stable_id("TOOL", f"{agent_type.value}:{capability_id}:{canonical_hash(inputs)}")
        output["tool_version"] = RULE_VERSION
        output["input_hash"] = canonical_hash(inputs)
        output["evidence_ids"] = output.get("evidence_ids", [])
        output["deterministic"] = True
        output["output_hash"] = canonical_hash({key: value for key, value in output.items() if key != "output_hash"})
        return output


tool_registry = ToolRegistry()
