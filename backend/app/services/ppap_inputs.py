"""PPAP 结构化输入契约：13 个数据集的必填字段与校验规则。

硬性边界：

- 字段不猜：数据集字段以本契约为准，未知字段显式报错（不静默丢弃、不隐藏默认值）。
- 单位与规格不做隐式换算：数值字段必须是真实数值（拒绝布尔值），
  单位原样保留，换算只能由人工确认后修改原始输入。
- 左右件：左右件敏感数据集不得使用 ``NONE`` 侧。
- 输入不携带判定字段：合格/不合格等结论只能由一致性检查和已确认的业务规则产生，
  人工输入里出现判定字段一律拒绝。
- ``null`` 表示"确认无值"，与"未提供该字段"区分开。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.ppap_template import SIDE_SENSITIVE_DATASETS

SIDE_NONE = "NONE"
SIDE_LEFT = "LEFT"
SIDE_RIGHT = "RIGHT"
SIDE_BOTH = "BOTH"
VALID_SIDES = (SIDE_NONE, SIDE_LEFT, SIDE_RIGHT, SIDE_BOTH)

# 人工输入里不允许出现的判定/结论类字段（判定由检查阶段与业务规则产生）
VERDICT_FIELDS = {
    "result",
    "verdict",
    "conclusion",
    "acceptable",
    "acceptance",
    "pass",
    "passed",
    "judge",
    "判定",
    "结论",
    "合格",
    "判定结果",
}

_CONTRACTS: dict[str, dict[str, Any]] = {
    "product_base": {
        "label": "产品基础信息",
        "required": ("part_no", "part_name", "customer_name"),
        "optional": ("drawing_version", "engineering_change_level", "revision_note"),
        "numeric": (),
        "boolean": (),
    },
    "process_operation": {
        "label": "工序",
        "required": ("op_id", "op_name"),
        "optional": ("station", "description", "sequence"),
        "numeric": ("sequence",),
        "boolean": (),
    },
    "process_relation": {
        "label": "工序关系",
        "required": ("from_op", "to_op"),
        "optional": ("relation_type", "note"),
        "numeric": (),
        "boolean": (),
    },
    "characteristic": {
        "label": "产品/过程特性",
        "required": ("characteristic_id", "name", "characteristic_type"),
        "optional": (
            "special",
            "unit",
            "usl",
            "lsl",
            "nominal",
            "target",
            "drawing_location",
            "note",
        ),
        "numeric": ("usl", "lsl", "nominal", "target"),
        "boolean": ("special",),
    },
    "inspection_value": {
        "label": "检验原始值",
        "required": ("characteristic_id", "point"),
        "optional": (
            "value",
            "usl",
            "lsl",
            "nominal",
            "unit",
            "sample_no",
            "measured_by",
            "measured_at",
            "missing_reason",
        ),
        "numeric": ("value", "usl", "lsl", "nominal"),
        "boolean": (),
    },
    "msa_study": {
        "label": "MSA 原始研究",
        "required": ("study_id", "characteristic_id"),
        # unit 是检查器 PPAP-CHK-UNIT-001 的必需字段：特性已声明单位时，
        # 研究记录缺单位或缺对应单位都会被记为缺项，契约必须能承接该字段。
        "optional": ("measurements", "tolerance", "unit", "method", "note"),
        "numeric": ("tolerance",),
        "boolean": (),
    },
    "process_study": {
        "label": "过程研究原始样本",
        "required": ("study_id", "characteristic_id"),
        # 同上：缺少 unit 会让 UNIT-001 的 unit_missing 永远无法补齐。
        "optional": ("values", "subgroups", "usl", "lsl", "target", "unit", "sampling_note", "note"),
        "numeric": ("usl", "lsl", "target"),
        "boolean": (),
    },
    "material_evidence": {
        "label": "材料/性能证据",
        "required": ("test_item",),
        "optional": (
            "supplier",
            "lot_no",
            "report_no",
            "spec_ref",
            "unit",
            "value",
            "note",
        ),
        "numeric": ("value",),
        "boolean": (),
    },
    "packaging_equipment": {
        "label": "设备与包装",
        "required": ("item",),
        "optional": ("quantity", "owner", "note"),
        "numeric": ("quantity",),
        "boolean": (),
    },
    "customer_requirement": {
        "label": "客户特殊要求",
        "required": ("requirement_text",),
        "optional": ("source", "clause", "note"),
        "numeric": (),
        "boolean": (),
    },
    "change_record": {
        "label": "工程变更记录",
        "required": ("change_description",),
        "optional": ("change_no", "change_date", "level", "reason"),
        "numeric": (),
        "boolean": (),
    },
    "physical_record": {
        "label": "实物记录",
        "required": ("record_type",),
        "optional": ("quantity", "storage_location", "photo_refs", "note"),
        "numeric": ("quantity",),
        "boolean": (),
    },
    "customer_handling": {
        "label": "客户 handling 要求",
        "required": ("instruction",),
        "optional": ("stage", "note"),
        "numeric": (),
        "boolean": (),
    },
}


def dataset_contract(dataset: str) -> dict[str, Any] | None:
    contract = _CONTRACTS.get(dataset)
    if not contract:
        return None
    return dict(contract)


def dataset_contracts() -> dict[str, dict[str, Any]]:
    return {name: dict(contract) for name, contract in _CONTRACTS.items()}


def normalize_part_side(value: Any) -> tuple[str | None, str | None]:
    """侧别只接受 NONE/LEFT/RIGHT/BOTH（大小写不敏感），不做别名猜测。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return SIDE_NONE, None
    text = str(value).strip().upper()
    if text not in VALID_SIDES:
        return None, f"侧别只允许 {'/'.join(VALID_SIDES)}，收到：{value!r}"
    return text, None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    return isinstance(value, (int, float))


