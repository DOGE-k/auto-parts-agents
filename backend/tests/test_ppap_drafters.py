"""Read-only drafter contract audit. All sources are explicit unit-test fixtures."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from app.persistence.models import PpapTaskRow, PpapElementRow, PpapInputRow, PpapEvidenceRow
from app.services import ppap, ppap_checks, ppap_template as template, ppap_calc


FIXED_TIME = datetime(2026, 10, 13, tzinfo=timezone.utc)


def task_row(side="LEFT"):
    return PpapTaskRow(task_id="PPAP-TEST-PURE", customer_id="FIXTURE-CUSTOMER", customer_name="Fixture only customer",
        part_no="FIXTURE-PART-L", part_name="Explicit test fixture part", part_side=side,
        drawing_version="FIXTURE-V1", engineering_change_level="FIXTURE-E1", submission_reason="test fixture",
        submission_level="fixture-only", owner="fixture-author", reviewer="fixture-reviewer",
        due_date="", template_id=template.TEMPLATE_ID, template_version=template.TEMPLATE_VERSION,
        template_source=template.TEMPLATE_SOURCE, run_state="NOT_STARTED", package_state="NOT_SUBMITTED",
        customer_state="NOT_SUBMITTED", business_object_json={}, coverage_json={}, input_snapshot_hash="",
        authorized_by="fixture-author", authorization_ref="test fixture only", state_note="",
        created_at=FIXED_TIME, updated_at=FIXED_TIME)


def elements():
    return [PpapElementRow(element_row_id=f"TEST-EL-{s.element_id}", task_id="PPAP-TEST-PURE",
        element_id=s.element_id, category=s.category, requirement="submit", applicability_basis="Explicit test fixture",
        title=s.title, compilation_method=s.compilation_method, status="READY", status_reason="",
        content_json={}, source_refs_json={}, check_result_json={}, updated_at=FIXED_TIME) for s in template.ELEMENTS]


def input_row(dataset, payload, *, side="LEFT", key="1"):
    return PpapInputRow(input_id=f"TEST-INPUT-{dataset}-{key}", task_id="PPAP-TEST-PURE", dataset=dataset,
        record_key=f"fixture-record-{key}", part_side=side, element_id="", payload_json=payload,
        source_type="user_confirmation", source_ref=f"Explicit fixture {dataset} source",
        confirmed_by="fixture-engineer", confirmed_at=FIXED_TIME, updated_at=FIXED_TIME)


def evidence_row(source_type="user_upload", *, element_ids=(), read_status="OK", truncated=False):
    return PpapEvidenceRow(evidence_id="TEST-EVIDENCE-1", task_id="PPAP-TEST-PURE", dedup_key="fixture-source-key",
        source_system="fixture", source_type=source_type, source_id="FIXTURE-SOURCE-1", source_version="FIXTURE-V1",
        locator="fixture-test-only", summary="Explicit test evidence", read_status=read_status,
        error_json=None, truncated=truncated, content_hash="a" * 64, review_state="UNVERIFIED",
        collected_by="fixture-author", element_ids_json=list(element_ids), payload_json={}, collected_at=FIXED_TIME)


def context(inputs=(), evidence=(), rules=None):
    return ppap.DraftContext(task_row(), elements(), list(inputs), list(evidence), rules or {})


def element_of(ctx, element_id):
    return next(e for e in ctx.elements if e.element_id == element_id)


def draft(ctx, element_id):
    return ppap.draft_element(ctx, element_of(ctx, element_id))


def char(**extra):
    return input_row("characteristic", {"characteristic_id": "FIXTURE-CHAR-L", "name": "Fixture dimension",
                      "characteristic_type": "product", "unit": "mm", "usl": 11, "lsl": 9, **extra})


def test_all_22_drafters_return_named_task_base_and_unapproved_content():
    ctx = context()
    assert set(ppap.DRAFTERS) == set(template.ELEMENT_IDS) and len(ppap.DRAFTERS) == 22
    for element_id in template.ELEMENT_IDS:
        result = draft(ctx, element_id)
        assert result["content"]["base_info"]["part_no"] == "FIXTURE-PART-L"
        assert result["content"]["template_source"] == "project_defined"
        assert result["content"]["approval_state"] == "DRAFT"
        assert isinstance(result["source_refs"], dict)
        assert result["status"] in ("WAITING_DATA", "DRAFTED")
    psw = draft(ctx, "psw")["content"]
    assert psw["signature"] is None and psw["customer_approval"] is None


def test_missing_engineering_fields_cannot_be_fabricated():
    ctx = context([input_row("process_operation", {"op_id": "FIXTURE-OP-L", "op_name": "Explicit fixture operation"}), char()])
    for element_id in ("pfmea", "control_plan"):
        result = draft(ctx, element_id)
        assert result["status"] == "WAITING_DATA"
        assert result["content"]["engineering_state"] == "NEEDS_ENGINEERING_INPUT"
        assert result["content"]["pending_confirmations"]
        encoded = json.dumps(result["content"], ensure_ascii=False)
        assert '"rpn"' not in encoded and '"severity"' not in encoded and '"frequency"' not in encoded
    assert draft(ctx, "process_flow_diagram")["content"]["operations"][0]["op_id"] == "FIXTURE-OP-L"


def test_process_and_characteristic_contracts_match_checkers():
    ctx = context([input_row("process_operation", {"op_id": "FIXTURE-OP-L", "op_name": "Explicit fixture operation"}), char(special=True)])
    prepared = []
    for element_id in ("process_flow_diagram", "pfmea", "control_plan"):
        result = draft(ctx, element_id)
        prepared.append({"element_id": element_id, "content": result["content"], "status": "DRAFTED", "requirement": "submit"})
    check_context = {"elements": prepared, "inputs": [ppap._input_dict(r) for r in ctx.inputs]}
    assert ppap_checks.check_process_operations(check_context) == []
    assert ppap_checks.check_characteristics(check_context) == []


def test_left_drafter_excludes_right_measurements_even_with_same_characteristic_id():
    ctx = context([char(), input_row("inspection_value", {"characteristic_id": "FIXTURE-CHAR-L", "point": "LEFT-P", "value": 10.0, "unit": "mm"}),
                   input_row("inspection_value", {"characteristic_id": "FIXTURE-CHAR-L", "point": "RIGHT-P", "value": 123.0, "unit": "mm"}, side="RIGHT", key="2")])
    result = draft(ctx, "dimensional_results")
    assert [row["point"] for row in result["content"]["rows"]] == ["LEFT-P"]
    assert "RIGHT-P" not in json.dumps(result) and 123.0 not in [row["value"] for row in result["content"]["rows"]]


@pytest.mark.parametrize("reading", [None, True, "10.0"])
def test_missing_or_invalid_measurement_not_zero_or_qualified(reading):
    ctx = context([char(), input_row("inspection_value", {"characteristic_id": "FIXTURE-CHAR-L", "point": "FIXTURE-P", "value": reading, "unit": "mm"})])
    result = draft(ctx, "dimensional_results")
    assert result["status"] == "WAITING_DATA"
    assert result["content"]["rows"][0]["result"] == "MISSING"
    assert result["content"]["rows"][0]["value"] is None


@pytest.mark.parametrize("reading,limits", [(12, {}), (10, {"usl": None, "lsl": None}), (10, {"usl": 0, "lsl": 20})])
def test_ng_missing_spec_or_invalid_limits_block_dimensional_submission(reading, limits):
    ctx = context([char(**limits), input_row("inspection_value", {"characteristic_id": "FIXTURE-CHAR-L", "point": "FIXTURE-P", "value": reading, "unit": "mm"})])
    result = draft(ctx, "dimensional_results")
    assert result["status"] in ("WAITING_DATA", "CONFLICT", "BLOCKED_MISSING_DATA")
    findings = ppap_checks.check_requirements({"elements": [{"element_id": "dimensional_results", "requirement": "submit", **result}]})
    assert ppap_checks.blocking_findings(findings)


def test_unit_conflict_retains_original_value_but_does_not_compare_as_ok():
    ctx = context([char(), input_row("inspection_value", {"characteristic_id": "FIXTURE-CHAR-L", "point": "FIXTURE-P", "value": 1.0, "unit": "cm"})])
    result = draft(ctx, "dimensional_results")
    assert result["status"] == "WAITING_DATA"
    assert result["content"]["rows"][0]["result"] == "MISSING"
    assert result["content"]["raw_rows"][0]["value"] == 1.0
    assert result["content"]["raw_rows"][0]["unit"] == "cm"


def test_original_document_scope_cannot_make_other_original_ready():
    only_design = evidence_row(element_ids=["design_records"])
    ctx = context(evidence=[only_design])
    design = draft(ctx, "design_records")
    assert design["content"]["references"][0]["evidence_id"] == only_design.evidence_id
    approval = draft(ctx, "customer_engineering_approval")
    assert approval["content"]["references"] == []
    assert approval["status"] == "WAITING_DATA"


def test_unscoped_human_document_not_listed_as_an_original_of_every_element():
    ctx = context(evidence=[evidence_row(element_ids=[])])
    for element_id in ("design_records", "customer_engineering_approval", "appearance_approval_report"):
        result = draft(ctx, element_id)
        assert result["status"] == "WAITING_DATA"
        assert result["content"]["references"] == []


@pytest.mark.parametrize("state,truncated", [("EMPTY", False), ("ERROR", False), ("OK", True)])
def test_empty_failed_or_truncated_reference_not_drafted(state, truncated):
    ctx = context(evidence=[evidence_row(element_ids=["customer_engineering_approval"], read_status=state, truncated=truncated)])
    result = draft(ctx, "customer_engineering_approval")
    assert result["content"]["references"] == [] and result["status"] == "WAITING_DATA"


def test_physical_type_is_not_substituted_or_claimed_by_another_record():
    ctx = context([input_row("physical_record", {"record_type": "master_sample", "quantity": 1}, side="NONE")])
    sample = draft(ctx, "sample_production_part")
    assert not sample["content"]["rows"] and sample["status"] == "WAITING_DATA"
    master = draft(ctx, "master_sample")
    assert master["content"]["rows"][0]["record_type"] == "master_sample"
    assert "不等于已提供实物" in master["content"]["notice"]


def test_msa_explicit_method_and_needs_confirmation_contract():
    ctx = context([input_row("msa_study", {"study_id": "FIXTURE-MSA", "characteristic_id": "FIXTURE-CHAR-L", "unit": "mm"})])
    result = draft(ctx, "msa")
    assert result["status"] == "WAITING_DATA" and result["content"]["studies"][0]["result"]["state"] == "UNSUPPORTED_CONFIG"
    assert all(row["acceptance_state"] == "NO_VALUE" for row in result["content"]["calculations"])
    findings = ppap_checks.check_msa({"elements": [{"element_id": "msa", "requirement": "submit", **result}],
                                    "inputs": [ppap._input_dict(r) for r in ctx.inputs]})
    assert findings and all(row["check_id"] == "PPAP-CHK-MSA-001" for row in findings)
    findings.extend(ppap_checks.check_requirements({"elements": [{"element_id": "msa", "requirement": "submit", **result}]}))
    assert ppap_checks.blocking_findings(findings)


def test_rules_require_explicit_human_confirmation_even_with_thresholds():
    rules = {"configured": True, "requires_human_confirmation": True, "confirmed_by": "TBD",
             "metrics": {"cpk": {"min": 1.2}}, "rule_version": "TEST-RULE"}
    assert ppap_calc.classify_metric("cpk", 8.0, rules)["acceptance_state"] == "NEEDS_CONFIRMATION"


@pytest.mark.parametrize("element_id,dataset,payload,checker", [
    ("msa", "msa_study", {"study_id": "FIXTURE-MSA", "characteristic_id": "FIXTURE-CHAR-L", "unit": "mm", "method": "average_range",
      "tolerance": 2.0, "measurements": [
          {"appraiser": "FIXTURE-A", "parts": [{"part": "P1", "trials": [1.0, 1.1]}, {"part": "P2", "trials": [2.0, 2.1]}]},
          {"appraiser": "FIXTURE-B", "parts": [{"part": "P1", "trials": [1.2, 1.3]}, {"part": "P2", "trials": [2.2, 2.3]}]}]}, ppap_checks.check_msa),
    ("initial_process_study", "process_study", {"study_id": "FIXTURE-PROCESS", "characteristic_id": "FIXTURE-CHAR-L", "unit": "mm",
      "values": [10.0, 10.1, 9.9] * 10, "usl": 11.0, "lsl": 9.0}, ppap_checks.check_process_study),
])
def test_submit_metric_without_confirmed_acceptance_rules_blocks(element_id, dataset, payload, checker):
    ctx = context([char(), input_row(dataset, payload)])
    result = draft(ctx, element_id)
    assert any(row["acceptance_state"] == "NEEDS_CONFIRMATION" for row in result["content"]["calculations"])
    check_context = {"elements": [{"element_id": element_id, "requirement": "submit", **result}],
                     "inputs": [ppap._input_dict(row) for row in ctx.inputs]}
    findings = checker(check_context) + ppap_checks.check_requirements(check_context)
    assert ppap_checks.blocking_findings(findings), "未确认阈值的提交条目不能只作为MAJOR提示后批准"


def test_evidence_scope_changes_invalidate_compilation_snapshot():
    ctx = context(evidence=[evidence_row(element_ids=["design_records"])])
    first = ppap._current_snapshot_hash(ctx.task, ctx.elements, ctx.inputs, ctx.evidence)
    ctx.evidence[0].element_ids_json = ["customer_engineering_approval"]
    second = ppap._current_snapshot_hash(ctx.task, ctx.elements, ctx.inputs, ctx.evidence)
    assert first != second, "原件用途变更必须使旧审核快照失效"


@pytest.mark.parametrize("field", ["owner", "reviewer", "due_date"])
def test_displayed_task_metadata_changes_invalidate_export_snapshot(field):
    ctx = context()
    first = ppap._current_snapshot_hash(ctx.task, ctx.elements, ctx.inputs, ctx.evidence)
    setattr(ctx.task, field, "explicit fixture changed value")
    second = ppap._current_snapshot_hash(ctx.task, ctx.elements, ctx.inputs, ctx.evidence)
    assert second != first


def test_calculation_time_change_does_not_change_compilation_content(monkeypatch):
    inputs = [char(), input_row("process_study", {"study_id": "FIXTURE-STUDY", "characteristic_id": "FIXTURE-CHAR-L",
              "values": [10.0, 10.1, 9.9] * 10, "usl": 11.0, "lsl": 9.0, "unit": "mm"})]
    ctx = context(inputs)
    monkeypatch.setattr(ppap.ppap_storage, "now_iso", lambda: "2026-10-13T00:00:00+00:00")
    first = draft(ctx, "initial_process_study")
    monkeypatch.setattr(ppap.ppap_storage, "now_iso", lambda: "2026-10-14T00:00:00+00:00")
    second = draft(ctx, "initial_process_study")
    assert first["content"] == second["content"]
    assert "calculated_at" not in json.dumps(first["content"])


def test_real_compilation_with_metric_data_replays_after_time_change(tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_STORAGE_DIR", str(tmp_path / "ppap_store"))
    monkeypatch.setenv("PPAP_RULES_FILE", str(tmp_path / "not_confirmed_rules.json"))
    created = ppap.create_task(customer_name="Time replay test fixture", part_no="FIXTURE-TIME-L",
                               part_name="Explicit test fixture", part_side="LEFT", owner="fixture-author", actor="fixture-author", data_mode="TEST")
    task_id = created["task"]["task_id"]
    assert ppap.confirm_applicability(task_id, [{"element_id": e, "requirement": "submit" if e == "initial_process_study" else "not_applicable",
        "basis": "Explicit isolated test fixture, not a customer submission"} for e in template.ELEMENT_IDS], actor="fixture-author")["success"]
    assert ppap.upsert_input(task_id, dataset="characteristic", payload={"characteristic_id": "FIXTURE-CHAR-L", "name": "Fixture dimension",
        "characteristic_type": "product", "unit": "mm", "usl": 11.0, "lsl": 9.0}, actor="fixture-author", source_ref="Fixture source only")["success"]
    assert ppap.upsert_input(task_id, dataset="process_study", payload={"study_id": "FIXTURE-STUDY", "characteristic_id": "FIXTURE-CHAR-L",
        "values": [10.0, 10.1, 9.9] * 10, "usl": 11.0, "lsl": 9.0, "unit": "mm"}, actor="fixture-author", source_ref="Fixture original record")["success"]
    monkeypatch.setattr(ppap.ppap_storage, "now_iso", lambda: "2026-10-13T00:00:00+00:00")
    first = ppap.compile_task(task_id, actor="fixture-author")
    assert first["success"]
    monkeypatch.setattr(ppap.ppap_storage, "now_iso", lambda: "2026-10-14T00:00:00+00:00")
    second = ppap.compile_task(task_id, actor="fixture-author")
    assert second["success"] and second["idempotent_replay"]
    assert second["artifact"]["artifact_id"] == first["artifact"]["artifact_id"]
    assert len(ppap.list_versions(task_id)["items"]) == 1
