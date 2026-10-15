"""Synthetic seed contracts and network boundaries, using fixture-only HTTP.

No test connects to the deployed application, loads real login configuration,
or writes to any ERP/MES adapter. Credentials below are explicit test fixtures.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging

import httpx
import pytest

import seed_ppap
from app.services import ppap_calc, ppap_inputs, ppap_template


LABEL = "FIXTURE_20261014"
FIXTURE_USERNAME = "fixture-quality-user"
FIXTURE_PASSWORD = "FIXTURE-PASSWORD-NOT-A-REAL-CREDENTIAL"
FIXTURE_COOKIE = "FIXTURE-SESSION-NOT-A-REAL-SESSION"


def input_records(case: str) -> list[dict]:
    return seed_ppap.synthetic_inputs(seed_ppap.task_fields(case, LABEL), LABEL)


def transport_api(handler):
    return seed_ppap.SeedApi("http://127.0.0.1:9000", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("case", ["left", "right"])
def test_all_13_datasets_validate_against_actual_input_contract(case):
    records = input_records(case)
    assert {row["dataset"] for row in records} == set(ppap_template.ALL_DATASETS)
    assert len(ppap_template.ALL_DATASETS) == 13
    assert len({(row["dataset"], row["record_key"], row["part_side"]) for row in records}) == len(records)
    for row in records:
        result = ppap_inputs.validate_input(row["dataset"], row["payload"], part_side=row["part_side"])
        assert result["ok"], (row["dataset"], result["errors"])
        assert result["payload"] == row["payload"]
        assert row["source_type"] == seed_ppap.SOURCE_TYPE
        assert LABEL in row["source_ref"]
        assert seed_ppap.TEST_NOTICE in row["source_ref"]


def test_left_and_right_use_same_characteristics_with_independent_values():
    left, right = input_records("left"), input_records("right")
    chars = lambda rows: {row["payload"]["characteristic_id"]: row["payload"] for row in rows if row["dataset"] == "characteristic"}
    left_chars, right_chars = chars(left), chars(right)
    assert set(left_chars) == set(right_chars)
    assert all(row["part_side"] == "LEFT" for row in left)
    assert all(row["part_side"] == "RIGHT" for row in right)
    for characteristic_id in left_chars:
        assert left_chars[characteristic_id]["nominal"] != right_chars[characteristic_id]["nominal"]
    assert {row["record_key"] for row in left}.isdisjoint({row["record_key"] for row in right})
    for case, rows, character_map in (("left", left, left_chars), ("right", right, right_chars)):
        readings = []
        for row in rows:
            if row["dataset"] != "inspection_value":
                continue
            reading = deepcopy(row["payload"])
            characteristic = character_map[reading["characteristic_id"]]
            reading.update({name: characteristic[name] for name in ("lsl", "usl", "nominal")})
            reading["source_ref"] = row["source_ref"]
            readings.append(reading)
        result = ppap_calc.dimensional_results(readings)
        assert result["summary"]["ng"] == (1 if case == "left" else 0)
        assert result["summary"]["missing_value"] == (1 if case == "left" else 0)
        assert result["summary"]["ok"] == (4 if case == "left" else 6)


def test_seed_never_injects_verdicts_even_inside_nested_payloads():
    def walk(value):
        if isinstance(value, dict):
            assert set(value).isdisjoint(ppap_inputs.VERDICT_FIELDS)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    for case in ("left", "right"):
        for record in input_records(case):
            walk(record["payload"])
            injected = {**record["payload"], "verdict": "PASS"}
            assert not ppap_inputs.validate_input(record["dataset"], injected, part_side=record["part_side"])["ok"]


@pytest.mark.parametrize("case", ["left", "right"])
def test_research_seeds_are_raw_supported_calculation_inputs(case):
    rows = {row["dataset"]: row["payload"] for row in input_records(case)}
    msa = rows["msa_study"]
    assert msa["method"] == "average_range"
    result = ppap_calc.average_range_grr(msa["measurements"], tolerance=msa["tolerance"])
    assert result["state"] == "OK"
    assert result["value"]["percent_grr"] is not None
    study = rows["process_study"]
    assert study["values"] == [value for group in study["subgroups"] for value in group]
    result = ppap_calc.capability_indices(study["values"], usl=study["usl"], lsl=study["lsl"],
                                         target=study["target"], subgroups=study["subgroups"])
    assert result["state"] == "INCOMPLETE"
    assert "small_sample" in result["flags"]
    assert all(result["value"][metric] is not None for metric in ("cp", "cpk", "pp", "ppk"))
    assert ppap_calc.classify_metric("cpk", result["value"]["cpk"], {})["acceptance_state"] == "NEEDS_CONFIRMATION"


def test_right_applicability_preserves_unconfirmed_studies_without_fabricating_originals():
    decisions = seed_ppap.applicability("right", list(ppap_template.ELEMENT_IDS))
    by_id = {row["element_id"]: row for row in decisions}
    assert set(by_id) == set(ppap_template.ELEMENT_IDS)
    for row in decisions:
        assert row["basis"] and seed_ppap.TEST_NOTICE in row["basis"]
    for element_id in ("submission_checklist", "psw", "attachment_index"):
        assert by_id[element_id]["requirement"] == "submit"
    for element_id in ("msa", "initial_process_study"):
        assert by_id[element_id]["requirement"] == "retain"
    for spec in ppap_template.ELEMENTS:
        if spec.evidence_types:
            assert by_id[spec.element_id]["requirement"] == "on_request"
    assert all(row["requirement"] == "submit" for row in seed_ppap.applicability("left", list(ppap_template.ELEMENT_IDS)))


@pytest.mark.parametrize("url", ["http://127.0.0.1:9000", "http://localhost:9000/", "http://[::1]:9000"])
def test_only_loopback_http_base_addresses_are_accepted(url):
    assert seed_ppap.checked_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:9000", "http://example.com:9000", "http://192.168.1.1:9000",
    "http://localhost.example.com:9000", "http://0.0.0.0:9000", "file:///tmp/app",
    "http://fixture:secret@localhost:9000", "http://localhost:9000/api",
    "http://localhost:9000/?password=fixture", "http://localhost:9000/#fixture",
])
def test_non_loopback_or_credential_containing_base_is_rejected(url):
    with pytest.raises(seed_ppap.SeedError):
        seed_ppap.checked_base_url(url)


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/ppap/templates"), ("GET", "/api/ppap/tasks"),
    ("GET", "/api/ppap/tasks/TEST-T"), ("GET", "/api/ppap/tasks/TEST-T/versions"),
    ("GET", "/api/ppap/tasks/TEST-T/issues"), ("GET", "/api/ppap/tasks/TEST-T/runs"),
    ("POST", "/api/ppap/tasks"), ("POST", "/api/ppap/tasks/TEST-T/applicability"),
    ("POST", "/api/ppap/tasks/TEST-T/inputs"), ("POST", "/api/ppap/tasks/TEST-T/compile"),
    ("POST", "/api/ppap/tasks/TEST-T/checks"),
])
def test_expected_seed_routes_use_the_local_http_client(method, path):
    calls = []
    def handler(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"ok": True})
    api = transport_api(handler)
    try:
        assert api.request(method, path, body={"fixture": "test-only"}) == {"ok": True}
        assert calls == [(method, "http://127.0.0.1:9000" + path)]
    finally:
        api.close()


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/api/erp/orders"), ("POST", "/api/mes/work-orders"),
    ("POST", "/api/real/write"), ("POST", "/api/ppap/tasks/TEST-T/reviews/submit"),
    ("POST", "/api/ppap/tasks/TEST-T/reviews/R/decide"),
    ("POST", "/api/ppap/tasks/TEST-T/archive"), ("POST", "/api/ppap/tasks/TEST-T/customer-state"),
    ("POST", "/api/ppap/tasks/TEST-T/evidence/collect"),
    ("POST", "/api/ppap/tasks/TEST-T/evidence"),
    ("POST", "/api/ppap/tasks/TEST-T/issues/I/resolve"),
    ("POST", "/api/ppap/templates"), ("POST", "/api/ppap/unknown"),
    ("GET", "/api/ppap/unknown"), ("DELETE", "/api/ppap/tasks/TEST-T"),
    ("PUT", "/api/ppap/tasks/TEST-T/inputs"), ("PATCH", "/api/ppap/tasks/TEST-T"),
    ("GET", "/api/ppap/../../erp/orders"),
    ("POST", "/api/ppap/tasks/../../erp/inputs"),
    ("POST", "/api/ppap/tasks/%2e%2e/inputs"),
    ("POST", "/api/ppap/tasks/TEST%2fOTHER/inputs"),
    ("GET", "http://example.com/api/ppap/tasks"),
])
def test_forbidden_actions_and_path_traversal_are_rejected_before_transport(method, path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})
    api = transport_api(handler)
    try:
        with pytest.raises(seed_ppap.SeedError):
            api.request(method, path, body={"fixture": "never-send-this"})
        assert calls == [], "Forbidden actions must be rejected before any HTTP request"
    finally:
        api.close()


def test_login_uses_real_auth_endpoint_and_session_without_logging_fixtures(capsys, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/api/auth/login":
            assert request.method == "POST"
            assert json.loads(request.content) == {"username": FIXTURE_USERNAME, "password": FIXTURE_PASSWORD}
            return httpx.Response(200, json={"identity": {"provider": "project", "roles": ["quality_manager"]}},
                                  headers={"set-cookie": f"project_session={FIXTURE_COOKIE}; Path=/"})
        if request.url.path == "/api/ppap/templates":
            assert FIXTURE_COOKIE in request.headers.get("cookie", "")
            return httpx.Response(200, json={"template": {"elements": []}})
        assert request.url.path == "/api/auth/logout" and request.method == "POST"
        return httpx.Response(200, json={"ok": True})
    api = transport_api(handler)
    try:
        api.login(FIXTURE_USERNAME, FIXTURE_PASSWORD)
        api.request("GET", "/api/ppap/templates")
    finally:
        api.close()
    assert [request.url.path for request in calls] == ["/api/auth/login", "/api/ppap/templates", "/api/auth/logout"]
    output = capsys.readouterr()
    logged = output.out + output.err + caplog.text
    assert all(value not in logged for value in (FIXTURE_USERNAME, FIXTURE_PASSWORD, FIXTURE_COOKIE))


@pytest.mark.parametrize(("provider", "roles"), [("project", ["purchase_manager"]), ("openmes", ["quality_manager"])])
def test_login_rejects_accounts_without_real_project_quality_authority(provider, roles):
    def handler(request):
        return httpx.Response(200, json={"identity": {"provider": provider, "roles": roles}})
    api = transport_api(handler)
    try:
        with pytest.raises(seed_ppap.SeedError, match="quality_manager"):
            api.login(FIXTURE_USERNAME, FIXTURE_PASSWORD)
    finally:
        api.close()


@pytest.mark.parametrize("network_error", [False, True])
def test_main_login_failure_never_echoes_credentials_or_response_body(tmp_path, monkeypatch, capsys, caplog, network_error):
    caplog.set_level(logging.DEBUG)
    def handler(request):
        assert request.url.path == "/api/auth/login"
        if network_error:
            raise httpx.ConnectError(f"fixture-only error {FIXTURE_USERNAME} {FIXTURE_PASSWORD}", request=request)
        return httpx.Response(401, json={"detail": f"fixture-only rejection {FIXTURE_USERNAME} {FIXTURE_PASSWORD}"},
                              headers={"set-cookie": f"project_session={FIXTURE_COOKIE}; Path=/"})
    api_class = seed_ppap.SeedApi
    monkeypatch.setattr(seed_ppap, "SeedApi", lambda base: api_class(base, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(seed_ppap, "BACKEND_ROOT", tmp_path / "backend")
    monkeypatch.setenv("PROJECT_AUTH_USERNAME", FIXTURE_USERNAME)
    monkeypatch.setenv("PROJECT_AUTH_PASSWORD", FIXTURE_PASSWORD)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    assert seed_ppap.main(["--label", LABEL]) == 1
    output = capsys.readouterr()
    logged = output.out + output.err + caplog.text
    assert all(value not in logged for value in (FIXTURE_USERNAME, FIXTURE_PASSWORD, FIXTURE_COOKIE))
    assert not (tmp_path / "backend" / "data" / "ppap_seed" / LABEL / "manifest.json").exists()


@pytest.mark.parametrize("case", ["left", "right", "missing"])
def test_same_batch_existing_seed_is_read_only_and_not_overwritten(case, tmp_path):
    fields = seed_ppap.task_fields(case, LABEL)
    original_fields = deepcopy(fields)
    fixture_bytes = b"PK-fixture-only-existing-workbook-bytes"
    artifact = {"artifact_id": "TEST-ARTIFACT", "version": 1,
                "content_hash": hashlib.sha256(fixture_bytes).hexdigest()}
    human_edited_input = {"dataset": "customer_handling", "payload": {"note": "fixture human edit, preserve it"}}
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET", "An existing seed must never trigger a write request"
        if request.url.path == "/api/ppap/tasks":
            assert request.url.params["part_no"] == fields["part_no"]
            return httpx.Response(200, json={"total": 1, "items": [{**fields, "task_id": "TEST-EXISTING"}]})
        if request.url.path == "/api/ppap/tasks/TEST-EXISTING":
            return httpx.Response(200, json={"task": {"run_state": "FIXTURE-HUMAN-EDITED"}, "inputs": [human_edited_input]})
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json={"total": 1, "items": [artifact]})
        if request.url.path.endswith("/issues"):
            return httpx.Response(200, json={"total": 0, "items": []})
        if request.url.path.endswith("/runs"):
            # The real runs API has no total field.
            return httpx.Response(200, json={"items": [{"run_id": "TEST-EXISTING-RUN"}]})
        assert request.url.path == "/api/ppap/tasks/TEST-EXISTING/artifacts/TEST-ARTIFACT/download"
        return httpx.Response(200, content=fixture_bytes)
    api = transport_api(handler)
    try:
        result = seed_ppap.seed_case(api, case, LABEL, list(ppap_template.ELEMENT_IDS), tmp_path)
        assert result["reused_existing"] is True
        assert result["task_id"] == "TEST-EXISTING"
        assert result["state"] == "FIXTURE-HUMAN-EDITED"
        assert result["input_count"] == 1
        assert result["runs"] == 1
        assert result["idempotence_verified"] is False
        assert [path for method, path in calls] == [
            "/api/ppap/tasks", "/api/ppap/tasks/TEST-EXISTING",
            "/api/ppap/tasks/TEST-EXISTING/versions", "/api/ppap/tasks/TEST-EXISTING/issues",
            "/api/ppap/tasks/TEST-EXISTING/runs",
            "/api/ppap/tasks/TEST-EXISTING/artifacts/TEST-ARTIFACT/download",
        ]
        assert all(method == "GET" for method, path in calls)
        assert fields == original_fields
        assert human_edited_input["payload"]["note"] == "fixture human edit, preserve it"
        assert (tmp_path / "TEST-EXISTING-v1.xlsx").read_bytes() == fixture_bytes
    finally:
        api.close()


@pytest.mark.parametrize(("task_id", "artifact_id", "version"), [
    ("../outside", "TEST-ARTIFACT", 1), ("TEST-T", "../outside", 1),
    ("TEST-T", "TEST/OTHER", 1), ("TEST-T", "%2e%2e", 1),
    ("TEST-T", "TEST-ARTIFACT", "../outside"),
])
def test_download_rejects_ids_or_versions_that_could_escape_seed_scope(task_id, artifact_id, version, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"PK-fixture-only")
    api = transport_api(handler)
    try:
        with pytest.raises(seed_ppap.SeedError):
            seed_ppap.download_verified(api, task_id, {"artifact_id": artifact_id, "version": version,
                                                       "content_hash": "unused-fixture-hash"}, tmp_path)
        assert calls == []
        assert list(tmp_path.iterdir()) == []
    finally:
        api.close()


def test_download_rejects_changed_file_bytes_without_writing_output(tmp_path):
    expected_bytes = b"PK-fixture-original"
    api = transport_api(lambda request: httpx.Response(200, content=b"PK-fixture-tampered"))
    try:
        with pytest.raises(seed_ppap.SeedError, match="指纹"):
            seed_ppap.download_verified(api, "TEST-T", {"artifact_id": "TEST-ARTIFACT", "version": 1,
                "content_hash": hashlib.sha256(expected_bytes).hexdigest()}, tmp_path)
        assert list(tmp_path.iterdir()) == []
    finally:
        api.close()


def test_ppap_redirect_is_rejected_without_following_external_location():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://example.com/erp"})
    api = transport_api(handler)
    try:
        with pytest.raises(seed_ppap.SeedError, match="HTTP 302"):
            api.request("GET", "/api/ppap/tasks")
        assert calls == ["http://127.0.0.1:9000/api/ppap/tasks"]
    finally:
        api.close()


@pytest.mark.parametrize("conflict", ["other-batch", "unmarked-existing", "duplicate"])
def test_same_part_number_from_another_batch_or_ambiguous_match_stops(conflict, tmp_path):
    fields = seed_ppap.task_fields("right", LABEL)
    existing = {**fields, "task_id": "TEST-EXISTING"}
    if conflict == "other-batch":
        existing["authorization_ref"] = seed_ppap.task_fields("right", "OTHER_FIXTURE")["authorization_ref"]
    elif conflict == "unmarked-existing":
        existing.pop("authorization_ref")
    rows = [existing, {**existing, "task_id": "TEST-SECOND"}] if conflict == "duplicate" else [existing]
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"total": len(rows), "items": rows})
    api = transport_api(handler)
    try:
        with pytest.raises(seed_ppap.SeedError, match="本批种子"):
            seed_ppap.seed_case(api, "right", LABEL, list(ppap_template.ELEMENT_IDS), tmp_path)
        assert calls == [("GET", "/api/ppap/tasks")]
        assert list(tmp_path.iterdir()) == []
    finally:
        api.close()
