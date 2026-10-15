"""Service integration in the isolated SQLite fixture; no live business writes."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

from app.persistence.database import SessionLocal
from app.persistence.models import PpapEvidenceRow, PpapInputRow, PpapTaskRow
from app.services import ppap, ppap_template


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PPAP_RULES_FILE", str(tmp_path / "no_rules.json"))


def create(mode="REAL", *, ready=False):
    suffix = uuid.uuid4().hex[:8]
    fields = {"customer_name": "隔离测试客户", "part_no": f"P-{suffix}", "part_name": "隔离测试零件",
        "part_side": "LEFT", "drawing_version": "A", "engineering_change_level": "E1"}
    result = ppap.create_task(**fields, owner="writer", actor="writer", data_mode=mode)
    assert result["success"], result
    task_id = result["task"]["task_id"]
    if ready:
        assert ppap.confirm_applicability(task_id, [{"element_id": element,
            "requirement": "submit" if element in ("submission_checklist", "psw", "attachment_index") else "not_applicable",
            "basis": "隔离服务契约测试，不是客户业务验收"} for element in ppap_template.ELEMENT_IDS], actor="writer")["success"]
        for dataset, payload in (
            ("product_base", {key: fields[key] for key in ("part_no", "part_name", "customer_name", "drawing_version", "engineering_change_level")}),
            ("process_operation", {"op_id": "OP-01", "op_name": "隔离测试工序"}),
            ("characteristic", {"characteristic_id": "C-01", "name": "长度", "characteristic_type": "product", "unit": "mm", "lsl": 9, "usl": 11}),
        ):
            assert put(task_id, dataset, payload)["success"]
    return task_id


def put(task_id, dataset, payload, **kwargs):
    return ppap.upsert_input(task_id, dataset=dataset, payload=payload, actor="writer",
        source_ref="隔离测试中的人工确认记录", **kwargs)


def compile_approve(task_id):
    compiled = ppap.compile_task(task_id, actor="writer")
    assert compiled["success"] and compiled["run"]["status"] == "COMPILED", compiled
    submitted = ppap.submit_review(task_id, actor="writer")
    assert submitted["success"], submitted
    approved = ppap.decide_review(task_id, submitted["review"]["review_id"], actor="reviewer",
        decision="APPROVED", note="隔离测试中的人工审核意见")
    assert approved["success"], approved
    return compiled["artifact"]["artifact_id"]


def test_test_mode_remains_test_after_review_and_archive():
    task_id = create("TEST", ready=True)
    artifact_id = compile_approve(task_id)
    assert ppap.archive_task(task_id, actor="reviewer", artifact_id=artifact_id)["success"]
    for state in ("SUBMITTED", "ACCEPTED", "REJECTED"):
        assert ppap.record_customer_state(task_id, actor="reviewer", state=state, reference="CUSTOMER-RECORD-01")["code"] == "test_customer_state_forbidden"
    assert ppap.get_task(task_id)["task"]["data_mode"] == "TEST"
    assert ppap.get_task(task_id)["task"]["customer_state"] == "NOT_SUBMITTED"
    assert ppap.update_task(task_id, {"data_mode": "REAL"}, actor="writer")["code"] == "task_mode_immutable"


def test_real_customer_workflow_still_requires_internal_approval():
    task_id = create("REAL", ready=True)
    assert ppap.record_customer_state(task_id, actor="reviewer", state="SUBMITTED", reference="R-01")["code"] == "internal_approval_required"
    compile_approve(task_id)
    assert ppap.record_customer_state(task_id, actor="reviewer", state="SUBMITTED", reference="R-01")["success"]
    assert ppap.record_customer_state(task_id, actor="reviewer", state="ACCEPTED", reference="R-02")["success"]


def test_test_task_never_calls_upstream_reader(monkeypatch):
    task_id = create("TEST")
    calls = []
    async def unexpected(_context):
        calls.append(1)
        raise AssertionError("reader must not run")
    for collector in ppap.COLLECTORS:
        monkeypatch.setitem(ppap.READERS, collector.collector_id, unexpected)
    assert asyncio.run(ppap.collect_evidence(task_id, actor="writer"))["code"] == "test_real_source_forbidden"
    assert not calls
    assert ppap.update_task(task_id, {"erp_order_id": "ORDER-01"}, actor="writer")["code"] == "test_business_link_forbidden"
    assert not ppap.get_task(task_id)["task"]["business_object"]["erp_order_id"]


@pytest.mark.parametrize("source_type,payload,expected", [
    ("synthetic_demo_only", {"instruction": "说明"}, "test_data_in_real_task"),
    ("custom", {"instruction": "说明"}, "unknown_input_source_type"),
    ("internal_ppap_package", {"instruction": "说明"}, "unknown_input_source_type"),
    ("user_confirmation", {"instruction": "TEST-NOTICE"}, "test_data_in_real_task"),
])
def test_real_task_rejects_synthetic_unknown_and_spoofed_input(source_type, payload, expected):
    task_id = create()
    assert put(task_id, "customer_handling", payload, source_type=source_type)["code"] == expected
    assert not ppap.get_task(task_id)["inputs"]


def test_legacy_synthetic_input_is_not_compiled_or_approved_as_real():
    task_id = create(ready=True)
    with SessionLocal() as session:
        session.add(PpapInputRow(input_id="PIN-" + uuid.uuid4().hex, task_id=task_id,
            dataset="customer_handling", record_key="LEGACY-01", part_side="LEFT",
            payload_json={"instruction": "历史合成输入"}, source_type="synthetic_demo_only",
            source_ref="历史来源", confirmed_by="writer"))
        session.commit()
    assert ppap.compile_task(task_id, actor="writer")["code"] == "test_data_in_real_task"
    assert ppap.run_checks(task_id, actor="writer")["code"] == "test_data_in_real_task"
    assert not ppap.list_versions(task_id)["items"]


def test_test_package_cannot_be_referenced_by_real_task():
    source = create("TEST", ready=True)
    artifact = compile_approve(source)
    target = create("REAL")
    result = ppap.register_human_evidence(target, source_type="internal_ppap_package", source_id="SOURCE-01",
        locator=artifact, summary="隔离引用测试", actor="writer")
    assert result["code"] == "package_mode_mismatch"
    assert not ppap.list_evidence(target)["items"]


def test_legacy_nested_reference_cannot_launder_a_test_package():
    test_source = create("TEST", ready=True)
    test_artifact = compile_approve(test_source)
    real_source = create("REAL", ready=True)
    real_artifact = compile_approve(real_source)
    with SessionLocal() as session:
        row = session.get(ppap.PpapArtifactRow, real_artifact)
        run = session.get(ppap.PpapCompileRunRow, row.run_id)
        summary = dict(run.summary_json)
        snapshot = dict(summary["snapshot"])
        provenance = dict(snapshot["provenance"])
        provenance["evidence"] = [{"evidence_id": "LEGACY-REF", "source_type": "internal_ppap_package", "locator": test_artifact}]
        snapshot["evidence"] = [{"source_type": "internal_ppap_package", "source_id": "SOURCE-02", "content_hash": "legacy"}]
        provenance["evidence"][0]["payload"] = {}
        snapshot["provenance"] = provenance
        summary["snapshot"] = snapshot
        run.summary_json = summary
        session.commit()
    target = create("REAL")
    result = ppap.register_human_evidence(target, source_type="internal_ppap_package", source_id="SOURCE-02",
        locator=real_artifact, summary="隔离嵌套引用测试", actor="writer")
    assert result["code"] == "package_mode_mismatch"
    assert not ppap.list_evidence(target)["items"]


@pytest.mark.parametrize("legacy_case", ["unchanged", "changed", "missing_provenance", "frozen_synthetic"])
def test_package_provenance_uses_frozen_payload_or_verified_legacy_input(legacy_case):
    source = create("REAL", ready=True)
    artifact_id = compile_approve(source)
    with SessionLocal() as session:
        artifact = session.get(ppap.PpapArtifactRow, artifact_id)
        run = session.get(ppap.PpapCompileRunRow, artifact.run_id)
        summary = dict(run.summary_json)
        snapshot = dict(summary["snapshot"])
        provenance = dict(snapshot["provenance"])
        records = [dict(item) for item in provenance["inputs"]]
        row = session.get(PpapInputRow, records[0]["input_id"])
        assert records[0]["payload"] == row.payload_json
        if legacy_case in ("unchanged", "changed"):
            for record in records:
                record.pop("payload")
            if legacy_case == "changed":
                row.payload_json = {**row.payload_json, "revision_note": "later change"}
        elif legacy_case == "frozen_synthetic":
            records[0]["payload"] = {**records[0]["payload"], "synthetic_demo_only": True}
        else:
            provenance = {}
        if legacy_case != "missing_provenance":
            provenance["inputs"] = records
        snapshot["provenance"] = provenance
        summary["snapshot"] = snapshot
        run.summary_json = summary
        session.commit()
    target = create("REAL")
    result = ppap.register_human_evidence(target, source_type="internal_ppap_package", source_id="SOURCE-HISTORY",
        locator=artifact_id, summary="隔离历史来源测试", actor="writer")
    if legacy_case == "unchanged":
        assert result["success"], result
    else:
        expected = "test_data_in_real_task" if legacy_case == "frozen_synthetic" else "package_provenance_unavailable"
        assert result["code"] == expected, result
        assert not ppap.list_evidence(target)["items"]


def test_same_key_can_be_revised_but_alias_key_cannot_duplicate_characteristic():
    task_id = create("TEST", ready=True)
    row = next(item for item in ppap.get_task(task_id)["inputs"] if item["dataset"] == "characteristic")
    payload = {**row["payload"], "usl": 12}
    assert put(task_id, "characteristic", payload, record_key=row["record_key"])["success"]
    duplicate = put(task_id, "characteristic", {**payload, "usl": 13}, record_key="ALIAS-01")
    assert duplicate["code"] == "characteristic_natural_key_conflict"
    assert len([item for item in ppap.get_task(task_id)["inputs"] if item["dataset"] == "characteristic"]) == 1


def test_parallel_alias_keys_cannot_create_two_specs():
    task_id = create("TEST")
    payload = {"characteristic_id": "C-01", "name": "尺寸", "characteristic_type": "product", "unit": "mm", "lsl": 0, "usl": 10}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda key: put(task_id, "characteristic", payload, record_key=key), ("KEY-A", "KEY-B")))
    assert sum(bool(result.get("success")) for result in results) == 1
    assert len(ppap.get_task(task_id)["inputs"]) == 1


def test_mode_filter_route_uses_actual_service_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.services.identity import RealIdentity
    task_id = create("TEST")
    part_no = ppap.get_task(task_id)["task"]["part_no"]
    identity = RealIdentity("writer", "隔离测试用户", ("quality_manager",), "Project", "project")
    monkeypatch.setattr(main, "resolve_project_token", lambda token: identity if token == "fixture-session" else None)
    # Do not start lifespan: only the route and isolated persistence are tested.
    client = TestClient(main.app)
    client.cookies.set(main.PROJECT_SESSION_COOKIE, "fixture-session")
    test_response = client.get("/api/ppap/tasks", params={"mode": "TEST", "part_no": part_no})
    assert test_response.status_code == 200
    assert [item["task_id"] for item in test_response.json()["items"]] == [task_id]
    real_response = client.get("/api/ppap/tasks", params={"mode": "REAL", "part_no": part_no})
    assert real_response.status_code == 200 and real_response.json()["total"] == 0
    payload = {"mode": "TEST", "customer_name": "隔离接口测试客户", "part_no": "TEST-ROUTE-" + uuid.uuid4().hex[:8],
               "part_name": "隔离接口测试零件", "part_side": "LEFT"}
    created = client.post("/api/ppap/tasks", json=payload)
    assert created.status_code == 200 and created.json()["task"]["data_mode"] == "TEST"
    rejected = client.post("/api/ppap/tasks", json={**payload, "mode": "REAL"})
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "test_data_in_real_task"
