"""Public browser boundary and per-user assistant data isolation (offline SQLite)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from starlette.responses import StreamingResponse

from app import main
from app.persistence.database import SessionLocal
from app.persistence.models import AssistantSessionRow, AssistantMessageRow, RealAgentRunRow
from app.services.browser_security import BrowserSecurityMiddleware, browser_actor, request_limiter
from app.services.identity import RealIdentity
from app.services.assistant_session import _get_or_create_session


def identity(actor="alice"):
    return RealIdentity(actor, actor, ("quality_manager",), "Project", "project")


@pytest.fixture(autouse=True)
def strict_boundary(monkeypatch):
    monkeypatch.setenv("PROJECT_AUTH_REQUIRE_LOGIN", "true")
    monkeypatch.setenv("PROJECT_AUTH_ALLOWED_ORIGINS", "https://factory.example")
    for name in ("PROJECT_LOGIN_ATTEMPTS_PER_MINUTE", "PROJECT_ASSISTANT_REQUESTS_PER_MINUTE", "PROJECT_ASSISTANT_REQUESTS_PER_DAY", "PROJECT_ASSISTANT_MAX_CONCURRENT", "PROJECT_ASSISTANT_GLOBAL_CONCURRENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "resolve_project_token", lambda token: identity(token) if token in {"alice", "bob"} else None)
    request_limiter.clear()
    yield
    request_limiter.clear()


@contextmanager
def actor_context(actor):
    token = browser_actor.set(actor)
    try:
        yield
    finally:
        browser_actor.reset(token)


def seed(owner):
    sid = "ASST-SEC-" + uuid4().hex[:14]
    with SessionLocal() as db:
        db.add(AssistantSessionRow(session_id=sid, title="private", owner_subject=owner))
        db.add(AssistantMessageRow(session_id=sid, role="user", content="private text"))
        db.add(RealAgentRunRow(run_id="RUN-"+sid, agent_type="coordinator", operation="ask", session_id=sid, input_json={}, result_json={}))
        db.commit()
    return sid


@pytest.mark.asyncio
async def test_anonymous_cannot_read_business_or_use_paid_model(monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr("app.services.assistant_session.handle_ask", handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example") as client:
        for path in ("/api/real-orders/assistant/sessions", "/api/real-orders/agent-runs", "/api/real-orders/erp/sales-orders", "/api/mes/work-orders", "/api/ppap/tasks", "/openapi.json", "/aip/quotation/rpc"):
            response = await client.get(path)
            assert response.status_code == 401, (path, response.text)
        for path in ("/api/real-orders/assistant/ask", "/api/real-orders/assistant/ask/stream"):
            assert (await client.post(path, json={"question": "hello"})).status_code == 401
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_default_policy_is_enabled_without_environment_flag(monkeypatch):
    monkeypatch.delenv("PROJECT_AUTH_REQUIRE_LOGIN")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example") as client:
        assert (await client.get("/api/real-orders/assistant/sessions")).status_code == 401
        assert (await client.get("/api/runtime/surface")).status_code == 200


@pytest.mark.asyncio
async def test_session_list_messages_and_runs_are_private():
    alice, bob, legacy = seed("alice"), seed("bob"), seed(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", headers={"Authorization": "Bearer alice"}) as client:
        listed = (await client.get("/api/real-orders/assistant/sessions?limit=50")).json()
        ids = {v["session_id"] for v in listed["sessions"]}
        assert alice in ids and bob not in ids and legacy not in ids
        assert (await client.get(f"/api/real-orders/assistant/sessions/{alice}/messages")).json()["messages"][0]["content"] == "private text"
        for sid in (bob, legacy, "missing"):
            assert (await client.get(f"/api/real-orders/assistant/sessions/{sid}/messages")).status_code == 404
            for path in ("/api/real-orders/assistant/ask", "/api/real-orders/assistant/ask/stream"):
                assert (await client.post(path, json={"question": "hello", "session_id": sid})).status_code == 404
        assert (await client.get(f"/api/real-orders/agent-runs/RUN-{bob}")).status_code == 404
        runs = (await client.get("/api/real-orders/agent-runs?limit=200")).json()
        assert not any(v["run_id"] == "RUN-"+bob for v in runs)


def test_new_browser_session_records_owner_and_existing_cannot_be_claimed():
    with actor_context("alice"):
        row, created = _get_or_create_session(None, "owned question")
        assert created and row.owner_subject == "alice"
    with actor_context("bob"), pytest.raises(PermissionError):
        _get_or_create_session(row.session_id, "steal")
    legacy = seed(None)
    with actor_context("alice"), pytest.raises(PermissionError):
        _get_or_create_session(legacy, "claim")


@pytest.mark.asyncio
async def test_cross_origin_or_missing_origin_cookie_writes_rejected(monkeypatch):
    handler = AsyncMock(return_value={"answer": "ok"})
    monkeypatch.setattr("app.services.assistant_session.handle_ask", handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", cookies={"project_session": "alice"}) as client:
        for origin in ("https://evil.example", "null", "https://factory.example.evil"):
            response = await client.post("/api/real-orders/assistant/ask", json={"question": "hello"}, headers={"Origin": origin})
            assert response.status_code == 403
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "hello"})).status_code == 403
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "hello"}, headers={"Origin": "https://factory.example"})).status_code == 200
    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_forwarded_headers_do_not_allow_forged_origin(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", cookies={"project_session": "alice"}) as client:
        response = await client.post("/api/auth/logout", json={}, headers={"Origin": "https://evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_login_attempt_limit_is_per_client_and_blocks_before_password_work(monkeypatch):
    monkeypatch.setenv("PROJECT_LOGIN_ATTEMPTS_PER_MINUTE", "2")
    login = AsyncMock()
    # Invalid login payloads still consume an attempt, preventing validation bypass.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("198.51.100.10", 1)), base_url="https://factory.example") as client:
        assert (await client.post("/api/auth/login", json={})).status_code == 422
        assert (await client.post("/api/auth/login", json={})).status_code == 422
        blocked = await client.post("/api/auth/login", json={})
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("198.51.100.11", 1)), base_url="https://factory.example") as other:
        assert (await other.post("/api/auth/login", json={})).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("variable", ["PROJECT_ASSISTANT_REQUESTS_PER_MINUTE", "PROJECT_ASSISTANT_REQUESTS_PER_DAY"])
async def test_assistant_rate_and_daily_limits(monkeypatch, variable):
    monkeypatch.setenv(variable, "1")
    handler = AsyncMock(return_value={"answer": "ok"})
    monkeypatch.setattr("app.services.assistant_session.handle_ask", handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", headers={"Authorization": "Bearer alice"}) as client:
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "hello"})).status_code == 200
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "hello"})).status_code == 429
    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_stream_holds_concurrency_lease_until_body_finishes():
    started, finish = asyncio.Event(), asyncio.Event()
    async def body():
        started.set()
        yield b"event: step\ndata: {}\n\n"
        await finish.wait()
        yield b"event: done\ndata: {}\n\n"
    app = FastAPI()
    @app.post("/api/real-orders/assistant/ask/stream")
    def stream():
        return StreamingResponse(body(), media_type="text/event-stream")
    app.add_middleware(BrowserSecurityMiddleware, token_resolver=lambda token: identity(token) if token else None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://factory.example", headers={"Authorization": "Bearer alice"}) as client:
        pending = asyncio.create_task(client.post("/api/real-orders/assistant/ask/stream", json={"question": "hello"}))
        await asyncio.wait_for(started.wait(), timeout=2)
        assert (await client.post("/api/real-orders/assistant/ask/stream", json={"question": "again"})).status_code == 429
        finish.set()
        assert (await pending).status_code == 200
        assert (await client.post("/api/real-orders/assistant/ask/stream", json={"question": "after"})).status_code == 200


@pytest.mark.asyncio
async def test_body_size_and_question_length_rejected_before_model(monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr("app.services.assistant_session.handle_ask", handler)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", headers={"Authorization": "Bearer alice"}) as client:
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "a"*8001})).status_code == 422
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "a", "context": "b"*70000})).status_code == 413
    handler.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_request_context_assigns_session_owner_in_sync_and_stream(monkeypatch, stream):
    async def handle(*args, **kwargs):
        row, _ = _get_or_create_session(None, "context owner")
        return {"answer": "ok", "session_id": row.session_id}
    monkeypatch.setattr("app.services.assistant_session.handle_ask", handle)
    path = "/api/real-orders/assistant/ask" + ("/stream" if stream else "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", headers={"Authorization": "Bearer alice"}) as client:
        response = await client.post(path, json={"question": "hello"})
    assert response.status_code == 200
    if stream:
        import json
        lines = response.text.splitlines()
        result = json.loads(next(v[6:] for v in lines if v.startswith("data: ")))
    else:
        result = response.json()
    with SessionLocal() as db:
        assert db.get(AssistantSessionRow, result["session_id"]).owner_subject == "alice"


@pytest.mark.asyncio
async def test_https_login_sets_secure_http_only_session_cookie(monkeypatch):
    monkeypatch.setenv("PROJECT_AUTH_COOKIE_SECURE", "true")
    monkeypatch.setattr(main, "login_project_user", lambda u, p: ("alice", identity("alice")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example") as client:
        response = await client.post("/api/auth/login", json={"username": "fixture", "password": "fixture-only"}, headers={"Origin": "https://factory.example"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_websocket_handshake_requires_project_identity():
    app = FastAPI()
    @app.websocket("/api/events")
    async def events(ws: WebSocket):
        await ws.accept()
        await ws.close()
    app.add_middleware(BrowserSecurityMiddleware, token_resolver=lambda token: None)
    sent = []
    async def send(event):
        sent.append(event)
    async def receive():
        return {"type": "websocket.connect"}
    await app({"type": "websocket", "path": "/api/events", "scheme": "wss", "headers": [(b"host", b"factory.example")], "query_string": b"", "client": ("127.0.0.1", 1)}, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008, "reason": "project_login_required"}]


@pytest.mark.asyncio
async def test_cors_preflight_does_not_need_session_but_grants_no_data():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.options("/api/real-orders/assistant/ask", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert (await client.post("/api/real-orders/assistant/ask", json={"question": "hello"})).status_code == 401


@pytest.mark.asyncio
async def test_project_bearer_identity_is_not_forwarded_to_upstream(monkeypatch):
    upstream = AsyncMock(side_effect=AssertionError("project token must stay local"))
    monkeypatch.setattr("app.services.identity.resolve_real_identity", upstream)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://factory.example", headers={"Authorization": "Bearer alice"}) as client:
        response = await client.get("/api/real-orders/identity/me")
    assert response.status_code == 200 and response.json()["provider"] == "project"
    assert response.json()["actor_id"] == "alice"
    upstream.assert_not_called()
