"""PPAP 跨表一致性检查（12 项检查）。

检查器是纯函数：只读取传入的上下文快照（任务、条目、输入、证据、规则、模板），
不访问数据库、不调用任何接口、不修改任何数据。它只产出**缺项/冲突/规则未确认**
清单，绝不产出"合格"结论：验收判定交给 ``ppap_calc.classify_metric`` 与已确认的业务
规则，实际批准交给人工审核。

严重度约定（AC09）：只有 ``BLOCKER`` 阻止批准；``MAJOR`` 与 ``INFO`` 只提示。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.services import ppap_characteristics, ppap_inputs, ppap_template
from app.services.ppap_template import (
    DS_CHARACTERISTIC,
    DS_CUSTOMER_REQUIREMENT,
    DS_INSPECTION_VALUE,
    DS_MATERIAL_EVIDENCE,
    DS_MSA_STUDY,
    DS_PROCESS_OPERATION,
    DS_PROCESS_STUDY,
    DS_PRODUCT_BASE,
    EL_NOT_APPLICABLE,
    REQ_NOT_APPLICABLE,
    REQ_ON_REQUEST,
    REQ_SUBMIT,
    REQ_UNCONFIRMED,
    UNMET_ELEMENT_STATES,
    element_spec,
)

SEV_BLOCKER = "BLOCKER"
SEV_MAJOR = "MAJOR"
SEV_INFO = "INFO"

KIND_MISSING = "MISSING_DATA"
KIND_INCONSISTENCY = "INCONSISTENCY"
KIND_RULE = "RULE_UNCONFIRMED"
KIND_APPLICABILITY = "APPLICABILITY_UNCONFIRMED"
KIND_TEMPLATE = "TEMPLATE_NOTICE"

# 检查项定义：(check_id, severity, title, 责任岗)
CHECK_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    ("PPAP-CHK-BASE-001", SEV_BLOCKER, "基础信息一致性", "quality_manager"),
    ("PPAP-CHK-SIDE-001", SEV_BLOCKER, "左右件隔离与跨侧引用", "quality_manager"),
    ("PPAP-CHK-MAT-001", SEV_MAJOR, "材料与性能证据", "purchase_manager"),
    ("PPAP-CHK-OP-001", SEV_BLOCKER, "工序清单一致性", "production_manager"),
    ("PPAP-CHK-CHAR-001", SEV_BLOCKER, "特性清单一致性", "production_manager"),
    ("PPAP-CHK-UNIT-001", SEV_BLOCKER, "单位一致性", "quality_manager"),
    ("PPAP-CHK-MSA-001", SEV_MAJOR, "测量系统分析", "quality_manager"),
    ("PPAP-CHK-STUDY-001", SEV_MAJOR, "初始过程研究", "quality_manager"),
    ("PPAP-CHK-VER-001", SEV_BLOCKER, "版本口径一致性", "quality_manager"),
    ("PPAP-CHK-EVID-001", SEV_BLOCKER, "来源证据可追溯", "quality_manager"),
    ("PPAP-CHK-TPL-001", SEV_INFO, "模板口径提示", "quality_manager"),
    ("PPAP-CHK-REQ-001", SEV_BLOCKER, "适用性与提交要求", "quality_manager"),
)

CHECK_IDS = tuple(item[0] for item in CHECK_DEFINITIONS)
CHECK_SEVERITY = {item[0]: item[1] for item in CHECK_DEFINITIONS}
CHECK_TITLE = {item[0]: item[2] for item in CHECK_DEFINITIONS}
CHECK_ROLE = {item[0]: item[3] for item in CHECK_DEFINITIONS}


# ----------------------------------------------------------------------
# 上下文读取辅助
# ----------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _payload(record: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("payload", "payload_json"):
        if key in record:
            return _as_dict(record.get(key))
    return {}


def _by_dataset(inputs: Sequence[Mapping[str, Any]], dataset: str) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in inputs
        if _text(_as_dict(item).get("dataset")) == dataset
    ]


def _elements_by_id(elements: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in elements:
        record = _as_dict(item)
        element_id = _text(record.get("element_id"))
        if element_id:
            out[element_id] = record
    return out


def _content(element: Mapping[str, Any] | None) -> dict[str, Any]:
    if not element:
        return {}
    for key in ("content", "content_json"):
        if key in element:
            return _as_dict(element.get(key))
    return {}


def _spec(element: Mapping[str, Any]):
    element_id = _text(element.get("element_id"))
    return element_spec(element_id) if element_id else None


def _element_status(element: Mapping[str, Any] | None) -> str:
    return _text((element or {}).get("status"))


def _finding(
    check_id: str,
    *,
    scope: str,
    kind: str,
    title: str,
    detail: str,
    basis: str = "",
    element_id: str | None = None,
    field_path: str | None = None,
    required_evidence: str = "",
    responsible_role: str | None = None,
) -> dict[str, Any]:
    severity = CHECK_SEVERITY[check_id]
    dedup = f"{check_id}:{scope}"[:240]
    return {
        "dedup_key": dedup,
        "kind": kind,
        "severity": severity,
        "check_id": check_id,
        "element_id": element_id,
        "field_path": field_path,
        "title": title,
        "detail": detail,
        "basis": basis,
        "required_evidence": required_evidence,
        "responsible_role": responsible_role or CHECK_ROLE[check_id],
        "blocks_approval": severity == SEV_BLOCKER,
    }


def _calc_entries(element: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    content = _content(element)
    entries: list[dict[str, Any]] = []
    for key in ("calculations", "studies"):
        for item in _as_list(content.get(key)):
            record = _as_dict(item)
            if record:
                entries.append(record)
    return entries


def _study_metric_findings(
    check_id: str,
    element_id: str,
    element: Mapping[str, Any] | None,
    metrics: tuple[str, ...],
) -> list[dict[str, Any]]:
    """把计算/判定结果里的"未确认/不达标/缺值"转成检查发现。"""
    findings: list[dict[str, Any]] = []
    entries = _calc_entries(element)
    relevant = [e for e in entries if _text(e.get("metric")) in metrics]
    if not relevant:
        findings.append(
            _finding(
                check_id,
                scope=f"{element_id}:no_result",
                kind=KIND_MISSING,
                title=f"{ppap_template.element_spec(element_id).title}缺少计算结果",
                detail=(
                    f"条目 {element_id} 尚未得到 {list(metrics)} 的确定性计算结果，"
                    "无法判断是否可以提交。"
                ),
                basis="设计说明 §5.2 计算方法表：指标类条目必须先完成确定性计算",
                element_id=element_id,
                required_evidence="原始测量/研究数据（由已登录用户确认来源后录入）",
            )
        )
        return findings
    for entry in relevant:
        metric = _text(entry.get("metric"))
        calc_state = _text(entry.get("calc_state"))
        acceptance = _text(entry.get("acceptance_state"))
        if calc_state and calc_state != "OK":
            if calc_state == "INCOMPLETE":
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{element_id}:{metric}:condition",
                        kind=KIND_RULE,
                        title=f"{metric} 的样本条件需业务确认",
                        detail=(
                            f"指标 {metric} 已计算（当前值 {entry.get('value')}），但存在需确认的样本条件："
                            f"{'；'.join(_as_list(entry.get('notes'))[:3]) or '；'.join(_as_list(entry.get('flags'))[:3])}"
                        ),
                        basis="样本量与抽样规则属于业务规则，未确认前不得当作达标（设计说明 §14）",
                        element_id=element_id,
                        field_path=metric,
                        required_evidence="业务方对样本量/抽样规则的确认",
                    )
                )
                continue
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:{metric}:calc",
                    kind=KIND_MISSING,
                    title=f"{metric} 未完成计算（{calc_state}）",
                    detail=(
                        f"指标 {metric} 计算状态为 {calc_state}："
                        f"{'；'.join(_as_list(entry.get('problems'))[:3]) or '需补充原始数据'}"
                    ),
                    basis="计算结果边界状态必须显式暴露，不得按合格处理",
                    element_id=element_id,
                    field_path=metric,
                    required_evidence="补齐原始数据后重新计算",
                )
            )
            continue
        if acceptance in ("BELOW_RULE", "NEEDS_CONFIRMATION", "NO_VALUE"):
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:{metric}:acceptance",
                    kind=KIND_RULE if acceptance == "NEEDS_CONFIRMATION" else KIND_MISSING,
                    title=f"{metric} 验收状态为 {acceptance}",
                    detail=(
                        f"指标 {metric} 当前值 {entry.get('value')}，验收状态 {acceptance}："
                        f"{_text(entry.get('basis')) or '缺少已确认的业务规则阈值'}"
                    ),
                    basis="未取得人工确认的业务规则前不得判定达标（设计说明 §5.3/§14）",
                    element_id=element_id,
                    field_path=metric,
                    required_evidence="已确认的 acceptance_rules.json 阈值或改进后的数据",
                )
            )
        elif acceptance == "CONDITIONAL_PER_RULE":
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:{metric}:conditional",
                    kind=KIND_RULE,
                    title=f"{metric} 按规则为条件接受",
                    detail=(
                        f"指标 {metric} 当前值 {entry.get('value')} 落在条件接受区间："
                        f"{_text(entry.get('basis'))}"
                    ),
                    basis="条件接受需要业务确认与书面依据",
                    element_id=element_id,
                    field_path=metric,
                    required_evidence="业务方对条件接受的书面确认",
                )
            )
    return findings


# ----------------------------------------------------------------------
# 各检查项
# ----------------------------------------------------------------------


def check_base_info(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-BASE-001"
    task = _as_dict(context.get("task"))
    findings: list[dict[str, Any]] = []
    base_records = _by_dataset(_as_list(context.get("inputs")), DS_PRODUCT_BASE)
    if not base_records:
        findings.append(
            _finding(
                check_id,
                scope="no_base_input",
                kind=KIND_MISSING,
                title="缺少产品基础信息确认记录",
                detail=(
                    "未录入 product_base 数据集：产品编号、名称、客户、图纸版本与工程变更等级"
                    "必须以人工确认的结构化输入为准，不能靠臆测。"
                ),
                basis="设计说明 §5.1 基础信息统一；真实数据硬性规则：禁止猜测字段与默认值",
                required_evidence="product_base 数据集（客户/产品/图纸版本/工程变更等级）",
            )
        )
        return findings

    task_expected = {
        "part_no": _text(task.get("part_no")),
        "part_name": _text(task.get("part_name")),
        "customer_name": _text(task.get("customer_name")),
        "drawing_version": _text(task.get("drawing_version")),
        "engineering_change_level": _text(task.get("engineering_change_level")),
    }
    seen: dict[str, set[str]] = {}
    for record in base_records:
        payload = _payload(record)
        record_key = _text(record.get("record_key")) or "?"
        for field, expected in task_expected.items():
            actual = _text(payload.get(field))
            if actual:
                seen.setdefault(field, set()).add(actual)
            if actual and expected and actual != expected:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{record_key}:{field}",
                        kind=KIND_INCONSISTENCY,
                        title=f"{field} 与任务基础信息不一致",
                        detail=(
                            f"输入记录 {record_key} 的 {field}={actual!r}，"
                            f"任务登记为 {expected!r}。"
                        ),
                        basis="基础信息必须唯一、可追溯；冲突需人工确认哪一个是现行版本",
                        field_path=field,
                        required_evidence="确认后的产品基础信息（含图纸版本与变更等级）",
                    )
                )
        for field in ("part_no", "part_name", "customer_name"):
            if not _text(payload.get(field)):
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{record_key}:{field}:empty",
                        kind=KIND_MISSING,
                        title=f"基础信息 {field} 缺失",
                        detail=f"输入记录 {record_key} 未提供 {field}。",
                        basis="基础信息字段不允许留空或使用默认值",
                        field_path=field,
                        required_evidence=f"{field} 的人工确认值",
                    )
                )
    for field, values in seen.items():
        if len(values) > 1:
            findings.append(
                _finding(
                    check_id,
                    scope=f"multi:{field}",
                    kind=KIND_INCONSISTENCY,
                    title=f"{field} 存在多个取值",
                    detail=f"输入记录中 {field} 出现多个不同取值：{sorted(values)}。",
                    basis="同一 PPAP 任务的基础信息必须唯一，多个取值需人工确认",
                    field_path=field,
                    required_evidence="唯一确认值及其来源",
                )
            )
    return findings


def check_part_side(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-SIDE-001"
    findings: list[dict[str, Any]] = []
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    side_sensitive = set(ppap_template.SIDE_SENSITIVE_DATASETS)

    for record in inputs:
        dataset = _text(record.get("dataset"))
        if dataset not in side_sensitive:
            continue
        record_key = _text(record.get("record_key")) or "?"
        side = _text(record.get("part_side")).upper()
        if side not in (ppap_inputs.SIDE_LEFT, ppap_inputs.SIDE_RIGHT, ppap_inputs.SIDE_BOTH):
            findings.append(
                _finding(
                    check_id,
                    scope=f"{dataset}:{record_key}",
                    kind=KIND_MISSING,
                    title=f"{dataset} 记录 {record_key} 未标注左右件",
                    detail=(
                        f"数据集 {dataset} 属于左右件敏感数据，当前侧别为 {side or 'NONE'}，"
                        "左右件必须分别取证与录入。"
                    ),
                    basis="设计说明 §5.3 左右件隔离；AC01",
                    field_path="part_side",
                    required_evidence="明确 LEFT/RIGHT/BOTH 的记录",
                )
            )

    char_sides: dict[str, set[str]] = {}
    char_units: dict[str, set[str]] = {}
    for record in _by_dataset(inputs, DS_CHARACTERISTIC):
        payload = _payload(record)
        char_id = _text(payload.get("characteristic_id"))
        side = _text(record.get("part_side")).upper()
        if char_id and side:
            char_sides.setdefault(char_id, set()).add(side)
        unit = _text(payload.get("unit"))
        if char_id and unit:
            char_units.setdefault(char_id, set()).add(unit)

    for dataset in (DS_INSPECTION_VALUE, DS_MSA_STUDY, DS_PROCESS_STUDY):
        for record in _by_dataset(inputs, dataset):
            payload = _payload(record)
            char_id = _text(payload.get("characteristic_id"))
            if not char_id:
                continue
            side = _text(record.get("part_side")).upper()
            known = char_sides.get(char_id)
            if known and side and side not in known:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{dataset}:{char_id}:{side}",
                        kind=KIND_INCONSISTENCY,
                        title=f"{dataset} 存在跨侧引用",
                        detail=(
                            f"记录引用的特性 {char_id} 只登记在 {sorted(known)} 侧，"
                            f"但本条记录标注为 {side}。"
                        ),
                        basis="左右件数据必须同侧对应，禁止跨侧拼凑（AC01）",
                        field_path="part_side",
                        required_evidence="同侧的原始记录或修正后的侧别",
                    )
                )
    return findings


def check_material_evidence(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-MAT-001"
    elements = _elements_by_id(_as_list(context.get("elements")))
    element = elements.get("material_performance_results")
    if not element or _element_status(element) == EL_NOT_APPLICABLE:
        return []
    if _text(element.get("requirement")) == REQ_NOT_APPLICABLE:
        return []
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    material_records = _by_dataset(inputs, DS_MATERIAL_EVIDENCE)
    inspection_records = _by_dataset(inputs, DS_INSPECTION_VALUE)
    findings: list[dict[str, Any]] = []

    if not material_records:
        findings.append(
            _finding(
                check_id,
                scope="no_material_evidence",
                kind=KIND_MISSING,
                title="缺少材料/性能证据输入",
                detail=(
                    "材料与性能结果条目没有任何 material_evidence 输入："
                    "供应商报告、批次、报告编号等必须由采购侧确认后录入。"
                ),
                basis="设计说明 §5.2：材料性能结果需材料证据或检验原始值支撑",
                element_id="material_performance_results",
                required_evidence="材料/性能报告（供应商、批次、报告编号、规格引用）",
                responsible_role="purchase_manager",
            )
        )
    else:
        traceable = [
            record
            for record in material_records
            if _text(_payload(record).get("report_no")) or _text(_payload(record).get("supplier"))
        ]
        if not traceable:
            findings.append(
                _finding(
                    check_id,
                    scope="no_traceable_material_source",
                    kind=KIND_MISSING,
                    title="材料证据缺少可追溯来源",
                    detail=(
                        f"共 {len(material_records)} 条材料证据，但没有一条带有报告编号或供应商，"
                        "无法追溯到原始报告。"
                    ),
                    basis="证据必须可追溯（AC10 来源索引）",
                    element_id="material_performance_results",
                    required_evidence="带报告编号或供应商的报告引用",
                    responsible_role="purchase_manager",
                )
            )

    if not inspection_records and not material_records:
        findings.append(
            _finding(
                check_id,
                scope="no_material_result_source",
                kind=KIND_MISSING,
                title="缺少材料/性能结果数据源",
                detail="既没有材料证据也没有检验原始值，材料性能结果无法编制。",
                basis="禁止凭空生成材料性能数据",
                element_id="material_performance_results",
                required_evidence="材料或性能测试的原始记录",
                responsible_role="purchase_manager",
            )
        )
    return findings


def _operations_from_inputs(inputs: Sequence[Mapping[str, Any]]) -> list[str]:
    ops: list[str] = []
    for record in _by_dataset(inputs, DS_PROCESS_OPERATION):
        op_id = _text(_payload(record).get("op_id"))
        if op_id and op_id not in ops:
            ops.append(op_id)
    return ops


def _operations_from_content(element: Mapping[str, Any] | None) -> list[str]:
    ops: list[str] = []
    for item in _as_list(_content(element).get("operations")):
        record = _as_dict(item)
        op_id = _text(record.get("op_id")) or _text(record.get("op_no"))
        if op_id and op_id not in ops:
            ops.append(op_id)
    return ops


def check_process_operations(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-OP-001"
    elements = _elements_by_id(_as_list(context.get("elements")))
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    findings: list[dict[str, Any]] = []

    flow = elements.get("process_flow_diagram")
    baseline = _operations_from_inputs(inputs)
    if not baseline and flow and _element_status(flow) not in (EL_NOT_APPLICABLE, "WAITING_DATA"):
        baseline = _operations_from_content(flow)

    if not baseline:
        findings.append(
            _finding(
                check_id,
                scope="no_operations",
                kind=KIND_MISSING,
                title="缺少工序清单",
                detail=(
                    "没有任何 process_operation 输入：过程流程图、PFMEA、控制计划都以工序为骨架，"
                    "工序清单必须来自工艺侧确认。"
                ),
                basis="设计说明 §5.2：三份文件共用同一工序骨架",
                element_id="process_flow_diagram",
                required_evidence="工序清单（工序号、工序名、工位）",
                responsible_role="production_manager",
            )
        )
        return findings

    for element_id in ("process_flow_diagram", "pfmea", "control_plan"):
        element = elements.get(element_id)
        if not element or _element_status(element) in (EL_NOT_APPLICABLE, "WAITING_DATA"):
            continue
        content_ops = _operations_from_content(element)
        if not content_ops:
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:no_operations",
                    kind=KIND_MISSING,
                    title=f"{ppap_template.element_spec(element_id).title}未包含工序清单",
                    detail="该条目内容中没有工序条目，无法与工序清单一对应。",
                    basis="三份过程文件必须逐一对应（设计说明 §5.3）",
                    element_id=element_id,
                    required_evidence="与工序清单一致的工序条目",
                    responsible_role="production_manager",
                )
            )
            continue
        missing = [op for op in baseline if op not in content_ops]
        extra = [op for op in content_ops if op not in baseline]
        if missing or extra:
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:mismatch",
                    kind=KIND_INCONSISTENCY,
                    title=f"{ppap_template.element_spec(element_id).title}工序清单不一致",
                    detail=(
                        f"相对确认的工序清单缺少 {missing or '无'}，多出 {extra or '无'}。"
                    ),
                    basis="过程流程图/PFMEA/控制计划必须使用同一工序清单（设计说明 §5.3）",
                    element_id=element_id,
                    required_evidence="与工序清单一致的修订内容",
                    responsible_role="production_manager",
                )
            )
    return findings


def _characteristics_from_inputs(
    inputs: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Only unambiguous same-side definitions may be used by downstream checks."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous: set[tuple[str, str]] = set()
    for group in ppap_characteristics.group_characteristics(inputs):
        key = (group["characteristic_id"], group["part_side"])
        if key in out or not group["usable"]:
            ambiguous.add(key)
        elif key not in ambiguous:
            out[key] = ppap_characteristics.payload_of(group["records"][0])
    for key in ambiguous:
        out.pop(key, None)
    return out


def _characteristic_label(key: tuple[str, str]) -> str:
    return f"{key[0]}（{key[1] or '未标侧别'}）"


def _characteristic_group_findings(groups: Sequence[Mapping[str, Any]], *,
                                   element_id: str | None = None) -> list[dict[str, Any]]:
    findings = []
    for group in groups:
        char_id, side = group["characteristic_id"], group["part_side"]
        if not group["duplicate"] and side in ppap_characteristics.VALID_SIDES:
            continue
        fields = [*group["conflict_fields"]]
        if side not in ppap_characteristics.VALID_SIDES:
            fields.append("part_side")
        reason = (f"定义字段冲突：{fields}" if fields else
                  "存在重复记录，即使定义相同也必须人工消除重复" if group["duplicate"] else
                  "侧别未明确，不能选择对应规格")
        finding = _finding("PPAP-CHK-CHAR-001",
            scope=f"{element_id or 'input'}:{group['task_id']}:{char_id}:{side}:definition",
            kind=KIND_INCONSISTENCY,
            title=f"特性 {char_id}（{side or '未标侧别'}）规格未唯一确认",
            detail=f"{reason}。全部来源：{ppap_characteristics.describe_sources(group['sources'])}",
            basis="同一任务、特性编号与侧别必须只有一条当前确认定义；不得任选来源或默认补规格",
            element_id=element_id, field_path="characteristics",
            required_evidence="人工核对全部来源，修订现有记录并明确唯一规格/单位/特殊特性定义")
        finding["sources"] = group["sources"]
        finding["conflict_fields"] = fields
        findings.append(finding)
    return findings


def check_characteristics(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-CHAR-001"
    elements = _elements_by_id(_as_list(context.get("elements")))
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    confirmed = _characteristics_from_inputs(inputs)
    groups = ppap_characteristics.group_characteristics(inputs)
    findings: list[dict[str, Any]] = _characteristic_group_findings(groups)

    if not groups:
        findings.append(
            _finding(
                check_id,
                scope="no_characteristics",
                kind=KIND_MISSING,
                title="缺少特性清单",
                detail=(
                    "没有 characteristic 输入：过程流程图、PFMEA、控制计划与测量数据必须围绕"
                    "同一套已确认的产品/过程特性。"
                ),
                basis="设计说明 §5.1 特性结构统一",
                element_id="control_plan",
                required_evidence="特性清单（特性编号、名称、类型、特殊特性标识、规格与单位）",
                responsible_role="production_manager",
            )
        )
        return findings

    specials = [key for key, payload in confirmed.items() if payload.get("special") is True]

    # Explicit measurement limits may not silently override the characteristic.
    prepared = ppap_characteristics.prepare_dimensional_comparison(
        _by_dataset(inputs, DS_CHARACTERISTIC), _by_dataset(inputs, DS_INSPECTION_VALUE))
    for issue in prepared["specification_conflicts"]:
        finding = _finding(check_id,
            scope=f"measurement:{issue['input_id'] or issue['record_key']}:{issue['characteristic_id']}:{issue['part_side']}",
            kind=KIND_INCONSISTENCY,
            title=f"检验记录 {issue['record_key'] or issue['input_id'] or '?'} 的特性规格不能比对",
            detail=issue["detail"], basis="测量、特性及侧别必须使用同一套明确规格；缺失或冲突不得按达标处理",
            element_id="dimensional_results", field_path="characteristics",
            required_evidence="确认同侧的特性规格、单位及本条原始检验记录")
        finding["sources"] = issue["sources"]
        finding["conflict_fields"] = issue["conflict_fields"]
        findings.append(finding)
    for element_id in ("pfmea", "control_plan"):
        element = elements.get(element_id)
        if not element or _element_status(element) == EL_NOT_APPLICABLE:
            continue
        content_records = [_as_dict(item) for item in _as_list(_content(element).get("characteristics"))]
        if _element_status(element) == "WAITING_DATA" and not content_records:
            continue
        content_groups = ppap_characteristics.group_characteristics(content_records)
        findings.extend(_characteristic_group_findings(content_groups, element_id=element_id))
        in_content = {(group["characteristic_id"], group["part_side"]): group
                      for group in content_groups if group["usable"]}
        if not in_content:
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:no_characteristics",
                    kind=KIND_MISSING,
                    title=f"{ppap_template.element_spec(element_id).title}未包含特性清单",
                    detail="该条目内容中没有特性条目，无法与已确认特性清单对应。",
                    basis="特性必须逐一落入 PFMEA 与控制计划（设计说明 §5.3）",
                    element_id=element_id,
                    required_evidence="与特性清单一致的特性条目",
                    responsible_role="production_manager",
                )
            )
            continue
        for key in sorted(set(confirmed) & set(in_content)):
            group = in_content[key]
            fields = ppap_characteristics.definition_conflicts(
                [confirmed[key], *group["records"]])
            if fields:
                input_sources = [source for input_group in groups
                                 if (input_group["characteristic_id"], input_group["part_side"]) == key
                                 for source in input_group["sources"]]
                all_sources = [*input_sources, *group["sources"]]
                finding = _finding(check_id,
                    scope=f"{element_id}:{key[0]}:{key[1]}:definition_mismatch",
                    kind=KIND_INCONSISTENCY,
                    title=f"{ppap_template.element_spec(element_id).title}特性定义与确认来源不一致",
                    detail=f"特性 {_characteristic_label(key)} 的字段不一致或缺失：{fields}；"
                           f"全部确认/内容来源：{ppap_characteristics.describe_sources(all_sources)}",
                    basis="特殊特性标识、规格与单位必须来自同一当前确认定义，不能默认填入",
                    element_id=element_id, field_path="characteristics",
                    required_evidence="按唯一已确认特性定义修订条目")
                finding["sources"] = all_sources
                finding["conflict_fields"] = fields
                findings.append(finding)
        missing_special = [_characteristic_label(key) for key in specials if key not in in_content]
        if missing_special:
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:special_missing",
                    kind=KIND_INCONSISTENCY,
                    title=f"{ppap_template.element_spec(element_id).title}缺少特殊特性",
                    detail=f"已确认的特殊特性未出现在该条目：{missing_special}",
                    basis="特殊特性必须在 PFMEA 与控制计划中同时受控（设计说明 §5.3）",
                    element_id=element_id,
                    required_evidence="补齐特殊特性条目",
                    responsible_role="production_manager",
                )
            )
        missing_all = [_characteristic_label(key) for key in confirmed if key not in in_content]
        if missing_all and len(missing_all) > len(missing_special):
            findings.append(
                _finding(
                    check_id,
                    scope=f"{element_id}:coverage",
                    kind=KIND_INCONSISTENCY,
                    title=f"{ppap_template.element_spec(element_id).title}特性覆盖不完整",
                    detail=(
                        f"已确认 {len(confirmed)} 项特性，该条目仅包含 {len(in_content)} 项；"
                        f"未覆盖：{missing_all[:10]}"
                    ),
                    basis="控制计划与 PFMEA 应覆盖全部已确认特性（未覆盖需登记不适用依据）",
                    element_id=element_id,
                    required_evidence="覆盖全部特性或逐项登记不适用依据",
                    responsible_role="production_manager",
                )
            )
    return findings


def check_units(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-UNIT-001"
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    findings: list[dict[str, Any]] = []

    groups = ppap_characteristics.group_characteristics(inputs)
    for group in groups:
        char_id, side = group["characteristic_id"], group["part_side"]
        if "unit" in group["conflict_fields"]:
            findings.append(
                _finding(
                    check_id,
                    scope=f"char:{char_id}:{side}:unit_conflict",
                    kind=KIND_INCONSISTENCY,
                    title=f"特性 {char_id}（{side}）单位不一致或缺失",
                    detail=f"同侧特性单位未唯一确认，无法直接比对。全部来源："
                           f"{ppap_characteristics.describe_sources(group['sources'])}",
                    basis="单位不做隐式换算，冲突必须人工确认（设计说明 §5.3）",
                    field_path="unit",
                    required_evidence="确认后的唯一单位",
                )
            )

    confirmed = _characteristics_from_inputs(inputs)
    for dataset in (DS_INSPECTION_VALUE, DS_MSA_STUDY, DS_PROCESS_STUDY, DS_MATERIAL_EVIDENCE):
        for record in _by_dataset(inputs, dataset):
            payload = _payload(record)
            char_id = _text(payload.get("characteristic_id"))
            if dataset == DS_MATERIAL_EVIDENCE:
                char_id = char_id or _text(payload.get("test_item"))
            if not char_id:
                continue
            record_key = _text(record.get("record_key")) or "?"
            unit = _text(payload.get("unit"))
            side = _text(record.get("part_side")).upper()
            definition = confirmed.get((char_id, side))
            expected = _text((definition or {}).get("unit"))
            if definition is not None and not expected:
                findings.append(_finding(check_id,
                    scope=f"{dataset}:{record_key}:{side}:characteristic_unit_missing",
                    kind=KIND_MISSING, title=f"特性 {char_id}（{side}）缺少确认单位",
                    detail=f"记录 {record_key} 引用的特性没有确认单位；本条记录的单位 {unit!r} 不能替代特性定义。",
                    basis="单位缺失不能默认从其他记录继承", field_path="unit",
                    required_evidence="同侧特性的人工确认单位"))
            if expected and not unit:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{dataset}:{record_key}:{side}:unit_missing",
                        kind=KIND_MISSING,
                        title=f"{dataset} 记录 {record_key} 缺少单位",
                        detail=f"特性 {char_id} 的单位已确认为 {expected}，本条记录未标注单位。",
                        basis="缺失单位时不得默认继承，防止错位比对",
                        field_path="unit",
                        required_evidence=f"单位（应为 {expected}）或换算说明",
                    )
                )
            elif expected and unit and unit != expected:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{dataset}:{record_key}:{side}:unit_mismatch",
                        kind=KIND_INCONSISTENCY,
                        title=f"{dataset} 记录 {record_key} 单位与特性不一致",
                        detail=(
                            f"特性 {char_id} 单位为 {expected}，本条记录为 {unit}；"
                            "不做自动换算。"
                        ),
                        basis="单位不一致必须人工确认换算关系（设计说明 §5.3）",
                        field_path="unit",
                        required_evidence="换算依据或按特性单位重新录入的数据",
                    )
                )
    return findings


def check_msa(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-MSA-001"
    elements = _elements_by_id(_as_list(context.get("elements")))
    element = elements.get("msa")
    if not element or _element_status(element) == EL_NOT_APPLICABLE:
        return []
    if _text(element.get("requirement")) == REQ_NOT_APPLICABLE:
        return []
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    findings: list[dict[str, Any]] = []

    studies = _by_dataset(inputs, DS_MSA_STUDY)
    if not studies:
        findings.append(
            _finding(
                check_id,
                scope="no_msa_study",
                kind=KIND_MISSING,
                title="缺少 MSA 原始研究数据",
                detail=(
                    "测量系统分析条目没有任何 msa_study 输入，无法计算重复性与再现性。"
                    "系统不会生成或虚构测量数据。"
                ),
                basis="设计说明 §5.2：MSA 由原始测量网格确定性计算",
                element_id="msa",
                required_evidence="按评价人/零件/重复测次组织的原始测量数据",
            )
        )
        return findings

    findings.extend(
        _study_metric_findings(
            check_id,
            "msa",
            element,
            ("percent_ev", "percent_av", "percent_grr", "percent_tolerance", "ndc"),
        )
    )

    confirmed = _characteristics_from_inputs(inputs)
    specials = [c for c, payload in confirmed.items() if payload.get("special") is True]
    covered = {
        (_text(_payload(record).get("characteristic_id")), _text(record.get("part_side")).upper())
        for record in studies
    }
    uncovered = [_characteristic_label(c) for c in specials if c not in covered]
    if uncovered:
        findings.append(
            _finding(
                check_id,
                scope="special_coverage",
                kind=KIND_MISSING,
                title="特殊特性缺少测量系统分析",
                detail=f"以下特殊特性没有对应的 MSA 研究：{uncovered}",
                basis="特殊特性必须完成测量系统分析后才能提交 PPAP",
                element_id="msa",
                required_evidence="特殊特性的 MSA 原始数据",
            )
        )
    return findings


def check_process_study(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-STUDY-001"
    elements = _elements_by_id(_as_list(context.get("elements")))
    element = elements.get("initial_process_study")
    if not element or _element_status(element) == EL_NOT_APPLICABLE:
        return []
    if _text(element.get("requirement")) == REQ_NOT_APPLICABLE:
        return []
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    findings: list[dict[str, Any]] = []

    studies = _by_dataset(inputs, DS_PROCESS_STUDY)
    if not studies:
        findings.append(
            _finding(
                check_id,
                scope="no_process_study",
                kind=KIND_MISSING,
                title="缺少初始过程研究原始样本",
                detail=(
                    "初始过程研究条目没有任何 process_study 输入，无法计算 Cp/Cpk/Pp/Ppk。"
                    "系统不会生成或虚构过程数据。"
                ),
                basis="设计说明 §5.2：初始过程研究由原始样本确定性计算",
                element_id="initial_process_study",
                required_evidence="按时间顺序的原始样本值（或子组结构）",
            )
        )
        return findings

    findings.extend(
        _study_metric_findings(
            check_id, "initial_process_study", element, ("cp", "cpk", "pp", "ppk")
        )
    )

    confirmed = _characteristics_from_inputs(inputs)
    specials = [c for c, payload in confirmed.items() if payload.get("special") is True]
    covered = {(_text(_payload(record).get("characteristic_id")), _text(record.get("part_side")).upper())
               for record in studies}
    uncovered = [_characteristic_label(c) for c in specials if c not in covered]
    if uncovered:
        findings.append(
            _finding(
                check_id,
                scope="special_coverage",
                kind=KIND_MISSING,
                title="特殊特性缺少初始过程研究",
                detail=f"以下特殊特性没有对应的过程研究数据：{uncovered}",
                basis="特殊特性必须完成初始过程研究（设计说明 §5.2）",
                element_id="initial_process_study",
                required_evidence="特殊特性的原始过程样本",
            )
        )
    return findings


def check_versions(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-VER-001"
    task = _as_dict(context.get("task"))
    inputs = [dict(item) for item in _as_list(context.get("inputs"))]
    evidence = [dict(item) for item in _as_list(context.get("evidence"))]
    findings: list[dict[str, Any]] = []

    task_version = _text(task.get("drawing_version"))
    if not task_version:
        findings.append(
            _finding(
                check_id,
                scope="drawing_version",
                kind=KIND_MISSING,
                title="任务未登记图纸版本",
                detail="PPAP 任务缺少 drawing_version：提交文件必须对应唯一图纸版本。",
                basis="设计说明 §5.3 版本一致性；AC10 已批准版本不可覆盖",
                field_path="drawing_version",
                required_evidence="图纸版本号（来源：客户图纸/设计记录）",
            )
        )

    change_level = _text(task.get("engineering_change_level"))
    change_records = _by_dataset(inputs, ppap_template.DS_CHANGE_RECORD)
    if change_records and not change_level:
        findings.append(
            _finding(
                check_id,
                scope="change_level",
                kind=KIND_MISSING,
                title="存在工程变更记录但未登记工程变更等级",
                detail=(
                    f"已录入 {len(change_records)} 条工程变更记录，但任务未登记 "
                    "engineering_change_level。"
                ),
                basis="工程变更等级影响提交等级（设计说明 §5.1）",
                field_path="engineering_change_level",
                required_evidence="工程变更等级或其不适用依据",
            )
        )

    if task_version:
        for record in evidence:
            if record.get("source_type") != ppap_template.EV_USER_UPLOAD:
                continue
            source_version = _text(record.get("source_version"))
            element_ids = _as_list(record.get("element_ids")) or _as_list(
                _as_dict(record).get("element_ids_json")
            )
            if "design_records" not in [str(x) for x in element_ids]:
                continue
            design = _elements_by_id(_as_list(context.get("elements"))).get("design_records", {})
            if design.get("requirement") in (REQ_NOT_APPLICABLE, REQ_ON_REQUEST, REQ_UNCONFIRMED):
                continue
            if not source_version:
                findings.append(_finding(check_id, scope=f"evidence:{record.get('evidence_id')}:version_missing",
                    kind=KIND_MISSING, title="设计原件未确认文档版本",
                    detail="文件内容指纹不能替代图纸版本，需明确登记原件版次。",
                    basis="AC10：设计文件与任务图纸版本一致且有明确依据", element_id="design_records",
                    field_path="source_version", required_evidence="原件中明确的图纸/文档版次"))
                continue
            if source_version and source_version != task_version:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"evidence:{_text(record.get('source_id')) or record.get('dedup_key')}",
                        kind=KIND_INCONSISTENCY,
                        title="设计记录证据版本与任务图纸版本不一致",
                        detail=(
                            f"证据来源版本为 {source_version}，任务图纸版本为 {task_version}。"
                        ),
                        basis="提交内容必须对应同一版本（AC10）",
                        element_id="design_records",
                        field_path="source_version",
                        required_evidence="与任务图纸版本一致的证据",
                    )
                )
    return findings


def check_evidence(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-EVID-001"
    elements = _as_list(context.get("elements"))
    evidence = [dict(item) for item in _as_list(context.get("evidence"))]
    findings: list[dict[str, Any]] = []

    usable: dict[str, list[dict[str, Any]]] = {}
    problems: dict[str, list[dict[str, Any]]] = {}
    for record in evidence:
        source_type = _text(record.get("source_type"))
        read_status = _text(record.get("read_status")) or "OK"
        bucket = usable if read_status == "OK" else problems
        bucket.setdefault(source_type, []).append(record)

    for item in elements:
        element = _as_dict(item)
        spec = _spec(element)
        if not spec or not spec.evidence_types:
            continue
        if _element_status(element) == EL_NOT_APPLICABLE:
            continue
        requirement = _text(element.get("requirement"))
        if requirement in (REQ_NOT_APPLICABLE, REQ_ON_REQUEST, REQ_UNCONFIRMED):
            # 不适用条目无需证据；``on_request`` 语义是"客户索取时才提交"，默认不索证，
            # 客户确实索取时应把适用性改为 submit 并据此取证；
            # ``REQ_UNCONFIRMED`` 已由 PPAP-CHK-REQ-001 单独阻断，此处不重复报缺项。
            # ``retain`` 与 ``submit`` 仍要求来源证据：内部批准前提是留存资料可追溯。
            continue
        for source_type in spec.evidence_types:
            def scoped(record):
                bindings = record.get("element_ids") or record.get("element_ids_json") or []
                return spec.element_id in bindings or (not bindings and source_type not in (
                    ppap_template.EV_USER_UPLOAD, ppap_template.EV_INTERNAL_PACKAGE))
            ok_records = [
                record
                for record in usable.get(source_type, [])
                if not record.get("truncated") and scoped(record)
            ]
            if ok_records:
                continue
            broken = [record for record in problems.get(source_type, []) if scoped(record)] or [
                record for record in usable.get(source_type, []) if record.get("truncated") and scoped(record)
            ]
            if broken:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{spec.element_id}:{source_type}:unavailable",
                        kind=KIND_MISSING,
                        title=f"{spec.title}的来源 {source_type} 取证未成功",
                        detail=(
                            f"来源类型 {source_type} 的取证记录存在但不可用"
                            f"（read_status={_text(broken[0].get('read_status')) or 'OK'}，"
                            f"truncated={bool(broken[0].get('truncated'))}）："
                            f"{_text(broken[0].get('summary'))}"
                        ),
                        basis="取证失败或分页截断不得当作「无此项」（设计说明 §7）",
                        element_id=spec.element_id,
                        required_evidence=f"{source_type} 的完整可用结果",
                    )
                )
            else:
                findings.append(
                    _finding(
                        check_id,
                        scope=f"{spec.element_id}:{source_type}:missing",
                        kind=KIND_MISSING,
                        title=f"{spec.title}缺少来源证据",
                        detail=(
                            f"条目 {spec.element_id} 需要 {source_type} 证据，但没有任何已取证记录。"
                        ),
                        basis="每条内容都必须有可追溯来源（设计说明 §7）",
                        element_id=spec.element_id,
                        required_evidence=f"{source_type} 证据",
                    )
                )
    return findings


def check_template(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-TPL-001"
    task = _as_dict(context.get("task"))
    findings: list[dict[str, Any]] = []
    template_id = _text(task.get("template_id"))
    template_version = _text(task.get("template_version"))
    if template_id and template_id != ppap_template.TEMPLATE_ID:
        findings.append(
            _finding(
                check_id,
                scope="template_id",
                kind=KIND_TEMPLATE,
                title="任务使用了非当前条目模板",
                detail=(
                    f"任务模板为 {template_id}，当前项目模板为 {ppap_template.TEMPLATE_ID}。"
                    "若客户模板尚未取得，请勿声称使用客户模板。"
                ),
                basis="设计说明 §6.1：客户模板不可得时使用 project_defined 条目模板",
                field_path="template_id",
            )
        )
    if template_version and template_version != ppap_template.TEMPLATE_VERSION:
        findings.append(
            _finding(
                check_id,
                scope="template_version",
                kind=KIND_TEMPLATE,
                title="任务模板版本与当前模板版本不同",
                detail=(
                    f"任务模板版本 {template_version}，当前 {ppap_template.TEMPLATE_VERSION}；"
                    "导出与审核需按任务登记的模板版本进行。"
                ),
                basis="版本可追溯（AC10）",
                field_path="template_version",
            )
        )
    return findings


def check_requirements(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    check_id = "PPAP-CHK-REQ-001"
    findings: list[dict[str, Any]] = []
    for item in _as_list(context.get("elements")):
        element = _as_dict(item)
        spec = _spec(element)
        if not spec:
            continue
        requirement = _text(element.get("requirement"))
        status = _element_status(element)
        if requirement == REQ_UNCONFIRMED:
            findings.append(
                _finding(
                    check_id,
                    scope=f"{spec.element_id}:applicability",
                    kind=KIND_APPLICABILITY,
                    title=f"{spec.title}的适用性未确认",
                    detail=(
                        f"条目 {spec.element_id} 的提交要求尚未确认。"
                        f"建议值：{spec.suggested_requirement or '需人工判断'}；"
                        f"判定条件：{spec.applicability_condition or '无附加条件'}。"
                        "必须由业务方确认提交/保留/按需/不适用，并记录依据。"
                    ),
                    basis="AC09：未确认适用性的条目阻止批准",
                    element_id=spec.element_id,
                    field_path="requirement",
                    required_evidence="适用性确认（含依据与确认人）",
                    responsible_role=spec.responsible_role,
                )
            )
            continue
        if requirement == REQ_SUBMIT and status in UNMET_ELEMENT_STATES:
            findings.append(
                _finding(
                    check_id,
                    scope=f"{spec.element_id}:unmet",
                    kind=KIND_MISSING,
                    title=f"{spec.title}未达提交状态",
                    detail=(
                        f"条目要求提交，当前状态为 {status}："
                        f"{_text(element.get('status_reason')) or '缺少数据或存在冲突'}。"
                    ),
                    basis="AC09：提交级条目未完成时阻止批准",
                    element_id=spec.element_id,
                    field_path="status",
                    required_evidence="补齐数据并完成编制",
                    responsible_role=spec.responsible_role,
                )
            )
    return findings


CHECK_RUNNERS = {
    "PPAP-CHK-BASE-001": check_base_info,
    "PPAP-CHK-SIDE-001": check_part_side,
    "PPAP-CHK-MAT-001": check_material_evidence,
    "PPAP-CHK-OP-001": check_process_operations,
    "PPAP-CHK-CHAR-001": check_characteristics,
    "PPAP-CHK-UNIT-001": check_units,
    "PPAP-CHK-MSA-001": check_msa,
    "PPAP-CHK-STUDY-001": check_process_study,
    "PPAP-CHK-VER-001": check_versions,
    "PPAP-CHK-EVID-001": check_evidence,
    "PPAP-CHK-TPL-001": check_template,
    "PPAP-CHK-REQ-001": check_requirements,
}


def run_all_checks(context: Mapping[str, Any]) -> dict[str, Any]:
    """执行全部检查，返回检查清单与发现（不写库）。"""
    checks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for check_id, severity, title, role in CHECK_DEFINITIONS:
        runner = CHECK_RUNNERS[check_id]
        produced = runner(context) or []
        checks.append(
            {
                "check_id": check_id,
                "title": title,
                "severity": severity,
                "responsible_role": role,
                "status": "FAIL" if produced else "PASS",
                "finding_count": len(produced),
            }
        )
        findings.extend(produced)

    summary = {
        "check_count": len(checks),
        "checks_failed": sum(1 for c in checks if c["status"] == "FAIL"),
        "finding_count": len(findings),
        "blocker_count": sum(1 for f in findings if f["severity"] == SEV_BLOCKER),
        "major_count": sum(1 for f in findings if f["severity"] == SEV_MAJOR),
        "info_count": sum(1 for f in findings if f["severity"] == SEV_INFO),
    }
    summary["blocks_approval"] = summary["blocker_count"] > 0
    return {"checks": checks, "findings": findings, "summary": summary}


def blocking_findings(findings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in findings if bool(_as_dict(item).get("blocks_approval"))]


def check_id_of(issue: Mapping[str, Any]) -> str:
    return _text(_as_dict(issue).get("check_id"))
