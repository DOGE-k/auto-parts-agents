"""PPAP evidence tri-state, scope and pagination audit without upstream access."""
from __future__ import annotations

import asyncio

import pytest

from app.persistence.database import SessionLocal
from app.persistence.models import PpapTaskRow
from app.services import ppap, ppap_checks, ppap_storage, ppap_template as template


@pytest.fixture(autouse=True)
def safe_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_STORAGE_DIR", str(tmp_path / "ppap_store"))
    monkeypatch.setenv("PPAP_RULES_FILE", str(tmp_path / "not_configured_rules.json"))


def create_evidence_task(*, read_contract=False):
    # REAL exercises the production read contract against explicitly isolated
    # reader fixtures. Human fixture files remain TEST and never call readers.
    result = ppap.create_task(customer_name="隔离数据库中的取证契约测试", part_no="PART-SCOPE-L" if read_contract else "FIXTURE-PART-L",
                              part_name="隔离契约测试零件", owner="fixture-author", actor="fixture-author", part_side="LEFT",
                              data_mode="REAL" if read_contract else "TEST")
    task_id = result["task"]["task_id"]
    with SessionLocal() as session:
        row = session.get(PpapTaskRow, task_id)
        row.business_object_json = {"work_order_id": "SCOPE-ORDER-01"} if read_contract else {}
        session.commit()
    return task_id


@pytest.mark.parametrize("collector,payload", [("openmes.engineering_documents", {"__items": []}),
    ("openmes.inspections", {"__items": []}), ("quality.issue_queue", {"items": []}),
    ("erp.quotation", {"found": False}), ("openmes.track_order", {"status": "NOT_FOUND"})])
def test_successful_zero_is_empty_not_auth_failure(collector, payload):
    state, summary, error = ppap._classify_read(collector, payload)
    assert state == "EMPTY" and error is None
    assert "401" not in summary and "认证失败" not in summary


@pytest.mark.parametrize("payload", [None, [], {"error": "HTTP 401 fixture only"}, {"error": "fixture timeout"}])
def test_reader_failures_are_errors_not_no_item(payload):
    state, summary, error = ppap._classify_read("openmes.quality_package", payload)
    assert state == "ERROR" and error
    assert "读取失败" in summary or "读取返回" in summary


def test_mes_unreachable_does_not_mean_not_found():
    state, _, error = ppap._classify_read("openmes.track_order", {"status": "MES_UNREACHABLE"})
    assert state == "ERROR" and error["code"] == "mes_unreachable"


def test_inspection_scope_unavailable_is_error_not_legitimate_zero():
    state, summary, error = ppap._classify_read("openmes.inspections", {
        "__items": [], "gate_details": {"inspections_scope": "unavailable"}})
    assert state == "ERROR" and error


def test_complete_empty_quality_parent_yields_empty_children(monkeypatch):
    async def reader(_context):
        return {"quality_records": [], "documents": [], "inspections": [], "quality_gate_passed": False}
    monkeypatch.setitem(ppap.READERS, "openmes.quality_package", reader)
    task_id = create_evidence_task(read_contract=True)
    result = asyncio.run(ppap.collect_evidence(task_id, source_types=[template.EV_OPENMES_QUALITY_PACKAGE,
         template.EV_OPENMES_ENGINEERING_DOC, template.EV_OPENMES_INSPECTION], actor="fixture-author"))
    assert result["success"]
    states = {r["collector_id"]: r["read_status"] for r in result["collected"]}
    assert states["openmes.quality_package"] == "OK"
    assert states["openmes.engineering_documents"] == states["openmes.inspections"] == "EMPTY"
    assert result["coverage"]["evidence"]["error"] == 0


def test_parent_error_is_never_relabelled_as_empty_child(monkeypatch):
    async def reader(_context):
        return {"error": "HTTP 401 fixture only"}
    monkeypatch.setitem(ppap.READERS, "openmes.quality_package", reader)
    task_id = create_evidence_task(read_contract=True)
    result = asyncio.run(ppap.collect_evidence(task_id, source_types=[template.EV_OPENMES_QUALITY_PACKAGE,
         template.EV_OPENMES_ENGINEERING_DOC, template.EV_OPENMES_INSPECTION], actor="fixture-author"))
    assert result["success"]
    states = {r["collector_id"]: r["read_status"] for r in result["collected"]}
    assert states["openmes.quality_package"] == "ERROR"
    assert "EMPTY" not in states.values()
    for child in ("openmes.engineering_documents", "openmes.inspections"):
        assert states.get(child) == "ERROR" or any(r["collector_id"] == child and r["reason"] == "parent_unavailable" for r in result["skipped"])


def test_parent_exception_is_not_relabelled_as_empty(monkeypatch):
    async def reader(_context):
        raise TimeoutError("explicit fixture transport failure")
    monkeypatch.setitem(ppap.READERS, "openmes.quality_package", reader)
    task_id = create_evidence_task(read_contract=True)
    result = asyncio.run(ppap.collect_evidence(task_id, source_types=[template.EV_OPENMES_QUALITY_PACKAGE,
         template.EV_OPENMES_ENGINEERING_DOC, template.EV_OPENMES_INSPECTION], actor="fixture-author"))
    assert all(r["read_status"] != "EMPTY" for r in result["collected"])
    assert any(r["read_status"] == "ERROR" for r in result["collected"])


@pytest.mark.parametrize("count,expected", [(0, False), (49, False), (50, True), (51, True)])
def test_inspection_window_exposes_incomplete_pagination(count, expected):
    payload = {"__items": [{"fixture_id": i} for i in range(count)], "gate_details": {"inspections_total": 500}}
    assert ppap._truncated_for("openmes.inspections", payload) is expected
    assert ppap._truncated_for("openmes.quality_package", {"inspections": payload["__items"]}) is expected


