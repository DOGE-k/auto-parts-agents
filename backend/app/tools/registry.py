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


class ToolRegistry:
    """Closed registry: handlers are explicitly installed and agent scoped."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "quotation.calculate_cost": calculate_demo_quote_cost,
            "quotation.create_draft": _passthrough,
            "quotation.create_cost_assessment": _cost_deltas,
            "procurement.calculate_net_requirement": calculate_demo_net_requirement,
            "procurement.compare_supply_plans": _passthrough,
            "procurement.create_po_draft": _passthrough,
            "tracking.calculate_eta": calculate_demo_eta,
            "tracking.check_ship_gate": lambda value: check_demo_ship_gate(
                quality_released=bool(value["quality_released"]),
                document_package_approved=bool(value["document_package_approved"]),
            ),
            "quality.check_completeness": _complete_check,
            "quality.build_package_draft": _passthrough,
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
