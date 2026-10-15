"""Pure PPAP characteristic resolution; ambiguous definitions are never selected.

Accepts either serialized inputs (``payload`` / ``payload_json``) or flattened
draft records. Only fields in the existing input contract are compared. Missing
fields and explicit null are distinct; units are never converted implicitly.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import math
from typing import Any, Iterable, Mapping


DEFINITION_FIELDS = (
    "name", "characteristic_type", "special", "unit", "usl", "lsl",
    "nominal", "target", "drawing_location",
)
SPECIFICATION_FIELDS = ("usl", "lsl", "nominal")
VALID_SIDES = ("LEFT", "RIGHT", "BOTH")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def payload_of(record: Mapping[str, Any]) -> dict[str, Any]:
    for field in ("payload", "payload_json"):
        if field in record:
            value = record[field]
            return deepcopy(dict(value)) if isinstance(value, Mapping) else {}
    return deepcopy(dict(record))


def characteristic_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    payload = payload_of(record)
    return (_text(record.get("task_id")), _text(payload.get("characteristic_id")),
            _text(record.get("part_side")).upper())


def _normalized_value(payload: Mapping[str, Any], field: str) -> tuple[str, Any]:
    if field not in payload:
        return ("missing", None)
    value = payload[field]
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        # 10 and 10.0 are the same number; True is not the number 1.
        return ("number", Decimal(str(value)))
    if isinstance(value, str):
        return ("text", value.strip())
    return ("invalid", json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def definition_conflicts(records: Iterable[Mapping[str, Any]]) -> list[str]:
    payloads = [payload_of(record) for record in records]
    return [field for field in DEFINITION_FIELDS
            if len({_normalized_value(payload, field) for payload in payloads}) > 1]


def _source(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = payload_of(record)
    return {"task_id": _text(record.get("task_id")),
            "input_id": _text(record.get("input_id")),
            "record_key": _text(record.get("record_key")),
            "part_side": _text(record.get("part_side")).upper(),
            "source_type": _text(record.get("source_type")),
            "source_ref": _text(record.get("source_ref")),
            "confirmed_by": _text(record.get("confirmed_by")),
            "definition": {field: deepcopy(payload[field]) for field in DEFINITION_FIELDS if field in payload},
            "missing_fields": [field for field in DEFINITION_FIELDS if field not in payload]}


def _stable(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)


def group_characteristics(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group by task/id/side, retaining *every* source in deterministic order."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if "dataset" in record and _text(record["dataset"]) != "characteristic":
            continue
        key = characteristic_key(record)
        if not key[1]:
            continue
        grouped.setdefault(key, []).append(deepcopy(dict(record)))
    groups = []
    for (task_id, char_id, side), members in sorted(grouped.items()):
        members.sort(key=_stable)
        conflicts = definition_conflicts(members)
        groups.append({"task_id": task_id, "characteristic_id": char_id,
                       "part_side": side, "records": members,
                       "sources": [_source(record) for record in members],
                       "conflict_fields": conflicts, "duplicate": len(members) > 1,
                       "usable": len(members) == 1 and side in VALID_SIDES})
    return groups


def describe_sources(sources: Iterable[Mapping[str, Any]]) -> str:
    """All source definitions, including missing fields; no truncation/selection."""
    descriptions = []
    for source in sources:
        definition = source.get("definition", {})
        missing = source.get("missing_fields", [])
        descriptions.append(
            f"record_key={source.get('record_key') or '[未提供]'}，"
            f"input_id={source.get('input_id') or '[未提供]'}，"
            f"side={source.get('part_side') or '[未提供]'}，"
            f"source_type={source.get('source_type') or '[未提供]'}，"
            f"source_ref={source.get('source_ref') or '[未提供]'}，"
            f"confirmed_by={source.get('confirmed_by') or '[未提供]'}，"
            f"定义={json.dumps(definition, ensure_ascii=False, sort_keys=True)}，"
            f"未提供字段={list(missing)}")
    return "；".join(descriptions)


def resolve_characteristic(records: Iterable[Mapping[str, Any]], *,
                           characteristic_id: str, part_side: str,
                           task_id: str = "") -> dict[str, Any]:
    """Return a definition only for one same-side record; duplicates are unsafe."""
    char_id, side = _text(characteristic_id), _text(part_side).upper()
    groups = [group for group in group_characteristics(records)
              if group["characteristic_id"] == char_id
              and (not task_id or not group["task_id"] or group["task_id"] == task_id)]
    matching = [group for group in groups if group["part_side"] == side]
    members = [record for group in matching for record in group["records"]]
    sources = [source for group in matching for source in group["sources"]]
    conflicts = definition_conflicts(members)
    code = ""
    if side not in VALID_SIDES:
        code = "characteristic_side_missing"
    elif not matching:
        code = "characteristic_side_conflict" if groups else "characteristic_missing"
        sources = [source for group in groups for source in group["sources"]]
    elif len(members) > 1:
        code = "characteristic_definition_conflict" if conflicts else "characteristic_duplicate"
    return {"ok": not code, "code": code,
            "characteristic": payload_of(members[0]) if not code else None,
            "sources": sources, "conflict_fields": conflicts,
            "characteristic_id": char_id, "part_side": side}


def validate_characteristic_upsert(existing_records: Iterable[Mapping[str, Any]],
                                   candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Guard natural key before a write; the existing record key may be revised.

    This does not write or validate the input schema. Call after contract/side
    validation, inside the service's database transaction, before adding a row.
    A legacy alias still blocks insertion/revision until explicitly resolved.
    """
    task_id, char_id, side = characteristic_key(candidate)
    key = _text(candidate.get("record_key"))
    if not char_id or side not in VALID_SIDES or not key:
        return {"ok": False, "code": "characteristic_identity_missing", "sources": []}
    existing = [dict(record) for record in existing_records
                if isinstance(record, Mapping)
                and _text(record.get("dataset", "characteristic")) == "characteristic"]
    peers = [record for record in existing
             if characteristic_key(record) == (task_id, char_id, side)]
    aliases = [record for record in peers if _text(record.get("record_key")) != key]
    # A same-key row changing its characteristic id is not a peer; it may revise
    # into the new id only when no other row already occupies that natural key.
    if aliases or len(peers) > 1:
        relevant = sorted(peers, key=_stable)
        return {"ok": False, "code": "characteristic_natural_key_conflict",
                "characteristic_id": char_id, "part_side": side,
                "record_key": key, "sources": [_source(row) for row in relevant],
                "conflict_fields": definition_conflicts([*relevant, candidate])}
    return {"ok": True, "code": "", "sources": [_source(row) for row in peers],
            "characteristic_id": char_id, "part_side": side, "record_key": key}


