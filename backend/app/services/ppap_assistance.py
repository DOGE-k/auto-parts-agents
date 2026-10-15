"""Read-only PPAP input suggestions, navigation, and frozen-source comparison.

This module performs no writes or upstream requests. Suggestions reuse confirmed
identifiers and definitions only; measurements, study methods, acceptance rules,
signatures and conclusions are never generated or copied into a new record.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from app.services import ppap_characteristics, ppap_inputs, ppap_mode, ppap_template


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _source(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: record.get(key, "") for key in
            ("input_id", "record_key", "part_side", "source_type", "source_ref", "confirmed_by", "confirmed_at")}


def _invalid_specification(definition: Mapping[str, Any]) -> list[str]:
    invalid = []
    for field in ("lsl", "usl", "nominal", "target"):
        value = definition.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            invalid.append(field)
    if not invalid and definition.get("lsl") is not None and definition.get("usl") is not None:
        if definition["lsl"] >= definition["usl"]:
            invalid.extend(("lsl", "usl"))
    unit = definition.get("unit")
    if unit is not None and not isinstance(unit, str):
        invalid.append("unit")
    return invalid


def build_input_assistance(task: Mapping[str, Any], records: Sequence[Mapping[str, Any]], *,
                           dataset: str, part_side: str = "", characteristic_id: str = "",
                           record_key: str = "") -> dict[str, Any]:
    """Suggest only missing fields with explicit provenance, without applying them."""
    contract = ppap_inputs.dataset_contract(dataset)
    if not contract:
        return {"ok": False, "code": "unknown_dataset", "error": "未知输入数据集", "read_only": True}
    side, error = ppap_inputs.normalize_part_side(part_side or task.get("part_side"))
    if error:
        return {"ok": False, "code": "invalid_part_side", "error": error, "read_only": True}
    mode = ppap_mode.task_mode(task)
    result: dict[str, Any] = {"ok": True, "read_only": True, "task_id": task.get("task_id"),
        "data_mode": mode, "dataset": dataset, "part_side": side, "suggestions": [],
        "candidates": [], "conflicts": [], "notices": [], "existing_input": None,
        "explicitly_empty_fields": []}
    confirmed = [dict(record) for record in records
                 if record.get("task_id") == task.get("task_id") and record.get("confirmed_by")
                 and record.get("confirmed_at") and record.get("source_ref")]
    for record in confirmed:
        problem = ppap_mode.validate_input_source(mode, record.get("source_type"),
                                                  record.get("source_ref"), record.get("payload"))
        if problem:
            return {**problem, "read_only": True}
    existing = [record for record in confirmed if record.get("dataset") == dataset
                and record.get("record_key") == record_key and record.get("part_side") == side] if record_key else []
    if len(existing) > 1:
        result["conflicts"].append({"code": "ambiguous_revision_record", "record_key": record_key,
                                    "message": "修订记录不唯一，请核对记录键和侧别"})
        return result
    original = deepcopy(existing[0]) if existing else None
    payload = _dict(original.get("payload")) if original else {}
    result["existing_input"] = original
    result["explicitly_empty_fields"] = [key for key, value in payload.items() if value is None]
    if record_key and not original:
        result["notices"].append("没有同数据集、同记录键、同侧别的已确认记录；不会载入其他记录。")

    def suggest(field: str, value: Any, source: Mapping[str, Any], origin: str) -> None:
        # Existing values, including explicit null, are user-owned. Never replace
        # them when loading assistance for a revision.
        if field in payload or value is None or value == "":
            return
        result["suggestions"].append({"field": field, "value": deepcopy(value), "origin": origin,
                                     **_source(source)})

    if dataset == "product_base":
        metadata_date = task.get("updated_at") or task.get("created_at")
        if task.get("authorized_by") and metadata_date:
            task_source = {"source_type": "synthetic_demo_only" if mode == "TEST" else "user_confirmation",
                "source_ref": f"PPAP:{task.get('task_id')}:metadata@{metadata_date}",
                "confirmed_by": task.get("authorized_by"), "confirmed_at": task.get("updated_at")}
            for field in ("part_no", "part_name", "customer_name", "drawing_version", "engineering_change_level"):
                suggest(field, task.get(field), task_source, "task_metadata")
            result["notices"].append("基础信息来自本任务已登记元数据，来源引用不是外部原件；保存前仍需确认。")
        else:
            result["notices"].append("任务缺少确认身份或元数据版本日期，不能提供已确认基础信息建议。")
        return result
    if dataset not in ("inspection_value", "msa_study", "process_study"):
        result["notices"].append("此数据集没有已核对的自动填充契约，请录入实际资料。")
        return result
    if side not in ("LEFT", "RIGHT", "BOTH"):
        result["notices"].append("请先选择明确侧别，不能从其他侧别自动识别特性。")
        return result
    chars = [record for record in confirmed if record.get("dataset") == "characteristic"
             and record.get("part_side") == side]
    groups = ppap_characteristics.group_characteristics(chars)
    for group in groups:
        for record in group["records"]:
            definition = _dict(record.get("payload"))
            candidate = {"characteristic_id": group["characteristic_id"], "name": definition.get("name", ""),
                         "part_side": side, **_source(record),
                         "conflict": not group["usable"] or bool(_invalid_specification(definition))}
            result["candidates"].append(candidate)
        if not group["usable"]:
            result["conflicts"].append({"code": "characteristic_natural_key_conflict",
                "characteristic_id": group["characteristic_id"], "part_side": side,
                "sources": group["sources"], "conflict_fields": group["conflict_fields"],
                "message": "同侧特性定义重复或冲突，不能自动选择或填充规格"})
        elif _invalid_specification(_dict(group["records"][0].get("payload"))):
            result["conflicts"].append({"code": "invalid_characteristic_specification",
                "characteristic_id": group["characteristic_id"], "part_side": side,
                "sources": group["sources"],
                "conflict_fields": _invalid_specification(_dict(group["records"][0].get("payload"))),
                "message": "特性边界或单位无效，不能将矛盾规格作为预填建议；请核对原记录"})
    selected = _text(characteristic_id)
    if not selected and "characteristic_id" in payload:
        selected = _text(payload["characteristic_id"])
        if not selected:
            result["notices"].append("原记录已显式提供空特性编号，不自动替换。")
            return result
    if not selected:
        if len(groups) == 1 and groups[0]["usable"]:
            selected = groups[0]["characteristic_id"]
        elif groups:
            result["notices"].append("存在多个候选或重复定义，请明确选择特性后识别。")
            return result
    matched = [group for group in groups if group["characteristic_id"] == selected]
    if not matched or not matched[0]["usable"]:
        result["notices"].append("没有唯一、已确认且同侧的特性定义，未提供规格建议。")
        return result
    record = matched[0]["records"][0]
    definition = _dict(record.get("payload"))
    suggest("characteristic_id", selected, record, "confirmed_characteristic")
    if _invalid_specification(definition):
        result["notices"].append("特性编号可供定位；规格或单位无效，不提供任何单位或规格预填。")
        return result
    fields = {"inspection_value": ("unit", "lsl", "usl", "nominal"),
              "msa_study": ("unit",), "process_study": ("unit", "lsl", "usl", "target")}[dataset]
    for field in fields:
        if field in definition:
            suggest(field, definition[field], record, "confirmed_characteristic")
    for field in fields:
        if field in payload and field in definition and payload[field] != definition[field]:
            result["conflicts"].append({"code": "existing_specification_conflict", "field": field,
                "old_value": payload[field], "reference_value": definition[field], "sources": [_source(record)],
                "message": "原记录明确值与当前特性定义不一致，保持原值待人工核对"})
    result["notices"].append("仅识别已确认的编号、单位和规格；不复用历史测量、样本、方法、阈值或结论。")
    return result


_CHECK_DATASETS = {"PPAP-CHK-BASE-001": "product_base", "PPAP-CHK-MAT-001": "material_evidence",
    "PPAP-CHK-OP-001": "process_operation", "PPAP-CHK-CHAR-001": "characteristic",
    "PPAP-CHK-MSA-001": "msa_study", "PPAP-CHK-STUDY-001": "process_study"}


def finding_navigation(finding: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Map only established checker scopes/fields to known input locations."""
    check, element, field = (_text(finding.get(key)) for key in ("check_id", "element_id", "field_path"))
    scope = _text(finding.get("dedup_key")).removeprefix(check + ":")
    navigation: dict[str, Any] = {"kind": "manual", "element_id": element,
        "reason": "没有唯一的已知补录位置，请按发现说明核对来源。"}
    if finding.get("kind") == "RULE_UNCONFIRMED":
        return {**navigation, "kind": "rules", "reason": "需要确认计算方法或验收规则，不能用默认阈值补齐。"}
    if check == "PPAP-CHK-REQ-001":
        if field == "requirement":
            return {**navigation, "kind": "applicability", "field": field, "reason": "确认此条目的适用性及依据。"}
        spec = ppap_template.element_spec(element)
        if spec and len(spec.required_inputs) == 1:
            dataset = spec.required_inputs[0]
        elif spec and spec.evidence_types and not spec.required_inputs:
            return {**navigation, "kind": "evidence", "reason": "核对此条目要求的原件或已取证资料。"}
        else:
            return navigation
    elif check == "PPAP-CHK-EVID-001" or (check == "PPAP-CHK-VER-001" and scope.startswith("evidence:")):
        segments = scope.split(":")
        source_type = segments[1] if len(segments) > 1 and segments[1] in ppap_mode.KNOWN_SOURCE_TYPES else ""
        return {**navigation, "kind": "evidence", "source_type": source_type,
                "field": field, "reason": "核对完整可用证据；读取失败不能作为无此项。"}
    elif check == "PPAP-CHK-VER-001" and field in ("drawing_version", "engineering_change_level"):
        return {**navigation, "kind": "task", "field": field, "reason": "补充任务的明确图纸版本或工程变更等级。"}
    else:
        segments = scope.split(":")
        dataset = segments[0] if segments[0] in ppap_template.ALL_DATASETS else _CHECK_DATASETS.get(check, "")
        if check == "PPAP-CHK-UNIT-001" and (scope.startswith("char:") or scope.endswith("characteristic_unit_missing")):
            dataset = "characteristic"
        if check == "PPAP-CHK-CHAR-001" and scope.startswith("measurement:"):
            dataset = "inspection_value"
        if not dataset:
            return navigation
    contract = ppap_inputs.dataset_contract(dataset) or {}
    known_fields = set(contract.get("required", ())) | set(contract.get("optional", ()))
    navigation.update(kind="input", dataset=dataset, field=field if field in known_fields or field == "part_side" else "",
                      reason="补充此数据集的已确认资料；保留原记录键修订，避免另建重复记录。")
    # Checker scopes use stable identifiers. Do not parse free descriptions or
    # use substring matches that could attach a finding to the wrong record.
    records = [dict(record) for record in inputs if record.get("dataset") == dataset]
    def known_scopes(record: Mapping[str, Any]) -> set[str]:
        ds, key, side = (_text(record.get(name)) for name in ("dataset", "record_key", "part_side"))
        char = _text(_dict(record.get("payload")).get("characteristic_id"))
        scopes = {f"{ds}:{key}", f"{ds}:{char}:{side}", f"char:{char}:{side}:unit_conflict",
                  f"measurement:{record.get('input_id') or key}:{char}:{side}"}
        scopes.update(f"{ds}:{key}:{side}:{suffix}" for suffix in
                      ("unit_missing", "unit_mismatch", "characteristic_unit_missing"))
        if ds == "product_base":
            for name in known_fields:
                scopes.update((f"{key}:{name}", f"{key}:{name}:empty"))
        return scopes
    exact = [record for record in records if scope in known_scopes(record)]
    if not exact and dataset == "characteristic" and scope.endswith("characteristic_unit_missing"):
        referenced = [record for record in inputs if scope in known_scopes(record)]
        if len(referenced) == 1:
            char = _dict(referenced[0].get("payload")).get("characteristic_id")
            exact = [record for record in records if _dict(record.get("payload")).get("characteristic_id") == char
                     and record.get("part_side") == referenced[0].get("part_side")]
    if not exact:
        source_ids = {record.get("input_id") for record in finding.get("sources") or [] if record.get("input_id")}
        exact = [record for record in records if record.get("input_id") in source_ids]
    if len(exact) == 1:
        navigation.update({key: exact[0].get(key, "") for key in ("input_id", "record_key", "part_side")})
    return navigation


