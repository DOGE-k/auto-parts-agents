"""Transparent deterministic rules for the synthetic demonstration only.

These rules are not ERP/MES facts or a production costing policy. Replace them
with an approved, versioned rule set before using real business data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


RULE_VERSION = "demo-rules-1.0.0"
MONEY = Decimal("0.01")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_demo_quote_cost(inputs: dict[str, Any]) -> dict[str, Any]:
    """Calculate synthetic unit/total cost and quote price with a target margin."""
    quantity = int(inputs["order_quantity"])
    if quantity <= 0:
        raise ValueError("演示订单数量必须大于 0")

    material_cost = sum(
        Decimal(line["qty_per_product"]) * Decimal(line["unit_price"])
        for line in inputs["bom"]
    )
    fixed_per_unit = sum(
        Decimal(inputs["cost_components"][name])
        for name in ("processing", "outsourcing", "inspection", "packaging", "logistics")
    )
    pre_risk = material_cost + fixed_per_unit
    risk_rate = Decimal(inputs["risk_buffer_rate"])
    unit_cost = _money(pre_risk * (Decimal("1") + risk_rate))
    target_margin = Decimal(inputs["target_gross_margin"])
    minimum_margin = Decimal(inputs["minimum_gross_margin"])
    if not Decimal("0") <= minimum_margin < Decimal("1") or not Decimal("0") <= target_margin < Decimal("1"):
        raise ValueError("毛利率必须在 0 到 1 之间")

    unit_price = _money(unit_cost / (Decimal("1") - target_margin))
    gross_margin = (unit_price - unit_cost) / unit_price
    total_cost = _money(unit_cost * quantity)
    total_price = _money(unit_price * quantity)
    result = {
        "rule_version": RULE_VERSION,
        "currency": inputs["currency"],
        "order_quantity": quantity,
        "material_unit_cost": str(_money(material_cost)),
        "fixed_unit_cost": str(_money(fixed_per_unit)),
        "risk_buffer_rate": str(risk_rate),
        "unit_cost": str(unit_cost),
        "target_gross_margin": str(target_margin),
        "minimum_gross_margin": str(minimum_margin),
        "suggested_unit_price": str(unit_price),
        "actual_gross_margin": str(gross_margin.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "total_cost": str(total_cost),
        "total_price": str(total_price),
        "requires_low_margin_exception": gross_margin < minimum_margin,
        "calculation_note": "演示规则：单位成本=(BOM材料+加工+外协+检验+包装+物流)×(1+风险预留率)；建议价=单位成本÷(1-目标毛利率)。",
    }
    result["tool_call_id"] = f"tool-{_hash(inputs)[:16]}"
    result["tool_version"] = RULE_VERSION
    result["input_hash"] = _hash(inputs)
    result["output_hash"] = _hash(result)
    result["evidence_ids"] = ["ev-mock-erp-bom", "ev-mock-erp-item-price"]
    result["deterministic"] = True
    return result


def calculate_demo_net_requirement(inputs: dict[str, Any]) -> dict[str, Any]:
    """Exclude unusable stock and unconfirmed inbound quantities."""
    demand = Decimal(inputs["required_quantity"])
    excluded_statuses = {"frozen", "inspection", "rejected", "no_batch"}
    available = sum(
        Decimal(row["quantity"])
        for row in inputs["inventory"]
        if row["status"] not in excluded_statuses
    )
    confirmed_inbound = sum(
        Decimal(row["quantity"])
        for row in inputs["inbound"]
        if row["confirmed"] is True
    )
    net = max(Decimal("0"), demand - available - confirmed_inbound)
    result = {
        "rule_version": RULE_VERSION,
        "required_quantity": str(demand),
        "usable_inventory": str(available),
        "confirmed_inbound": str(confirmed_inbound),
        "net_requirement": str(net),
        "excluded_inventory_statuses": sorted(excluded_statuses),
        "calculation_note": "演示规则：净需求=max(0,正式需求-合格可用库存-已确认在途)；冻结、待检、不合格、无批次库存排除。",
    }
    result["tool_call_id"] = f"tool-{_hash(inputs)[:16]}"
    result["tool_version"] = RULE_VERSION
    result["input_hash"] = _hash(inputs)
    result["output_hash"] = _hash(result)
    result["evidence_ids"] = ["ev-mock-erp-demand", "ev-mock-erp-inventory"]
    result["deterministic"] = True
    return result


def calculate_demo_eta(inputs: dict[str, Any]) -> dict[str, Any]:
    """Use calendar days and a critical path based on material/capacity dates."""
    material_date = date.fromisoformat(inputs["material_available_date"])
    capacity_date = date.fromisoformat(inputs["capacity_completion_date"])
    base = max(material_date, capacity_date)
    quality_days = int(inputs["quality_wait_days"])
    buffer_days = int(inputs["buffer_days"])
    eta = base + timedelta(days=quality_days + buffer_days)
    result = {
        "rule_version": RULE_VERSION,
        "eta_date": eta.isoformat(),
        "critical_path": "material" if material_date >= capacity_date else "capacity",
        "material_available_date": material_date.isoformat(),
        "capacity_completion_date": capacity_date.isoformat(),
        "quality_wait_days": quality_days,
        "buffer_days": buffer_days,
        "calculation_note": "演示规则：ETA=材料可用日与产能完成日的较晚者+质量等待日历天+缓冲日历天。",
    }
    result["tool_call_id"] = f"tool-{_hash(inputs)[:16]}"
    result["tool_version"] = RULE_VERSION
    result["input_hash"] = _hash(inputs)
    result["output_hash"] = _hash(result)
    result["evidence_ids"] = ["ev-mock-erp-material-date", "ev-mock-mes-capacity"]
    result["deterministic"] = True
    return result


def check_demo_ship_gate(*, quality_released: bool, document_package_approved: bool) -> dict[str, Any]:
    """Require two independent positive signals for shipment readiness."""
    ready = quality_released and document_package_approved
    inputs = {
        "quality_released": quality_released,
        "document_package_approved": document_package_approved,
    }
    result = {
        **inputs,
        "ready_to_request_shipment_approval": ready,
        "rule_version": RULE_VERSION,
        "calculation_note": "质量放行和资料包批准是两道独立门禁，必须同时满足。",
    }
    result["tool_call_id"] = f"tool-{_hash(inputs)[:16]}"
    result["tool_version"] = RULE_VERSION
    result["input_hash"] = _hash(inputs)
    result["output_hash"] = _hash(result)
    result["evidence_ids"] = []
    result["deterministic"] = True
    return result
