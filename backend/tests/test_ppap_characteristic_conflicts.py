"""Pure fixture tests for ambiguous PPAP definitions; no service/database writes."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import pytest

from app.services import ppap_calc, ppap_characteristics as characteristics, ppap_checks


def characteristic(key="fixture-original", *, side="LEFT", task="FIXTURE-TASK", **fields):
    return {"task_id": task, "dataset": "characteristic", "record_key": key,
            "input_id": f"FIXTURE-{key}", "part_side": side,
            "source_ref": f"Explicit test fixture source: {key}",
            "source_type": "user_confirmation", "confirmed_by": "fixture-only",
            "payload": {"characteristic_id": "FIXTURE-C", "name": "Fixture dimension",
                        "characteristic_type": "product", "unit": "mm", "lsl": 0,
                        "usl": 10, "special": True, **fields}}


def inspection(*, side="LEFT", **fields):
    return {"task_id": "FIXTURE-TASK", "dataset": "inspection_value",
            "record_key": "fixture-reading", "input_id": "FIXTURE-READING", "part_side": side,
            "source_ref": "Explicit measurement fixture source",
            "payload": {"characteristic_id": "FIXTURE-C", "point": "FIXTURE-P",
                        "value": 25, "unit": "mm", **fields}}


def flattened(record):
    return {**deepcopy(record["payload"]),
            **{key: deepcopy(value) for key, value in record.items() if key not in ("payload", "dataset")}}


def test_conflicting_definitions_are_stable_under_reversed_input_order_and_keep_all_sources():
    records = [characteristic(), characteristic("fixture-alias", lsl=20, usl=30)]
    context = {"inputs": [*records, inspection()], "elements": []}
    findings = ppap_checks.check_characteristics(context)
    reversed_findings = ppap_checks.check_characteristics({**context, "inputs": list(reversed(context["inputs"]))})
    assert findings == reversed_findings
    definition_issue = next(row for row in findings if "规格未唯一确认" in row["title"])
    assert definition_issue["severity"] == "BLOCKER" and definition_issue["blocks_approval"]
    assert set(definition_issue["conflict_fields"]) == {"lsl", "usl"}
    assert len(definition_issue["sources"]) == 2
    for record in records:
        assert record["source_ref"] in definition_issue["detail"]
        assert record["record_key"] in definition_issue["detail"]


@pytest.mark.parametrize("flatten", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_measurement_25_never_becomes_ok_by_choosing_last_20_to_30_definition(flatten, reverse):
    records = [characteristic(), characteristic("fixture-alias", lsl=20, usl=30)]
    reading = inspection()
    if reverse:
        records.reverse()
    if flatten:
        records = [flattened(record) for record in records]
        reading = flattened(reading)
    prepared = characteristics.prepare_dimensional_comparison(records, [reading])
    result = ppap_calc.dimensional_results(prepared["rows"])
    assert result["rows"][0]["result"] == "MISSING"
    assert result["state"] != "OK" and result["summary"]["ok"] == 0
    assert prepared["rows"][0]["original_value"] == 25
    assert prepared["rows"][0]["spec_source_ref"] == ""
    assert len(prepared["rows"][0]["spec_sources"]) == 2
    assert prepared["specification_conflicts"][0]["code"] == "characteristic_definition_conflict"


def test_equal_duplicate_definitions_are_blocking_and_unusable():
    records = [characteristic(), characteristic("fixture-alias", usl=10.0, lsl=0.0)]
    grouped = characteristics.group_characteristics(records)
    assert grouped[0]["conflict_fields"] == [] and grouped[0]["duplicate"]
    resolution = characteristics.resolve_characteristic(records, characteristic_id="FIXTURE-C", part_side="LEFT")
    assert not resolution["ok"] and resolution["code"] == "characteristic_duplicate"
    assert resolution["characteristic"] is None
    findings = ppap_checks.check_characteristics({"inputs": records, "elements": []})
    assert ppap_checks.blocking_findings(findings)
    assert "即使定义相同" in findings[0]["detail"]


@pytest.mark.parametrize("field,value", [("usl", 30), ("lsl", None), ("nominal", 5),
                                          ("target", 6), ("unit", "cm"),
                                          ("special", False), ("characteristic_type", "process")])
def test_definition_conflict_fields_are_blockers(field, value):
    records = [characteristic(), characteristic("fixture-alias", **{field: value})]
    findings = ppap_checks.check_characteristics({"inputs": records, "elements": []})
    assert field in findings[0]["conflict_fields"]
    assert findings[0]["severity"] == "BLOCKER"


def test_missing_field_and_confirmed_null_are_distinct_and_visible():
    first, second = characteristic(), characteristic("fixture-alias", usl=None)
    del first["payload"]["usl"]
    group = characteristics.group_characteristics([first, second])[0]
    assert group["conflict_fields"] == ["usl"]
    assert any("usl" in source["missing_fields"] for source in group["sources"])
    assert any(source["definition"].get("usl", "absent") is None for source in group["sources"])


def test_same_record_key_allows_normal_definition_revision():
    original = characteristic()
    revision = characteristic(lsl=20, usl=30, special=False)
    before = deepcopy(original)
    outcome = characteristics.validate_characteristic_upsert([original], revision)
    assert outcome["ok"] and outcome["code"] == ""
    assert original == before


@pytest.mark.parametrize("fields", [{}, {"lsl": 20, "usl": 30}])
def test_new_alias_key_for_same_natural_key_is_rejected_even_with_equal_specs(fields):
    outcome = characteristics.validate_characteristic_upsert(
        [characteristic()], characteristic("fixture-alias", **fields))
    assert not outcome["ok"] and outcome["code"] == "characteristic_natural_key_conflict"
    assert len(outcome["sources"]) == 1


def test_revision_cannot_change_characteristic_id_to_another_rows_identity():
    old = characteristic(characteristic_id="FIXTURE-OLD")
    occupied = characteristic("fixture-occupied", characteristic_id="FIXTURE-NEW")
    revision = characteristic(characteristic_id="FIXTURE-NEW")
    assert not characteristics.validate_characteristic_upsert([old, occupied], revision)["ok"]
    assert characteristics.validate_characteristic_upsert([old], revision)["ok"]


def test_legacy_alias_still_blocks_revising_one_record_until_duplicates_resolved():
    existing = [characteristic(), characteristic("fixture-alias")]
    assert not characteristics.validate_characteristic_upsert(existing, characteristic(usl=30))["ok"]


def test_same_id_is_independent_per_side_and_task_without_cross_side_unit_conflict():
    left = characteristic()
    right = characteristic("fixture-right", side="RIGHT", lsl=20, usl=30, unit="cm")
    other_task = characteristic("fixture-task-2", task="FIXTURE-OTHER", lsl=40, usl=50)
    assert len(characteristics.group_characteristics([left, right, other_task])) == 3
    assert characteristics.validate_characteristic_upsert([left], right)["ok"]
    assert characteristics.validate_characteristic_upsert([left], other_task)["ok"]
    left_reading, right_reading = inspection(value=5), inspection(side="RIGHT", unit="cm")
    prepared = characteristics.prepare_dimensional_comparison([left, right], [left_reading, right_reading])
    assert prepared["specification_conflicts"] == []
    assert [row["usl"] for row in prepared["rows"]] == [10, 30]
    assert ppap_checks.check_units({"inputs": [left, right, left_reading, right_reading]}) == []


@pytest.mark.parametrize("side", ["RIGHT", "", "NONE"])
def test_unmatched_or_missing_side_cannot_use_left_definition(side):
    reading = inspection(side=side, usl=30, lsl=20)
    prepared = characteristics.prepare_dimensional_comparison([characteristic()], [reading])
    assert prepared["specification_conflicts"]
    assert ppap_calc.dimensional_results(prepared["rows"])["rows"][0]["result"] == "MISSING"
    findings = ppap_checks.check_characteristics({"inputs": [characteristic(), reading]})
    assert ppap_checks.blocking_findings(findings)


@pytest.mark.parametrize("unit", [None, "cm"])
def test_measurement_missing_or_conflicting_units_block_and_keep_actual_reading(unit):
    reading = inspection(unit=unit, value=5)
    prepared = characteristics.prepare_dimensional_comparison([characteristic()], [reading])
    assert prepared["rows"][0]["original_value"] == 5 and reading["payload"]["value"] == 5
    assert prepared["rows"][0]["value"] is None
    assert ppap_checks.check_units({"inputs": [characteristic(), reading]})[0]["severity"] == "BLOCKER"


def test_explicit_measurement_limit_conflict_or_null_cannot_override_definition():
    for fields in ({"usl": 30, "lsl": 20}, {"usl": None}):
        prepared = characteristics.prepare_dimensional_comparison([characteristic()], [inspection(**fields)])
        assert "usl" in prepared["specification_conflicts"][0]["conflict_fields"]
        assert prepared["rows"][0]["value"] is None
        assert prepared["rows"][0]["usl"] == fields["usl"]


def test_unique_characteristic_fills_only_absent_limits_and_retains_source():
    prepared = characteristics.prepare_dimensional_comparison([characteristic()], [inspection(value=5)])
    assert prepared["problems"] == [] and prepared["specification_conflicts"] == []
    assert prepared["rows"][0]["lsl"] == 0 and prepared["rows"][0]["usl"] == 10
    assert prepared["rows"][0]["spec_source_ref"] == characteristic()["source_ref"]
    assert ppap_calc.dimensional_results(prepared["rows"])["rows"][0]["result"] == "OK"


def test_generated_special_characteristic_definition_mismatch_blocks():
    source = characteristic()
    content = flattened(characteristic(special=False))
    findings = ppap_checks.check_characteristics({"inputs": [source], "elements": [
        {"element_id": "control_plan", "status": "DRAFTED", "content": {"characteristics": [content]}}]})
    assert any("special" in row["detail"] and row["blocks_approval"] for row in findings)


def test_waiting_engineering_draft_still_exposes_available_special_definition_conflict():
    source = characteristic()
    content = flattened(characteristic(special=False))
    findings = ppap_checks.check_characteristics({"inputs": [source], "elements": [
        {"element_id": "pfmea", "status": "WAITING_DATA", "content": {"characteristics": [content]}}]})
    mismatch = next(row for row in findings if "特性定义与确认来源不一致" in row["title"])
    assert mismatch["blocks_approval"] and len(mismatch["sources"]) == 2


def test_missing_characteristic_unit_or_all_limits_is_not_inferred_from_reading():
    for source in (characteristic(unit=None), characteristic(usl=None, lsl=None)):
        prepared = characteristics.prepare_dimensional_comparison([source], [inspection(value=5)])
        assert prepared["specification_conflicts"] and prepared["rows"][0]["value"] is None
        assert ppap_calc.dimensional_results(prepared["rows"])["summary"]["ok"] == 0


def test_payload_json_is_supported_without_affecting_source_trace():
    source, reading = characteristic(), inspection(value=5)
    source["payload_json"] = source.pop("payload")
    reading["payload_json"] = reading.pop("payload")
    prepared = characteristics.prepare_dimensional_comparison([source], [reading])
    assert prepared["specification_conflicts"] == []
    assert prepared["rows"][0]["spec_sources"][0]["input_id"] == source["input_id"]


def test_helpers_do_not_modify_original_payloads_or_sources():
    source, reading = characteristic(), inspection(value=5)
    before = deepcopy([source, reading])
    prepared = characteristics.prepare_dimensional_comparison([source], [reading])
    prepared["rows"][0]["spec_sources"][0]["definition"]["usl"] = -1
    assert [source, reading] == before


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("equal_specs", [False, True])
def test_actual_dimensional_drafter_refuses_all_duplicate_definitions(reverse, equal_specs):
    from app.services import ppap

    records = [characteristic(), characteristic("fixture-alias", **({} if equal_specs else {"lsl": 20, "usl": 30}))]
    if reverse:
        records.reverse()
    all_records = [*records, inspection(value=5 if equal_specs else 25)]
    # This fixture supplies only an immutable source snapshot; no database rows
    # are created and no external sources are read by this pure drafter call.
    ctx = SimpleNamespace(task=SimpleNamespace(task_id="FIXTURE-TASK", part_side="LEFT"),
        records=lambda dataset: [flattened(record) for record in all_records if record["dataset"] == dataset])
    result = ppap.draft_dimensional_results(ctx)
    assert result["rows"][0]["result"] == "MISSING"
    assert result["result"]["state"] != "OK"
    assert result["raw_rows"][0]["value"] == (5 if equal_specs else 25)
    assert len(result["characteristics"]) == 2
    assert len(result["comparison_rows"][0]["spec_sources"]) == 2