def test_scoped_original_cannot_satisfy_different_original_requirement():
    context = {"elements": [{"element_id": "customer_engineering_approval", "requirement": "submit", "status": "DRAFTED"}],
        "evidence": [{"source_type": "user_upload", "read_status": "OK", "truncated": False,
                       "element_ids": ["design_records"], "source_id": "FIXTURE-DESIGN-ONLY"}]}
    findings = ppap_checks.check_evidence(context)
    assert ppap_checks.blocking_findings(findings)
    assert findings[0]["element_id"] == "customer_engineering_approval"


def test_truncated_original_not_usable_for_approval():
    context = {"elements": [{"element_id": "customer_engineering_approval", "requirement": "submit", "status": "DRAFTED"}],
        "evidence": [{"source_type": "user_upload", "read_status": "OK", "truncated": True,
                       "element_ids": ["customer_engineering_approval"], "source_id": "FIXTURE-TRUNCATED"}]}
    findings = ppap_checks.check_evidence(context)
    assert ppap_checks.blocking_findings(findings)
    assert "truncated=True" in findings[0]["detail"]


def test_unverified_external_file_is_recorded_but_cannot_make_item_complete():
    task_id = create_evidence_task()
    result = ppap.register_human_evidence(task_id, source_type="user_upload", source_id="FIXTURE-EXTERNAL",
        locator="unavailable-fixture-source.pdf", summary="Explicit external fixture not present on disk", actor="fixture-author")
    assert result["success"] and not result["content_verified"] and result["read_status"] != "OK"
    records = ppap.list_evidence(task_id, include_payload=True)["items"]
    assert records and all(r["read_status"] != "OK" for r in records)


def test_human_original_requires_explicit_element_scope_and_actual_file_fingerprint():
    task_id = create_evidence_task()
    original = b"Explicit fixture source document A"
    ppap_storage.write_bytes("sources/fixture-source.txt", original)
    registered = ppap.register_human_evidence(task_id, source_type="user_upload", source_id="FIXTURE-DESIGN-ORIGINAL",
        locator="sources/fixture-source.txt", summary="Explicit scoped original unit test fixture", actor="fixture-author",
        element_ids=["design_records"])
    assert registered["success"]
    row = next(r for r in ppap.list_evidence(task_id, include_payload=True)["items"] if r["source_id"] == "FIXTURE-DESIGN-ORIGINAL")
    assert row["element_ids"] == ["design_records"]
    assert row["locator"] == "sources/fixture-source.txt"
    assert row["content_hash"] == ppap_storage.sha256_bytes(original)
    ppap_storage.write_bytes("sources/fixture-source.txt", b"Explicit fixture source document B")
    assert len(original) == len(ppap_storage.read_bytes("sources/fixture-source.txt"))
    again = ppap.register_human_evidence(task_id, source_type="user_upload", source_id="FIXTURE-DESIGN-ORIGINAL",
        locator="sources/fixture-source.txt", summary="Explicit revised original unit test fixture", actor="fixture-author",
        element_ids=["design_records"])
    assert again["success"]
    changed = next(r for r in ppap.list_evidence(task_id, include_payload=True)["items"] if r["source_id"] == "FIXTURE-DESIGN-ORIGINAL")
    assert changed["evidence_id"] == row["evidence_id"]
    assert changed["content_hash"] != row["content_hash"]


def test_unscoped_human_original_is_not_claimed_for_all_ppap_requirements():
    task_id = create_evidence_task()
    ppap_storage.write_bytes("sources/fixture-source.txt", b"Explicit fixture unscoped original")
    registered = ppap.register_human_evidence(task_id, source_type="user_upload", source_id="FIXTURE-UNSCOPED",
        locator="sources/fixture-source.txt", summary="Explicit unscoped original fixture", actor="fixture-author")
    if not registered.get("success"):
        assert registered.get("code") in ("element_scope_required", "invalid_element_scope", "element_ids_required")
        return
    row = next(r for r in ppap.list_evidence(task_id, include_payload=True)["items"] if r["source_id"] == "FIXTURE-UNSCOPED")
    assert row["element_ids"] == []
    context = {"elements": [{"element_id": "customer_engineering_approval", "requirement": "submit", "status": "WAITING_DATA"}],
               "evidence": [row]}
    assert ppap_checks.blocking_findings(ppap_checks.check_evidence(context))


@pytest.mark.parametrize("version,expected", [("FIXTURE-V1", False), ("FIXTURE-V2", True), ("", True)])
def test_drawing_revision_is_explicit_not_derived_from_file_hash(version, expected):
    context = {"task": {"drawing_version": "FIXTURE-V1"},
        "elements": [{"element_id": "design_records", "requirement": "submit", "status": "DRAFTED"}],
        "evidence": [{"evidence_id": "FIXTURE-EVID", "source_type": "user_upload", "source_id": "FIXTURE-DESIGN",
            "source_version": version, "content_hash": "abcdef" * 10 + "abcd", "element_ids": ["design_records"]}]}
    findings = ppap_checks.check_versions(context)
    assert bool(ppap_checks.blocking_findings(findings)) == expected


def test_source_content_version_not_misread_as_drawing_revision():
    context = {"task": {"drawing_version": "FIXTURE-V1"},
        "elements": [{"element_id": "design_records", "requirement": "retain", "status": "DRAFTED"}],
        "evidence": [{"source_type": "erp_sales_order", "source_id": "FIXTURE-ERP",
            "source_version": "abcdef1234567890", "element_ids": ["design_records"]}]}
    assert ppap_checks.check_versions(context) == []
