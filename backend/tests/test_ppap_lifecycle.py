"""Isolated PPAP lifecycle tests; no upstream ERP/MES reads or writes."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.persistence.database import SessionLocal
from app.persistence.models import AuditRecordRow, Base, PpapArtifactRow, PpapTaskRow
from app.services import ppap, ppap_export, ppap_template


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PPAP_RULES_FILE", str(tmp_path / "absent_rules.json"))


def task(*, side="LEFT", ready=False):
    result = ppap.create_task(customer_name="Explicit test customer", part_no="TEST-PPAP-LH",
        part_name="Explicit test part", owner="writer", actor="writer", part_side=side,
        drawing_version="TEST-V1", engineering_change_level="TEST-E1", reviewer="reviewer", data_mode="TEST")
    assert result["success"], result
    assert result["read_back_verified"]
    task_id = result["task"]["task_id"]
    if ready:
        decisions = [{"element_id": element, "requirement": "submit" if element in (
            "submission_checklist", "psw", "attachment_index") else "not_applicable",
            "basis": "Explicit lifecycle fixture; not a customer submission"}
            for element in ppap_template.ELEMENT_IDS]
        outcome = ppap.confirm_applicability(task_id, decisions, actor="writer")
        assert not outcome["rejected"], outcome
        put(task_id, "product_base", {"part_no": "TEST-PPAP-LH", "part_name": "Explicit test part",
            "customer_name": "Explicit test customer", "drawing_version": "TEST-V1",
            "engineering_change_level": "TEST-E1"})
        put(task_id, "process_operation", {"op_id": "TEST-OP", "op_name": "Test operation"})
        put(task_id, "characteristic", {"characteristic_id": "TEST-CHAR", "name": "Test dimension",
            "characteristic_type": "product", "unit": "mm", "lsl": 9, "usl": 11})
    return task_id


def put(task_id, dataset, payload, **kwargs):
    result = ppap.upsert_input(task_id, dataset=dataset, payload=payload, actor="writer",
        source_ref="Explicit test record", **kwargs)
    assert result["success"], result
    return result


def compile_ready(task_id):
    result = ppap.compile_task(task_id, actor="writer")
    assert result["success"], result
    assert result["run"]["status"] == "COMPILED", result["run"]["summary"]
    return result


def approve(task_id):
    submitted = ppap.submit_review(task_id, actor="writer")
    assert submitted["success"], submitted
    decided = ppap.decide_review(task_id, submitted["review"]["review_id"], actor="reviewer",
        decision="APPROVED", note="Explicit test review")
    assert decided["success"], decided
    return decided


def test_unconfirmed_applicability_blocks_and_waiver_does_not_bypass():
    task_id = task()
    compiled = ppap.compile_task(task_id, actor="writer")
    assert compiled["success"] and compiled["run"]["status"] == "BLOCKED"
    issues = ppap.list_issues(task_id)["items"]
    blocker = next(issue for issue in issues if issue["blocks_approval"])
    assert ppap.resolve_issue(task_id, blocker["issue_id"], actor="reviewer", note="still exists")["code"] == "issue_still_present"
    assert ppap.resolve_issue(task_id, blocker["issue_id"], actor="reviewer", status="WAIVED", note="explicit risk")["success"]
    assert ppap.submit_review(task_id, actor="writer")["code"] == "approval_blocked"
    ppap.run_checks(task_id, actor="reviewer")
    assert next(row for row in ppap.list_issues(task_id)["items"] if row["issue_id"] == blocker["issue_id"])["status"] == "OPEN"


def test_idempotence_freeze_versioning_archive_and_customer_state():
    task_id = task(ready=True)
    with SessionLocal() as session:
        audit_count = session.query(AuditRecordRow).count()
    first = compile_ready(task_id)
    artifact_id = first["artifact"]["artifact_id"]
    downloaded = ppap.read_artifact(task_id, artifact_id)
    assert downloaded["ok"] and downloaded["content"].startswith(b"PK")
    assert first["read_back_verified"]
    original_bytes = downloaded["content"]
    assert hashlib.sha256(original_bytes).hexdigest() == first["artifact"]["content_hash"]
    replay = ppap.compile_task(task_id, actor="writer")
    assert replay["idempotent_replay"] and replay["artifact"]["artifact_id"] == artifact_id
    assert len(ppap.list_versions(task_id)["items"]) == 1
    assert ppap.record_customer_state(task_id, actor="writer", state="ACCEPTED", reference="test")["code"] == "test_customer_state_forbidden"
    approved = approve(task_id)
    assert approved["artifact"]["frozen_at"]
    assert approved["review"]["reviewer"] == "reviewer"
    assert not approved["review"]["separation_warning"]
    assert ppap.compile_task(task_id, actor="writer")["idempotent_replay"]
    assert ppap.get_task(task_id)["task"]["package_state"] == "APPROVED"
    assert ppap.archive_task(task_id, actor="reviewer")["success"]
    assert ppap.record_customer_state(task_id, actor="reviewer", state="SUBMITTED", reference="TEST-CUSTOMER-RECORD")["code"] == "test_customer_state_forbidden"
    # A changed confirmed input creates a new draft; frozen bytes stay exactly the same.
    put(task_id, "customer_handling", {"instruction": "Explicit revised test instruction"})
    second = compile_ready(task_id)
    assert second["artifact"]["version"] == 2
    assert ppap.read_artifact(task_id, artifact_id)["content"] == original_bytes
    assert ppap.get_task(task_id)["task"]["customer_state"] == "NOT_SUBMITTED"
    with SessionLocal() as session:
        assert session.query(AuditRecordRow).count() == audit_count


def test_old_review_cannot_return_newer_pending_version():
    task_id = task(ready=True)
    first = compile_ready(task_id)
    old_request = ppap.submit_review(task_id, actor="writer")["review"]["review_id"]
    put(task_id, "customer_handling", {"instruction": "revision"})
    second = compile_ready(task_id)
    ppap.submit_review(task_id, actor="writer")
    stale = ppap.decide_review(task_id, old_request, actor="reviewer", decision="RETURNED", note="stale")
    assert stale["code"] == "review_superseded"
    assert ppap.get_task(task_id)["task"]["package_state"] == "PENDING_REVIEW"
    assert ppap.submit_review(task_id, actor="writer", artifact_id=first["artifact"]["artifact_id"])["code"] == "latest_artifact_required"
    assert second["artifact"]["version"] == 2


def test_old_archive_does_not_archive_current_draft():
    task_id = task(ready=True)
    first = compile_ready(task_id)
    approve(task_id)
    put(task_id, "customer_handling", {"instruction": "revision"})
    compile_ready(task_id)
    assert ppap.archive_task(task_id, actor="reviewer", artifact_id=first["artifact"]["artifact_id"])["success"]
    assert ppap.get_task(task_id)["task"]["package_state"] == "NOT_SUBMITTED"


def test_same_author_reviewer_is_disclosed_and_decision_is_idempotent():
    task_id = task(ready=True)
    compile_ready(task_id)
    request = ppap.submit_review(task_id, actor="writer")["review"]
    assert request["separation_warning"] == "author_reviewer_same_account"
    decided = ppap.decide_review(task_id, request["review_id"], actor="writer", decision="APPROVED", note="explicit test")
    assert decided["review"]["separation_warning"]
    replay = ppap.decide_review(task_id, request["review_id"], actor="writer", decision="APPROVED", note="explicit test")
    assert replay["idempotent_replay"]


def test_modified_input_and_rules_require_recompile(tmp_path, monkeypatch):
    task_id = task(ready=True)
    compile_ready(task_id)
    original = ppap.ppap_calc.load_acceptance_rules
    monkeypatch.setattr(ppap.ppap_calc, "load_acceptance_rules", lambda: {**original(), "version": "changed"})
    assert ppap.submit_review(task_id, actor="writer")["code"] == "stale_rules"
    monkeypatch.setattr(ppap.ppap_calc, "load_acceptance_rules", original)
    put(task_id, "customer_handling", {"instruction": "revision"})
    assert ppap.submit_review(task_id, actor="writer")["code"] == "stale_artifact"
    assert ppap.assess_source_change_impact(task_id)["changed"]


def test_request_key_cannot_be_reused_for_another_snapshot():
    task_id = task(ready=True)
    first = ppap.compile_task(task_id, actor="writer", request_key="explicit-key")
    assert first["success"]
    put(task_id, "customer_handling", {"instruction": "revision"})
    assert ppap.compile_task(task_id, actor="writer", request_key="explicit-key")["code"] == "idempotency_conflict"


def test_actual_file_hash_is_checked_before_review_and_download():
    task_id = task(ready=True)
    compiled = compile_ready(task_id)
    with SessionLocal() as session:
        artifact = session.get(PpapArtifactRow, compiled["artifact"]["artifact_id"])
        path = ppap.ppap_storage.resolve_within(ppap.ppap_storage.storage_root(), artifact.storage_rel_path)
    path.write_bytes(b"tampered")
    assert ppap.read_artifact(task_id, compiled["artifact"]["artifact_id"])["code"] == "artifact_hash_mismatch"
    assert ppap.submit_review(task_id, actor="writer")["code"] == "artifact_hash_mismatch"


def test_export_failure_rolls_back_drafts_and_records_failed_run(monkeypatch):
    task_id = task(ready=True)
    monkeypatch.setattr(ppap_export, "export_package", lambda **kwargs: (_ for _ in ()).throw(OSError("test failure")))
    failed = ppap.compile_task(task_id, actor="writer", request_key="failed-key")
    assert failed["code"] == "compile_failed"
    assert failed["run"]["status"] == "FAILED"
    assert not ppap.list_versions(task_id)["items"]
    assert ppap.compile_task(task_id, actor="writer", request_key="failed-key")["code"] == "run_not_replayable"


def test_left_right_isolation_and_verdict_and_boolean_rejection():
    task_id = task(side="LEFT")
    payload = {"characteristic_id": "TEST-C", "name": "Dimension", "characteristic_type": "product"}
    assert ppap.upsert_input(task_id, dataset="characteristic", payload=payload, actor="writer", part_side="RIGHT", source_ref="Explicit fixture")["code"] == "side_conflict"
    for extras in ({"verdict": "PASS"}, {"lsl": True}, {"guessed_field": "fake"}):
        assert ppap.upsert_input(task_id, dataset="characteristic", payload={**payload, **extras}, actor="writer", source_ref="Explicit fixture")["code"] == "invalid_input"
    assert not ppap.get_task(task_id)["inputs"]


def test_checks_preserve_historical_audit_and_read_queries_do_not_write():
    task_id = task(ready=True)
    before = ppap.get_task(task_id)
    ppap.run_checks(task_id, actor="reviewer")
    after = ppap.get_task(task_id)
    prior_audit = before["task"]["coverage"]["audit_trail"]
    assert after["task"]["coverage"]["audit_trail"][:len(prior_audit)] == prior_audit
    stable = ppap.get_task(task_id)
    ppap.get_missing_items(task_id)
    ppap.query_applicability(task_id)
    ppap.query_status(task_id)
    ppap.list_versions(task_id)
    ppap.list_runs(task_id)
    ppap.assess_source_change_impact(task_id)
    assert ppap.get_task(task_id) == stable


def test_all_22_drafters_have_no_automatic_engineering_verdict():
    task_id = task(ready=True)
    ppap.confirm_applicability(task_id, [{"element_id": "pfmea", "requirement": "submit", "basis": "test"}], actor="writer")
    compiled = ppap.compile_task(task_id, actor="writer")
    assert compiled["run"]["status"] == "BLOCKED"
    elements = {row["element_id"]: row for row in ppap.get_task(task_id)["elements"]}
    assert len(elements) == len(ppap.DRAFTERS) == 22
    assert elements["pfmea"]["status"] == "WAITING_DATA"
    assert elements["pfmea"]["content"]["engineering_state"] == "NEEDS_ENGINEERING_INPUT"
    assert elements["psw"]["content"]["signature"] is None


def test_migration_matches_models_and_keeps_existing_business_table(tmp_path):
    file = Path(__file__).parents[1] / "migrations/versions/f7b3c81d5e42_ppap_task_and_compilation.py"
    spec = importlib.util.spec_from_file_location("ppap_test_migration", file)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    mode_file = Path(__file__).parents[1] / "migrations/versions/a8c4d12e9f60_ppap_real_test_mode.py"
    mode_spec = importlib.util.spec_from_file_location("ppap_mode_test_migration", mode_file)
    mode_migration = importlib.util.module_from_spec(mode_spec)
    mode_spec.loader.exec_module(mode_migration)
    isolated_engine = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    try:
        with isolated_engine.begin() as connection:
            connection.execute(text("CREATE TABLE existing_business (id INTEGER PRIMARY KEY, value TEXT)"))
            connection.execute(text("INSERT INTO existing_business VALUES (1, 'preserved')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                mode_migration.upgrade()
            table_names = inspect(connection).get_table_names()
            ppap_tables = [name for name in table_names if name.startswith("ppap_")]
            assert len(ppap_tables) == 8
            for name in ppap_tables:
                assert {col["name"] for col in inspect(connection).get_columns(name)} == set(Base.metadata.tables[name].columns.keys())
            with Operations.context(MigrationContext.configure(connection)):
                mode_migration.downgrade()
                migration.downgrade()
            assert inspect(connection).get_table_names() == ["existing_business"]
            assert connection.execute(text("SELECT value FROM existing_business")).scalar() == "preserved"
    finally:
        isolated_engine.dispose()


def test_concurrent_same_request_creates_one_file_version():
    from concurrent.futures import ThreadPoolExecutor
    task_id = task(ready=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ppap.compile_task(task_id, actor="writer", request_key="concurrent-key"), range(2)))
    assert all(result["success"] for result in results), results
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    assert len(ppap.list_versions(task_id)["items"]) == 1
    assert len([row for row in ppap.list_runs(task_id)["items"] if row["request_key"] == "concurrent-key"]) == 1


@pytest.mark.parametrize("change", ["tamper", "delete"])
def test_registered_original_must_still_match_before_review(change):
    task_id = task(ready=True)
    ppap.ppap_storage.write_bytes("sources/approved-source.txt", b"Explicit original fixture")
    registered = ppap.register_human_evidence(task_id, source_type="user_upload", source_id="TEST-DESIGN",
        locator="sources/approved-source.txt", summary="Explicit original fixture", actor="writer", element_ids=["customer_engineering_approval"])
    assert registered["content_verified"]
    ppap.confirm_applicability(task_id, [{"element_id": "customer_engineering_approval", "requirement": "submit", "basis": "explicit fixture"}], actor="writer")
    compile_ready(task_id)
    path = ppap.ppap_storage.resolve_within(ppap.ppap_storage.storage_root(), "sources/approved-source.txt")
    if change == "tamper":
        path.write_bytes(b"Changed original fixture")
    else:
        path.unlink()
    assert ppap.submit_review(task_id, actor="writer")["code"] == "stale_evidence_file"


def test_internal_package_reference_verifies_actual_approved_file():
    task_id = task(ready=True)
    compiled = compile_ready(task_id)
    approve(task_id)
    other_task = task()
    with SessionLocal() as session:
        row = session.get(PpapArtifactRow, compiled["artifact"]["artifact_id"])
        path = ppap.ppap_storage.resolve_within(ppap.ppap_storage.storage_root(), row.storage_rel_path)
    path.write_bytes(b"Tampered approved fixture")
    result = ppap.register_human_evidence(other_task, source_type="internal_ppap_package", source_id="TEST-REF",
        locator=compiled["artifact"]["artifact_id"], summary="Explicit existing package reference fixture", actor="writer")
    assert result["code"] == "artifact_hash_mismatch"
    assert not ppap.list_evidence(other_task)["items"]


def test_complete_alembic_upgrade_on_isolated_database(tmp_path):
    import os
    import subprocess
    import sys
    backend_dir = Path(__file__).parents[1]
    target = tmp_path / "alembic_chain.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{target.as_posix()}"}
    for revision in ("e4f1a9c2d7b0", "head"):
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=backend_dir, env=environment, capture_output=True, timeout=30)
        assert result.returncode == 0, "Isolated Alembic upgrade failed"
    migrated_engine = create_engine(environment["DATABASE_URL"])
    try:
        with migrated_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "a8c4d12e9f60"
            tables = inspect(connection).get_table_names()
            assert "project_users" in tables and "projects" in tables
            assert len([name for name in tables if name.startswith("ppap_")]) == 8
    finally:
        migrated_engine.dispose()


def test_readback_rejects_missing_or_inconsistent_committed_result():
    task_id = task()
    missing = ppap._verify_persisted_result({"success": True, "task": {"task_id": "TEST-MISSING"}})
    assert missing["code"] == "read_back_failed"
    inconsistent = ppap._verify_persisted_result({"success": True,
        "task": {"task_id": task_id, "part_no": "TEST-FALSE-PART"}})
    assert inconsistent["code"] == "read_back_failed"
