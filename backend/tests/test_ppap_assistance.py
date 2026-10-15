"""Read-only assistance: synthetic local fixtures never call ERP/MES."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.persistence.database import SessionLocal, engine
from app.persistence.models import PpapCompileRunRow, PpapInputRow
from app.services import ppap, ppap_assistance, ppap_storage, ppap_template


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PPAP_RULES_FILE", str(tmp_path / "no_rules.json"))


def task():
    result = ppap.create_task(customer_name="资料识别测试客户", part_no="TEST-" + uuid4().hex[:12],
        part_name="测试板", part_side="LEFT", drawing_version="B", engineering_change_level="E2",
        owner="writer", actor="writer", data_mode="TEST")
    assert result["success"], result
    return result["task"]["task_id"]


def put(task_id, dataset, payload, *, record_key="R1", side="LEFT", source_type="synthetic_demo_only"):
    result = ppap.upsert_input(task_id, dataset=dataset, payload=payload, actor="writer",
        record_key=record_key, part_side=side, source_type=source_type, source_ref="已确认的隔离测试资料")
    assert result["success"], result
    return result


def characteristic(task_id, *, char="C1", key="SPEC-1", side="LEFT", **values):
    return put(task_id, "characteristic", {"characteristic_id": char, "name": "长度",
        "characteristic_type": "product", "unit": "mm", "lsl": 9, "usl": 11, "nominal": 10, **values},
        record_key=key, side=side)


def fields(result):
    assert result["ok"], result
    return {item["field"]: item["value"] for item in result["suggestions"]}


def test_product_base_suggestions_trace_task_metadata_not_external_evidence():
    task_id = task()
    result = ppap.get_input_assistance(task_id, dataset="product_base")
    assert fields(result) == {"part_no": ppap.get_task(task_id)["task"]["part_no"],
        "part_name": "测试板", "customer_name": "资料识别测试客户", "drawing_version": "B", "engineering_change_level": "E2"}
    assert result["read_only"] and result["data_mode"] == "TEST"
    assert all(item["origin"] == "task_metadata" and item["source_type"] == "synthetic_demo_only"
               and item["source_ref"].startswith(f"PPAP:{task_id}:metadata@") for item in result["suggestions"])


@pytest.mark.parametrize("dataset,expected", [
    ("inspection_value", {"characteristic_id": "C1", "unit": "mm", "lsl": 9, "usl": 11, "nominal": 10}),
    ("msa_study", {"characteristic_id": "C1", "unit": "mm"}),
    ("process_study", {"characteristic_id": "C1", "unit": "mm", "lsl": 9, "usl": 11, "target": 10}),
])
def test_unique_same_side_candidate_suggests_only_definitions(dataset, expected):
    task_id = task()
    characteristic(task_id, target=10)
    # A historical measurement/study is never used as a new sample.
    put(task_id, "inspection_value", {"characteristic_id": "C1", "point": "P1", "value": 9.8}, record_key="MEASURED")
    result = ppap.get_input_assistance(task_id, dataset=dataset)
    assert fields(result) == expected
    assert all(item["source_type"] == "synthetic_demo_only" and item["record_key"] == "SPEC-1"
               for item in result["suggestions"])
    assert not ({"value", "values", "measurements", "sample_no", "study_id", "method", "tolerance", "verdict"} & set(fields(result)))


def test_multiple_candidates_require_selection_and_other_side_is_excluded():
    task_id = task()
    characteristic(task_id)
    characteristic(task_id, char="C2", key="SPEC-2")
    with SessionLocal() as session:
        session.add(PpapInputRow(input_id="PIN-" + uuid4().hex, task_id=task_id, dataset="characteristic",
            record_key="SPEC-R", part_side="RIGHT", payload_json={"characteristic_id": "RIGHT-ONLY",
                "name": "历史右侧记录", "characteristic_type": "product", "unit": "cm"},
            source_type="synthetic_demo_only", source_ref="隔离测试历史侧别", confirmed_by="writer"))
        session.commit()
    result = ppap.get_input_assistance(task_id, dataset="inspection_value")
    assert fields(result) == {}
    assert {item["characteristic_id"] for item in result["candidates"]} == {"C1", "C2"}
    assert fields(ppap.get_input_assistance(task_id, dataset="inspection_value", characteristic_id="C2"))["characteristic_id"] == "C2"
    assert fields(ppap.get_input_assistance(task_id, dataset="inspection_value", characteristic_id="RIGHT-ONLY")) == {}


@pytest.mark.parametrize("second_limit", [11, 15])
def test_legacy_duplicate_or_conflicting_spec_cannot_suggest(second_limit):
    task_id = task()
    characteristic(task_id)
    with SessionLocal() as session:
        session.add(PpapInputRow(input_id="PIN-" + uuid4().hex, task_id=task_id, dataset="characteristic",
            record_key="LEGACY-DUP", part_side="LEFT", payload_json={"characteristic_id": "C1", "name": "长度",
            "characteristic_type": "product", "unit": "mm", "lsl": 9, "usl": second_limit, "nominal": 10},
            source_type="synthetic_demo_only", source_ref="隔离测试历史重复", confirmed_by="writer"))
        session.commit()
    result = ppap.get_input_assistance(task_id, dataset="inspection_value", characteristic_id="C1")
    assert not fields(result)
    assert all(item["conflict"] for item in result["candidates"])
    assert result["conflicts"][0]["code"] == "characteristic_natural_key_conflict"


def test_revision_preserves_original_measurements_nulls_and_source():
    task_id = task()
    characteristic(task_id)
    original = {"characteristic_id": "C1", "point": "P1", "value": 9.8, "lsl": None, "unit": "cm"}
    put(task_id, "inspection_value", original, record_key="READING-1")
    result = ppap.get_input_assistance(task_id, dataset="inspection_value", record_key="READING-1")
    assert result["existing_input"]["payload"] == original
    assert result["existing_input"]["source_type"] == "synthetic_demo_only"
    assert fields(result) == {"usl": 11, "nominal": 10}
    assert "lsl" in result["explicitly_empty_fields"]
    assert {item["field"] for item in result["conflicts"]} == {"lsl", "unit"}


def test_null_in_definition_is_not_given_a_hidden_default():
    task_id = task()
    characteristic(task_id, nominal=None, lsl=None)
    assert fields(ppap.get_input_assistance(task_id, dataset="inspection_value")) == {
        "characteristic_id": "C1", "unit": "mm", "usl": 11}


@pytest.mark.parametrize("invalid", [{"lsl": 11}, {"lsl": 12}, {"usl": True}, {"usl": float("inf")}, {"unit": 1}])
def test_legacy_invalid_limits_or_units_are_not_prefilled(invalid):
    task_id = task()
    characteristic(task_id)
    data = ppap.get_task(task_id)
    records = deepcopy(data["inputs"])
    records[0]["payload"].update(invalid)
    result = ppap_assistance.build_input_assistance(data["task"], records, dataset="inspection_value")
    assert fields(result) == {"characteristic_id": "C1"}
    assert result["candidates"][0]["conflict"]
    assert result["conflicts"][0]["code"] == "invalid_characteristic_specification"


def test_single_sided_definition_does_not_guess_other_limit():
    task_id = task()
    characteristic(task_id)
    data = ppap.get_task(task_id)
    records = deepcopy(data["inputs"])
    records[0]["payload"].pop("lsl")
    result = ppap_assistance.build_input_assistance(data["task"], records, dataset="process_study")
    assert "lsl" not in fields(result) and fields(result)["usl"] == 11


def test_missing_metadata_date_does_not_invent_a_source_version():
    task_id = task()
    metadata = ppap.get_task(task_id)["task"]
    metadata.pop("updated_at")
    metadata.pop("created_at")
    result = ppap_assistance.build_input_assistance(metadata, [], dataset="product_base")
    assert not fields(result)
    assert result["notices"]


def test_unconfirmed_and_other_task_records_are_not_candidates():
    task_id = task()
    other = task()
    characteristic(other)
    data = ppap.get_task(task_id)["task"]
    foreign = ppap.get_task(other)["inputs"]
    unconfirmed = {**foreign[0], "task_id": task_id, "confirmed_by": ""}
    result = ppap_assistance.build_input_assistance(data, [*foreign, unconfirmed], dataset="inspection_value")
    assert not fields(result) and not result["candidates"]


def test_assistance_and_comparison_perform_no_writes_or_upstream_requests(monkeypatch):
    task_id = task()
    characteristic(task_id)
    async def forbidden(_context):
        raise AssertionError("read-only assistance must not execute collection")
    for collector in ppap.COLLECTORS:
        monkeypatch.setitem(ppap.READERS, collector.collector_id, forbidden)
    writes = []
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().split()[0].upper() in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"):
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert ppap.get_input_assistance(task_id, dataset="inspection_value")["ok"]
        assert ppap.get_missing_items(task_id)["ok"]
        assert ppap.assess_source_change_impact(task_id)["ok"]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not writes


def test_invalid_real_provenance_cannot_be_relabelled_by_assistance():
    result = ppap.create_task(customer_name="客户", part_no="P-" + uuid4().hex[:10], part_name="板",
        part_side="LEFT", actor="writer", owner="writer", data_mode="REAL")
    task_id = result["task"]["task_id"]
    with SessionLocal() as session:
        session.add(PpapInputRow(input_id="PIN-" + uuid4().hex, task_id=task_id, dataset="characteristic",
            record_key="LEGACY", part_side="LEFT", payload_json={"characteristic_id": "C1"},
            source_type="synthetic_demo_only", source_ref="历史测试", confirmed_by="writer"))
        session.commit()
    assert ppap.get_input_assistance(task_id, dataset="inspection_value")["code"] == "test_data_in_real_task"


def test_navigation_targets_exact_record_and_does_not_invent_unknown_route():
    task_id = task()
    characteristic(task_id)
    put(task_id, "inspection_value", {"characteristic_id": "C1", "point": "P1", "value": 10}, record_key="READING-1")
    result = ppap.get_missing_items(task_id)
    unit = next(item for item in result["findings"] if item["dedup_key"].endswith("unit_missing"))
    assert unit["navigation"]["dataset"] == "inspection_value"
    assert unit["navigation"]["record_key"] == "READING-1"
    assert unit["navigation"]["field"] == "unit"
    applicability = next(item for item in result["findings"] if item["field_path"] == "requirement")
    assert applicability["navigation"]["kind"] == "applicability"
    assert ppap_assistance.finding_navigation({"check_id": "UNKNOWN", "field_path": "fake_field"}, [])["kind"] == "manual"
    assert ppap_assistance.finding_navigation({"check_id": "PPAP-CHK-MSA-001", "kind": "RULE_UNCONFIRMED"}, [])["kind"] == "rules"


def test_navigation_handles_natural_record_keys_containing_colons_and_pipes():
    task_id = task()
    characteristic(task_id)
    result = ppap.upsert_input(task_id, dataset="inspection_value",
        payload={"characteristic_id": "C1", "point": "P1", "sample_no": "S1", "value": 10},
        actor="writer", source_type="synthetic_demo_only", source_ref="隔离测试资料")
    assert result["success"]
    finding = next(item for item in ppap.get_missing_items(task_id)["findings"] if item["dedup_key"].endswith("unit_missing"))
    assert finding["navigation"]["record_key"] == "C1:P1:S1|LEFT"
    assert finding["navigation"]["input_id"] == result["input_id"]


def test_precise_comparison_keeps_explicit_null_when_removed():
    task_id = task()
    characteristic(task_id)
    old = {"study_id": "S1", "characteristic_id": "C1", "unit": "mm", "lsl": None, "values": [9, 10, 11]}
    put(task_id, "process_study", old)
    artifact, _ = compile_with(task_id)
    put(task_id, "process_study", {key: value for key, value in old.items() if key != "lsl"})
    result = ppap.assess_source_change_impact(task_id, compare_artifact_id=artifact["artifact_id"])
    change = next(item for item in result["changes"] if item["field_path"] == "payload.lsl")
    assert change["old_present"] is True and change["old_value"] is None
    assert change["new_present"] is False and change["change_type"] == "removed"


def test_deleted_record_uses_frozen_identity_and_payload_not_a_matching_hash():
    task_id = task()
    characteristic(task_id)
    artifact, _ = compile_with(task_id)
    with SessionLocal() as session:
        session.query(PpapInputRow).filter_by(task_id=task_id).delete()
        session.commit()
    result = ppap.assess_source_change_impact(task_id, compare_artifact_id=artifact["artifact_id"])
    change = next(item for item in result["changes"] if item["field_path"] == "payload.usl")
    assert change["old_available"] is True and change["old_value"] == 11 and change["new_present"] is False


def test_source_only_revision_uses_declared_dataset_dependencies():
    task_id = task()
    characteristic(task_id)
    artifact, _ = compile_with(task_id)
    original = ppap.get_task(task_id)["inputs"][0]
    assert ppap.upsert_input(task_id, dataset="characteristic", payload=original["payload"],
        record_key=original["record_key"], part_side="LEFT", actor="writer", source_type="synthetic_demo_only",
        source_ref="新确认的同侧规格记录")["success"]
    result = ppap.assess_source_change_impact(task_id, compare_artifact_id=artifact["artifact_id"])
    change = next(item for item in result["changes"] if item["field_path"] == "source_ref")
    assert change["old_source"]["source_ref"] == original["source_ref"]
    assert change["new_source"]["source_ref"] == "新确认的同侧规格记录"
    assert {"msa", "dimensional_results", "initial_process_study", "pfmea", "control_plan"} <= set(result["affected_elements"])
    assert "dfmea" not in result["affected_elements"]


def compile_with(task_id):
    result = ppap.compile_task(task_id, actor="writer")
    assert result["success"], result
    return result["artifact"], result["run"]


def test_diff_preserves_arrays_and_distinguishes_null_from_absent():
    task_id = task()
    characteristic(task_id)
    old = {"study_id": "S1", "characteristic_id": "C1", "unit": "mm", "values": [9.7, 9.8, 9.9]}
    put(task_id, "process_study", old, record_key="STUDY-1")
    artifact, _ = compile_with(task_id)
    put(task_id, "process_study", {**old, "values": [10, 10.1], "lsl": None}, record_key="STUDY-1")
    result = ppap.assess_source_change_impact(task_id, compare_artifact_id=artifact["artifact_id"])
    values = next(item for item in result["changes"] if item["field_path"] == "payload.values")
    assert values["old_value"] == old["values"] and values["new_value"] == [10, 10.1]
    null = next(item for item in result["changes"] if item["field_path"] == "payload.lsl")
    assert null["old_present"] is False and null["new_present"] is True and null["new_value"] is None
    assert values["old_source"]["source_type"] == "synthetic_demo_only"
    assert "initial_process_study" in result["affected_elements"]
    assert "design_records" not in result["affected_elements"]


def test_no_change_keeps_boolean_contract_and_has_no_precise_diff():
    task_id = task()
    characteristic(task_id)
    compile_with(task_id)
    result = ppap.assess_source_change_impact(task_id)
    assert result["changed"] is False
    assert result["changes"] == [] and result["affected_elements"] == []


def test_old_hash_only_payload_is_unavailable_not_current_value_as_history():
    task_id = task()
    characteristic(task_id)
    artifact, run = compile_with(task_id)
    with SessionLocal() as session:
        row = session.get(PpapCompileRunRow, run["run_id"])
        summary = deepcopy(row.summary_json)
        summary["snapshot"]["provenance"]["inputs"] = []
        row.summary_json = summary
        session.commit()
    characteristic(task_id, usl=12)
    result = ppap.assess_source_change_impact(task_id, compare_artifact_id=artifact["artifact_id"])
    unavailable = next(item for item in result["changes"] if item["field_path"] == "payload")
    assert unavailable["old_available"] is False and unavailable["old_present"] is None
    assert unavailable["old_value"] is None and unavailable["new_value"]["usl"] == 12


def test_specific_version_is_scoped_to_task_and_does_not_overwrite_old_artifact():
    task_id, other = task(), task()
    characteristic(task_id)
    artifact, _ = compile_with(task_id)
    original_hash = artifact["content_hash"]
    characteristic(task_id, usl=12)
    compile_with(task_id)
    assert ppap.assess_source_change_impact(other, compare_artifact_id=artifact["artifact_id"])["code"] == "artifact_not_found"
    result = ppap.assess_source_change_impact(task_id, compare_artifact_id=artifact["artifact_id"])
    assert result["comparison_version"] == 1
    assert any(item["field_path"] == "payload.usl" and item["old_value"] == 11 and item["new_value"] == 12
               for item in result["changes"])
    assert ppap.read_artifact(task_id, artifact["artifact_id"])["artifact"]["content_hash"] == original_hash


def test_evidence_bindings_and_unknown_changes_have_explained_impacts():
    elements = [{"element_id": spec.element_id, "requirement": "submit"} for spec in ppap_template.ELEMENTS]
    affected, reasons = ppap_assistance.affected_elements([
        {"section": "evidence", "source_type": "user_upload", "source_id": "DRAWING", "field_path": "element_ids",
         "old_value": ["design_records"], "new_value": ["engineering_change_documents"]}], elements, [])
    assert "design_records" in affected and "engineering_change_documents" in affected
    assert "msa" not in affected and reasons
    broad, _ = ppap_assistance.affected_elements([{"section": "unknown"}], elements, [])
    assert set(broad) == set(ppap_template.ELEMENT_IDS)
