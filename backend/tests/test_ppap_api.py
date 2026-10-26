"""PPAP route authorization, actor ownership and download boundaries (AC11)."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.identity import RealIdentity, ROLE_REQUIREMENTS


POST_CASES = [
    ("/tasks", {"customer_name": "客户输入", "part_no": "PART-LEFT", "part_name": "左饰板", "part_side": "LEFT"}),
    ("/tasks/T", {"patch": {"drawing_version": "B"}}),
    ("/tasks/T/applicability", {"decisions": [{"element_id": "psw", "requirement": "submit", "basis": "客户要求"}]}),
    ("/tasks/T/evidence/collect", {}),
    ("/tasks/T/evidence", {"source_type": "user_upload", "source_id": "FILE1", "summary": "图纸", "locator": "external-file-1"}),
    ("/tasks/T/inputs", {"dataset": "product_base", "payload": {}, "source_ref": "确认记录"}),
    ("/tasks/T/compile", {}),
    ("/tasks/T/checks", {}),
    ("/tasks/T/issues/I/resolve", {"note": "已修正"}),
    ("/tasks/T/reviews/submit", {}),
    ("/tasks/T/reviews/R/decide", {"decision": "APPROVED", "note": "审阅意见"}),
    ("/tasks/T/archive", {}),
    ("/tasks/T/customer-state", {"state": "SUBMITTED", "reference": "客户收件记录"}),
]


@pytest.fixture
def client(monkeypatch):
    # These tests isolate PPAP role/dependency contracts; perimeter is covered by test_browser_security.
    monkeypatch.setenv("PROJECT_AUTH_REQUIRE_LOGIN", "false")
    # No lifespan is started: API tests must not migrate or probe deployed systems.
    return TestClient(main.app)


@pytest.fixture
def quality_session(monkeypatch, client):
    identity = RealIdentity("quality-reviewer", "质量审核人", ("quality_manager",), "Project", "project")
    monkeypatch.setattr(main, "resolve_project_token", lambda token: identity if token == "test-session" else None)
    client.cookies.set(main.PROJECT_SESSION_COOKIE, "test-session")
    return identity


@pytest.mark.parametrize("path,payload", POST_CASES)
def test_every_persistent_route_rejects_anonymous(client, monkeypatch, path, payload):
    monkeypatch.setattr(main, "resolve_project_token", lambda token: None)
    response = client.post("/api/ppap" + path, json=payload)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "project_login_required"


def test_route_dependencies_do_not_relax_catalog_or_session_gates():
    routes = [route for route in main.app.routes if getattr(route, "path", "").startswith("/api/ppap/")]
    assert len(routes) >= 20
    for route in routes:
        dependencies = {getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies}
        if "POST" in route.methods:
            assert {"require_real_write_access", "require_real_human_identity"} <= dependencies
        if "GET" in route.methods:
            assert "require_real_identity" in dependencies
    assert all(role == "quality_manager" for key, role in ROLE_REQUIREMENTS.items() if key.startswith("ppap_"))


@pytest.mark.parametrize("path", ["/input-assistance?dataset=inspection_value", "/source-change-impact?artifact_id=A", "/missing-items"])
def test_assistance_routes_report_missing_identity_before_reading(client, monkeypatch, path):
    from app.services import identity as identity_service
    monkeypatch.setattr(main, "resolve_project_token", lambda token: None)
    # No project session, bearer or configured service identity. Never probe a
    # deployed upstream system while checking this isolated read-route gate.
    monkeypatch.setattr(identity_service, "resolve_real_identity", AsyncMock(side_effect=ValueError("identity unavailable")))
    response = client.get("/api/ppap/tasks/T" + path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "identity_unavailable"


@pytest.mark.parametrize("path", ["/input-assistance?dataset=inspection_value", "/source-change-impact?artifact_id=A", "/missing-items"])
def test_expired_project_session_cannot_fall_back_to_service_identity(client, monkeypatch, path):
    from app.services import identity as identity_service
    monkeypatch.setattr(main, "resolve_project_token", lambda token: None)
    upstream = AsyncMock()
    monkeypatch.setattr(identity_service, "resolve_real_identity", upstream)
    client.cookies.set(main.PROJECT_SESSION_COOKIE, "isolated-expired-session")
    response = client.get("/api/ppap/tasks/T" + path)
    assert response.status_code == 401
    upstream.assert_not_called()


def test_assistance_routes_forward_explicit_read_only_context(client, quality_session, monkeypatch):
    assist = Mock(return_value={"ok": True, "read_only": True, "suggestions": []})
    monkeypatch.setattr(main.ppap_service, "get_input_assistance", assist)
    response = client.get("/api/ppap/tasks/T/input-assistance", params={"dataset": "inspection_value",
        "part_side": "LEFT", "characteristic_id": "C-1", "record_key": "R-1"})
    assert response.status_code == 200 and response.json()["read_only"]
    assist.assert_called_once_with("T", dataset="inspection_value", part_side="LEFT", characteristic_id="C-1", record_key="R-1")
    impact = Mock(return_value={"ok": True, "read_only": True})
    monkeypatch.setattr(main.ppap_service, "assess_source_change_impact", impact)
    response = client.get("/api/ppap/tasks/T/source-change-impact", params={"artifact_id": "A-1"})
    assert response.status_code == 200
    impact.assert_called_once_with("T", compare_artifact_id="A-1")


def test_actor_impersonation_is_rejected_before_creation(client, monkeypatch, quality_session):
    create = Mock()
    monkeypatch.setattr(main.ppap_service, "create_task", create)
    response = client.post("/api/ppap/tasks", json={**POST_CASES[0][1], "actor": "another-user"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "actor_mismatch"
    create.assert_not_called()


def test_project_session_role_is_required(client, monkeypatch):
    identity = RealIdentity("purchase-user", "采购", ("purchase_manager",), "Project", "project")
    monkeypatch.setattr(main, "resolve_project_token", lambda token: identity)
    create = Mock()
    monkeypatch.setattr(main.ppap_service, "create_task", create)
    response = client.post("/api/ppap/tasks", json=POST_CASES[0][1])
    assert response.status_code == 403
    create.assert_not_called()


def test_creator_and_owner_are_taken_from_session(client, monkeypatch, quality_session):
    create = Mock(return_value={"success": True, "task": {"task_id": "T"}})
    monkeypatch.setattr(main.ppap_service, "create_task", create)
    response = client.post("/api/ppap/tasks", json=POST_CASES[0][1])
    assert response.status_code == 200
    assert create.call_args.kwargs["actor"] == "quality-reviewer"
    assert create.call_args.kwargs["owner"] == "quality-reviewer"
    assert create.call_args.kwargs["data_mode"] == "REAL"
    assert response.json()["authenticated_identity"]["authority"] == "Project"


def test_task_mode_creation_and_filter_are_explicit_and_validated(client, monkeypatch, quality_session):
    create = Mock(return_value={"success": True, "task": {"task_id": "T", "data_mode": "TEST"}})
    listing = Mock(return_value={"ok": True, "items": [], "total": 0})
    monkeypatch.setattr(main.ppap_service, "create_task", create)
    monkeypatch.setattr(main.ppap_service, "list_tasks", listing)
    assert client.post("/api/ppap/tasks", json={**POST_CASES[0][1], "mode": "TEST"}).status_code == 200
    assert create.call_args.kwargs["data_mode"] == "TEST"
    assert client.get("/api/ppap/tasks?mode=TEST").status_code == 200
    assert listing.call_args.kwargs["mode"] == "TEST"
    create.reset_mock()
    listing.reset_mock()
    assert client.post("/api/ppap/tasks", json={**POST_CASES[0][1], "mode": "custom"}).status_code == 422
    assert client.get("/api/ppap/tasks?mode=custom").status_code == 422
    create.assert_not_called()
    listing.assert_not_called()


def test_human_evidence_explicit_element_binding_is_forwarded(client, monkeypatch, quality_session):
    register = Mock(return_value={"success": True, "element_ids": ["design_records"]})
    monkeypatch.setattr(main.ppap_service, "register_human_evidence", register)
    payload = {**POST_CASES[4][1], "element_ids": ["design_records"], "document_version": "FIXTURE-V1"}
    response = client.post("/api/ppap/tasks/T/evidence", json=payload)
    assert response.status_code == 200
    assert register.call_args.kwargs["element_ids"] == ["design_records"]
    assert register.call_args.kwargs["document_version"] == "FIXTURE-V1"
    assert register.call_args.kwargs["actor"] == "quality-reviewer"
    register.reset_mock()
    response = client.post("/api/ppap/tasks/T/evidence", json=POST_CASES[4][1])
    assert response.status_code == 200
    assert register.call_args.kwargs["element_ids"] == []


def test_unknown_evidence_element_rejection_is_not_hidden(client, monkeypatch, quality_session):
    register = Mock(return_value={"success": False, "code": "unknown_element", "error": "不支持的原件用途"})
    monkeypatch.setattr(main.ppap_service, "register_human_evidence", register)
    response = client.post("/api/ppap/tasks/T/evidence", json={**POST_CASES[4][1], "element_ids": ["unknown"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_element"
    assert register.call_args.kwargs["element_ids"] == ["unknown"]


def test_request_body_cannot_supply_a_review_identity(client, quality_session, monkeypatch):
    decide = Mock()
    monkeypatch.setattr(main.ppap_service, "decide_review", decide)
    response = client.post("/api/ppap/tasks/T/reviews/R/decide",
                           json={"decision": "APPROVED", "note": "意见", "reviewer": "another-user"})
    assert response.status_code == 422
    decide.assert_not_called()


def test_review_decision_records_authenticated_reviewer(client, quality_session, monkeypatch):
    decide = Mock(return_value={"success": True, "review": {"reviewer": "quality-reviewer"}})
    monkeypatch.setattr(main.ppap_service, "decide_review", decide)
    response = client.post("/api/ppap/tasks/T/reviews/R/decide",
                           json={"decision": "RETURNED", "note": "资料待补充"})
    assert response.status_code == 200
    decide.assert_called_once_with("T", "R", actor="quality-reviewer", decision="RETURNED",
                                   note="资料待补充", reviewer_role="quality_manager")


def test_compile_idempotency_header_is_forwarded(client, quality_session, monkeypatch):
    compile_task = Mock(return_value={"success": True, "idempotent_replay": True})
    monkeypatch.setattr(main.ppap_service, "compile_task", compile_task)
    response = client.post("/api/ppap/tasks/T/compile", json={}, headers={"Idempotency-Key": "request-1"})
    assert response.status_code == 200
    compile_task.assert_called_once_with("T", actor="quality-reviewer", request_key="request-1")
    compile_task.reset_mock()
    response = client.post("/api/ppap/tasks/T/compile", json={"request_key": "body-key"},
                           headers={"Idempotency-Key": "header-key"})
    assert response.status_code == 422
    compile_task.assert_not_called()


def test_business_blockers_and_missing_tasks_are_explicit_http_failures(client, quality_session, monkeypatch):
    monkeypatch.setattr(main.ppap_service, "submit_review", lambda *a, **kw: {
        "success": False, "ok": False, "code": "approval_blocked", "error": "尚有阻断项", "blockers": ["B1"]})
    response = client.post("/api/ppap/tasks/T/reviews/submit", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["blockers"] == ["B1"]
    monkeypatch.setattr(main.ppap_service, "get_task", lambda *a, **kw: {"ok": False, "error": "task_not_found"})
    response = client.get("/api/ppap/tasks/MISSING")
    assert response.status_code == 404


def test_download_uses_only_hash_verified_bytes_and_hides_private_path(client, quality_session, monkeypatch):
    monkeypatch.setattr(main.ppap_service, "read_artifact", lambda *a, **kw: {
        "ok": True, "artifact": {"file_name": "零件资料包.xlsx"}, "path": "private-storage-path",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content": b"verified-file-content",
    })
    response = client.get("/api/ppap/tasks/T/artifacts/A/download")
    assert response.status_code == 200
    assert response.content == b"verified-file-content"
    assert "private-storage-path" not in response.text
    assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store"
    monkeypatch.setattr(main.ppap_service, "read_artifact", lambda *a, **kw: {
        "ok": False, "code": "artifact_hash_mismatch", "error": "文件不一致"})
    response = client.get("/api/ppap/tasks/T/artifacts/A/download")
    assert response.status_code == 409