def _same(left: Any, right: Any) -> bool:
    # bool is not an integer input in PPAP; preserve that distinction here too.
    return type(left) is type(right) and left == right


def _change(section: str, field: str, old: Mapping[str, Any], new: Mapping[str, Any], *,
            metadata: Mapping[str, Any] | None = None, old_source: Mapping[str, Any] | None = None,
            new_source: Mapping[str, Any] | None = None, old_available: bool = True) -> dict[str, Any] | None:
    old_present, new_present = field in old, field in new
    if old_available and old_present == new_present and (not old_present or _same(old[field], new[field])):
        return None
    return {"section": section, **dict(metadata or {}), "field_path": field,
        "old_present": old_present if old_available else None, "new_present": new_present,
        "old_value": deepcopy(old.get(field)) if old_available else None, "new_value": deepcopy(new.get(field)),
        "old_available": old_available, "new_available": True,
        "old_source": dict(old_source or {}), "new_source": dict(new_source or {}),
        "change_type": "unavailable" if not old_available else "added" if not old_present else "removed" if not new_present else "modified"}


def _snapshot_inputs(snapshot: Mapping[str, Any], current_inputs: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Resolve frozen payloads by recorded identity, never by coincidental hash."""
    provenance = _dict(snapshot.get("provenance")).get("inputs") or []
    by_id = {record.get("input_id"): record for record in current_inputs}
    identities: dict[tuple[str, str, str], dict[str, Any]] = {}
    for original in provenance:
        record = _dict(original)
        identity = record if all(key in record for key in ("dataset", "record_key", "part_side")) else by_id.get(record.get("input_id"), {})
        if identity:
            key = tuple(str(identity.get(field, "")) for field in ("dataset", "record_key", "part_side"))
            identities[key] = record
    output = {}
    for original in snapshot.get("inputs") or []:
        record = _dict(original)
        key = tuple(str(record.get(field, "")) for field in ("dataset", "record_key", "part_side"))
        frozen = identities.get(key, {})
        # Legacy metadata can be resolved by stable input_id, but the frozen
        # payload must still agree with the original snapshot fingerprint.
        available = isinstance(frozen.get("payload"), Mapping)
        if available:
            from app.services import ppap_storage
            available = ppap_storage.sha256_json(frozen["payload"]) == record.get("payload_hash")
        output[key] = {**record, **frozen, "payload_available": available}
    return output


def source_changes(previous: Mapping[str, Any], current: Mapping[str, Any],
                   current_inputs: Sequence[Mapping[str, Any]],
                   current_evidence: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Explain changes from a frozen version. Arrays remain original arrays."""
    changes: list[dict[str, Any]] = []
    for section in ("task", "business_object", "template"):
        old, new = _dict(previous.get(section)), _dict(current.get(section))
        for field in sorted(set(old) | set(new)):
            item = _change(section, field, old, new)
            if item:
                changes.append(item)
    old_inputs = _snapshot_inputs(previous, current_inputs)
    new_inputs = {(str(record.get("dataset", "")), str(record.get("record_key", "")), str(record.get("part_side", ""))): dict(record)
                  for record in current_inputs}
    for key in sorted(set(old_inputs) | set(new_inputs)):
        old, new = old_inputs.get(key, {}), new_inputs.get(key, {})
        metadata = dict(zip(("dataset", "record_key", "part_side"), key))
        metadata["input_id"] = new.get("input_id", old.get("input_id", ""))
        available = not old or bool(old.get("payload_available"))
        before, after = _dict(old.get("payload")), _dict(new.get("payload"))
        if not available:
            # Fingerprint equality proves unchanged content, not the value of
            # historical content. Emit an explicit unavailable record only when
            # changed or removed; don't invent a per-field old value.
            from app.services import ppap_storage
            if not new or old.get("payload_hash") != ppap_storage.sha256_json(after):
                changes.append({**_change("inputs", "payload", {}, {"payload": after} if new else {},
                    metadata=metadata, old_source=_source(old), new_source=_source(new), old_available=False),
                    "availability_reason": "历史版本未保存可唯一关联的完整输入，无法恢复旧字段值。"})
        else:
            for field in sorted(set(before) | set(after)):
                item = _change("inputs", field, before, after, metadata=metadata,
                               old_source=_source(old), new_source=_source(new))
                if item:
                    item["field_path"] = "payload." + field
                    changes.append(item)
        for field in ("source_type", "source_ref", "confirmed_by", "confirmed_at"):
            if not old and not new:
                continue
            # A legacy snapshot that omitted provenance must not suggest it was
            # blank. Unknown old source is explicitly unavailable.
            old_known = not old or field in old
            item = _change("inputs", field, old, new, metadata=metadata,
                           old_source=_source(old), new_source=_source(new), old_available=old_known)
            if item:
                changes.append(item)
    old_evidence = {(str(record.get("source_type", "")), str(record.get("source_id", ""))): _dict(record)
                    for record in previous.get("evidence") or []}
    frozen_ev = _dict(previous.get("provenance")).get("evidence") or []
    for record in frozen_ev:
        record = _dict(record)
        if "source_type" in record and "source_id" in record:
            key = (str(record["source_type"]), str(record["source_id"]))
            old_evidence[key] = {**old_evidence.get(key, {}), **record}
    new_evidence = {(str(record.get("source_type", "")), str(record.get("source_id", ""))): dict(record)
                    for record in current_evidence}
    for key in sorted(set(old_evidence) | set(new_evidence)):
        old, new = old_evidence.get(key, {}), new_evidence.get(key, {})
        metadata = {"source_type": key[0], "source_id": key[1], "evidence_id": new.get("evidence_id", old.get("evidence_id", ""))}
        for field in ("source_version", "read_status", "truncated", "content_hash", "locator", "element_ids", "payload"):
            item = _change("evidence", field, old, new, metadata=metadata, old_source=metadata,
                           new_source=metadata, old_available=not old or field in old)
            if item:
                changes.append(item)
    old_elements = {record.get("element_id"): record for record in previous.get("elements") or []}
    for new in current.get("elements") or []:
        old = old_elements.get(new.get("element_id"), {})
        for field in ("requirement", "applicability_basis", "content_hash"):
            item = _change("elements", field, old, new, metadata={"element_id": new.get("element_id")})
            if item:
                changes.append(item)
    return changes


def affected_elements(changes: Sequence[Mapping[str, Any]], elements: Sequence[Mapping[str, Any]],
                      evidence: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
    """Use declared dependencies; broad changes are conservatively explained."""
    active = {record.get("element_id") for record in elements if record.get("requirement") != "not_applicable"}
    reasons: dict[str, set[str]] = {}
    def add(ids: Sequence[str] | set[str], reason: str) -> None:
        for element in set(ids) & active:
            reasons.setdefault(str(element), set()).add(reason)
    for change in changes:
        section = change.get("section")
        if section == "inputs":
            dataset = str(change.get("dataset", ""))
            targets = [spec.element_id for spec in ppap_template.ELEMENTS if dataset in spec.required_inputs]
            if targets:
                add(targets, f"依赖数据集 {dataset} 的输入或来源发生变化")
            else:
                add(active, f"数据集 {dataset or '[未知]'} 未有唯一依赖映射，保守要求核对全部适用条目")
            add(["submission_checklist", "attachment_index"], "来源索引或提交完整性需重新核对")
        elif section == "evidence":
            source = change.get("source_type")
            binding = change.get("new_value") if change.get("field_path") == "element_ids" else None
            explicit = set(binding or []) | set(change.get("old_value") or []) if change.get("field_path") == "element_ids" else set()
            for record in evidence:
                if record.get("source_type") == source and record.get("source_id") == change.get("source_id"):
                    explicit.update(record.get("element_ids") or [])
            targets = explicit or {spec.element_id for spec in ppap_template.ELEMENTS if source in spec.evidence_types}
            add(targets or active, "已绑定证据或声明的证据来源变化，需核对资料内容与可用性")
            add(["submission_checklist", "attachment_index"], "证据来源索引及完整性需重新核对")
        elif section == "elements" and change.get("element_id"):
            add([str(change["element_id"])], "本条目的适用性、依据或编制内容发生变化")
            add(["submission_checklist", "attachment_index"], "条目提交清单及索引需重新核对")
        else:
            add(active, f"{section or '未知来源'} 变化影响整体编制口径，保守要求核对全部适用条目")
    return sorted(reasons), [{"element_id": key, "reason": "；".join(sorted(reasons[key]))} for key in sorted(reasons)]