def compare_reference_definition(record: Mapping[str, Any],
                                 definition: Mapping[str, Any]) -> list[str]:
    """Explicit limits cannot override a confirmed definition without review.

    A missing inspection limit may be filled from a unique characteristic. An
    explicitly supplied null means confirmed no value, so it cannot be filled.
    """
    payload = payload_of(record)
    return [field for field in SPECIFICATION_FIELDS
            if field in payload
            and _normalized_value(payload, field) != _normalized_value(definition, field)]


def prepare_dimensional_comparison(characteristics: Iterable[Mapping[str, Any]],
                                  inspections: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Prepare safe comparison rows and traceable conflicts, without calculating.

    Original measurements remain in the caller's raw rows. Unsafe comparison
    rows retain ``original_value`` and source definitions but have ``value=None``.
    """
    definitions = list(characteristics)
    rows, issues = [], []
    for record in inspections:
        row = payload_of(record)
        for field in ("task_id", "input_id", "record_key", "part_side", "source_ref", "confirmed_by"):
            if field in record:
                row[field] = deepcopy(record[field])
        resolution = resolve_characteristic(definitions,
            characteristic_id=_text(row.get("characteristic_id")),
            part_side=_text(row.get("part_side")), task_id=_text(row.get("task_id")))
        problem_fields = list(resolution["conflict_fields"])
        reasons = []
        spec = resolution["characteristic"] or {}
        if not resolution["ok"]:
            reasons.append(f"特性规格未唯一确认（{resolution['code']}）")
        else:
            problem_fields.extend(compare_reference_definition(row, spec))
            if problem_fields:
                reasons.append(f"检验记录与特性规格冲突：{problem_fields}")
            for field in SPECIFICATION_FIELDS:
                if field not in row and field in spec:
                    row[field] = deepcopy(spec[field])
            if not _text(spec.get("unit")) or not _text(row.get("unit")):
                reasons.append("特性或检验单位缺失，不能默认单位")
                problem_fields.append("unit")
            elif _text(row.get("unit")) != _text(spec.get("unit")):
                reasons.append("测量单位与特性不一致，未进行隐式换算")
                problem_fields.append("unit")
            if row.get("usl") is None and row.get("lsl") is None:
                reasons.append("特性及检验记录未提供可用的规格上下限")
                problem_fields.extend(["usl", "lsl"])
        row["spec_sources"] = resolution["sources"]
        row["spec_source_ref"] = resolution["sources"][0]["source_ref"] if resolution["ok"] else ""
        if reasons:
            row["original_value"] = deepcopy(row.get("value"))
            row["value"] = None
            source_detail = describe_sources(resolution["sources"])
            reading_detail = (
                f"检验来源 record_key={_text(row.get('record_key')) or '[未提供]'}，"
                f"input_id={_text(row.get('input_id')) or '[未提供]'}，"
                f"source_ref={_text(row.get('source_ref')) or '[未提供]'}，"
                f"实测值={row['original_value']!r}，unit={row.get('unit')!r}，"
                f"检验规格={{{', '.join(f'{field}: {row[field]!r}' for field in SPECIFICATION_FIELDS if field in row)}}}")
            row["missing_reason"] = "；".join(
                reasons + [reading_detail] + ([source_detail] if source_detail else []))
            issues.append({"characteristic_id": resolution["characteristic_id"],
                           "part_side": resolution["part_side"],
                           "record_key": _text(row.get("record_key")),
                           "input_id": _text(row.get("input_id")),
                           "source_ref": _text(row.get("source_ref")),
                           "code": resolution["code"] or "characteristic_measurement_conflict",
                           "conflict_fields": sorted(set(problem_fields)),
                           "detail": row["missing_reason"], "sources": resolution["sources"]})
        rows.append(row)
    issues.sort(key=_stable)
    return {"rows": rows, "problems": [issue["detail"] for issue in issues],
            "specification_conflicts": issues}