def validate_input(
    dataset: str,
    payload: Any,
    *,
    part_side: Any = None,
) -> dict[str, Any]:
    """按数据集契约校验并归一化一条人工确认输入。

    返回 ``{"ok", "dataset", "errors", "payload", "part_side"}``；
    校验失败时 ``payload`` 为 None，绝不返回半修正结果。
    """
    outcome: dict[str, Any] = {
        "ok": False,
        "dataset": dataset,
        "errors": [],
        "payload": None,
        "part_side": None,
    }
    contract = _CONTRACTS.get(dataset)
    if not contract:
        outcome["errors"].append(
            f"未知数据集 {dataset!r}，允许的数据集：{sorted(_CONTRACTS)}"
        )
        return outcome

    side, side_error = normalize_part_side(part_side)
    if side_error:
        outcome["errors"].append(side_error)
        return outcome

    if not isinstance(payload, Mapping):
        outcome["errors"].append("payload 必须是对象（键值对）")
        return outcome

    errors = outcome["errors"]
    allowed = set(contract["required"]) | set(contract["optional"])
    unknown = sorted(set(map(str, payload.keys())) - allowed - VERDICT_FIELDS)
    verdict_hits = sorted(VERDICT_FIELDS & set(map(str, payload.keys())))
    if verdict_hits:
        errors.append(
            f"输入不允许携带判定/结论字段：{verdict_hits}（判定由一致性检查与业务规则产生）"
        )
    if unknown:
        errors.append(
            f"数据集 {dataset} 不支持字段：{unknown}；允许字段：{sorted(allowed)}"
        )
    if errors:
        return outcome

    for field in contract["required"]:
        if field not in payload:
            errors.append(f"缺少必填字段：{field}")
            continue
        value = payload[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"必填字段不能为空：{field}")
    if errors:
        return outcome

    for field in contract["numeric"]:
        if field in payload and payload[field] is not None and not _is_number(payload[field]):
            errors.append(f"字段 {field} 必须是数值（不接受布尔/文本/空值以外的占位）")
    for field in contract["boolean"]:
        if field in payload and payload[field] is not None and not isinstance(payload[field], bool):
            errors.append(f"字段 {field} 必须是布尔值")
    if errors:
        return outcome

    if dataset in SIDE_SENSITIVE_DATASETS and side == SIDE_NONE:
        errors.append(
            f"数据集 {dataset} 属于左右件敏感数据，必须显式标注 LEFT/RIGHT/BOTH，不能使用 NONE"
        )
        return outcome

    normalized: dict[str, Any] = {}
    for field in sorted(allowed):
        if field not in payload:
            continue
        value = payload[field]
        if isinstance(value, str):
            value = value.strip()
        normalized[field] = value

    outcome["ok"] = True
    outcome["payload"] = normalized
    outcome["part_side"] = side
    return outcome
