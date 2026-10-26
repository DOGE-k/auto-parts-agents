"""Browser -> coordinator -> real SDK RPC -> login middleware regression tests.

Only LLM responses and final ERP/MES reads are fixtures. The coordinator's skill
invoker, SDK start/poll/complete exchange, AIP services and middleware are real.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app import main
from app.aip.aip_agent_service import register_aip_agent_router
from app.aip.agents.quotation_aip import create_quotation_aip_service
from app.aip.agents.tracking_aip import create_tracking_aip_service
from app.persistence.database import SessionLocal
from app.persistence.models import AssistantSessionRow, RealAgentRunRow
from app.services import coordinator as coordinator_module
from app.services import local_aip_transport as delegation
from app.services.browser_security import BrowserSecurityMiddleware, browser_actor
from app.services.coordinator import BusinessCoordinator
from app.services.identity import RealIdentity


BASE = "http://127.0.0.1:9000"
TOKENS = {"fixture-alice-token": "alice", "fixture-bob-token": "bob"}


def resolve(token):
    actor = TOKENS.get(token)
    return RealIdentity(actor, actor, (), "Project", "project") if actor else None


def tool_call(skill):
    return {"id": skill, "type": "function", "function": {
        "name": skill.replace(".", "__"), "arguments": '{"limit": 5}',
    }}


class QueryLLM:
    def __init__(self):
        self.round = 0

    async def chat_completion(self, *args, **kwargs):
        self.round += 1
        message = ({"role": "assistant", "tool_calls": [
            tool_call("tracking.list_real_work_orders"),
            tool_call("quotation.list_real_sales_orders"),
        ]} if self.round == 1 else {"role": "assistant", "content": "已查询工单和销售订单。"})
        return {"choices": [{"message": message}]}

    async def aclose(self):
        pass


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, app, records):
        self.inner = httpx.ASGITransport(app=app)
        self.records = records

    async def handle_async_request(self, request):
        payload = json.loads(request.content)
        self.records.append({"path": request.url.path,
                             "authorization": request.headers.get("Authorization"),
                             "command": payload["params"]["command"]["command"],
                             "payload": payload})
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


@pytest.fixture
def browser_app(monkeypatch):
    monkeypatch.setenv("PROJECT_AUTH_REQUIRE_LOGIN", "true")
    app = FastAPI()
    app.post("/api/real-orders/assistant/ask")(main.real_order_assistant_ask)
    app.post("/api/real-orders/assistant/ask/stream")(main.real_order_assistant_ask_stream)
    app.add_middleware(BrowserSecurityMiddleware, token_resolver=resolve)
    services = [create_tracking_aip_service(), create_quotation_aip_service()]
    register_aip_agent_router(app, services[0], "/aip/tracking/rpc")
    register_aip_agent_router(app, services[1], "/aip/quotation/rpc")
    records, reads = [], []

    async def read_orders(*, limit):
        # Actual AIP worker context has identity, but never delegates raw browser
        # credentials to subsequent business integrations.
        assert delegation.local_aip_session.get() is None
        reads.append((browser_actor.get(), limit))
        return {"orders": [{"work_order_no": "WO-FIXTURE"}], "authority": "isolated fixture"}

    monkeypatch.setattr("app.services.order_linkage.list_real_work_orders", read_orders)
    monkeypatch.setattr("app.services.order_linkage.list_real_sales_orders", read_orders)
    monkeypatch.setattr(coordinator_module, "build_coordinator", lambda: BusinessCoordinator(QueryLLM(), self_base_url=BASE))
    monkeypatch.setattr(delegation.httpx, "AsyncHTTPTransport", lambda **kwargs: ObservedTransport(app, records))
    return app, records, reads, services


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("credential_kind", ["cookie", "bearer"])
async def test_logged_in_query_crosses_real_coordinator_sdk_and_login_boundary(browser_app, stream, credential_kind):
    app, records, reads, services = browser_app
    token = "fixture-alice-token"
    options = {"cookies": {"project_session": token}} if credential_kind == "cookie" else {"headers": {"Authorization": f"Bearer {token}"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, **options) as client:
        response = await client.post("/api/real-orders/assistant/ask" + ("/stream" if stream else ""), json={"question": "查一下数据库里现在有的工单"})
    assert response.status_code == 200
    if stream:
        blocks = response.text.split("\n\n")
        done = next(block for block in blocks if block.startswith("event: done\n"))
        result = json.loads(done.split("data: ", 1)[1])
    else:
        result = response.json()
    assert [step["status"] for step in result["call_chain"]] == ["ok", "ok"]
    assert {step["skill_id"] for step in result["call_chain"]} == {"tracking.list_real_work_orders", "quotation.list_real_sales_orders"}
    assert reads == [("alice", 5), ("alice", 5)]
    assert {record["command"] for record in records} >= {"start", "get", "complete"}
    assert all(record["authorization"] == f"Bearer {token}" for record in records)
    assert all(token not in json.dumps(record["payload"]) for record in records)
    assert all(task.status.state.value == "completed" for service in services for task in service._tasks.values())
    with SessionLocal() as db:
        session = db.get(AssistantSessionRow, result["session_id"])
        run = db.get(RealAgentRunRow, result["coordination_run_id"])
        assert session.owner_subject == "alice" and run.session_id == session.session_id
        assert token not in json.dumps(run.input_json) + json.dumps(run.result_json)
    assert delegation.local_aip_session.get() is None
    assert browser_actor.get() is None


@pytest.mark.asyncio
async def test_anonymous_and_forged_loopback_headers_still_cannot_call_aip(browser_app):
    app, records, reads, _ = browser_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        response = await client.post("/aip/tracking/rpc", json={}, headers={"X-Forwarded-User": "alice", "X-Forwarded-For": "127.0.0.1"})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "project_login_required"
    assert not records and not reads


@pytest.mark.asyncio
async def test_omitting_delegation_reproduces_original_logged_in_tool_401(browser_app, monkeypatch):
    app, records, reads, _ = browser_app
    # Negative control: the former coordinator used an uncredentialed socket.
    # Keep the exact same middleware, handler, SDK and logged-in browser request.
    monkeypatch.setattr(delegation, "BrowserSessionAipTransport", lambda url: ObservedTransport(app, records))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, cookies={"project_session": "fixture-alice-token"}) as client:
        response = await client.post("/api/real-orders/assistant/ask", json={"question": "查一下数据库里现在有的工单"})
    assert response.status_code == 200
    chain = response.json()["call_chain"]
    assert len(chain) == 2 and all(step["status"] == "error" for step in chain)
    assert all("401" in step["result_summary"] and "project_login_required" in step["result_summary"] for step in chain)
    assert not reads
    assert all(record["authorization"] is None for record in records)
    assert delegation.local_aip_session.get() is None


@pytest.mark.asyncio
async def test_cached_coordinator_client_uses_each_concurrent_request_identity(browser_app):
    app, records, reads, _ = browser_app
    # Reuse one coordinator + SDK client across two simultaneous browser scopes.
    coordinator = BusinessCoordinator(None, self_base_url=BASE)
    coordinator._aip_client("tracking")

    async def ask():
        result = await coordinator._invoke_via_aip("tracking", "tracking.list_real_work_orders", {"limit": 7})
        return {"actor": browser_actor.get(), "result": result}

    # Use the standard assistant URL so the real middleware creates delegation.
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/api/real-orders/assistant/ask"]
    app.post("/api/real-orders/assistant/ask")(ask)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        try:
            responses = await asyncio.gather(*[
                client.post("/api/real-orders/assistant/ask", json={}, headers={"Authorization": f"Bearer {token}"}) for token in TOKENS
            ])
        finally:
            await coordinator.aclose()
    assert [response.json()["actor"] for response in responses] == ["alice", "bob"]
    assert sorted(reads) == [("alice", 7), ("bob", 7)]
    # Every command for a task must consistently use that task's original user.
    by_task = {}
    for record in records:
        task_id = record["payload"]["params"]["command"]["taskId"]
        by_task.setdefault(task_id, set()).add(record["authorization"])
    assert sorted(map(sorted, by_task.values())) == [["Bearer fixture-alice-token"], ["Bearer fixture-bob-token"]]
    assert delegation.local_aip_session.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://external.example/aip/tracking/rpc",
    "http://external.example:9000/aip/tracking/rpc",
    "http://localhost:9000/aip/tracking/rpc",
    "http://127.0.0.1:8080/aip/tracking/rpc",
    "http://127.0.0.2:9000/aip/tracking/rpc",
    "http://127.0.0.1:9000/api/mes/work-orders",
    "http://127.0.0.1:9000/api/resource/Sales%20Order",
    "http://127.0.0.1:9000/aip/unknown/rpc",
    "http://127.0.0.1:9000/aip/tracking/%72pc",
    "http://127.0.0.1:9000/aip/tracking/rpc?copy=1",
    "http://127.0.0.1:9000/aip/tracking/rpc#copy",
    "http://user:password@127.0.0.1:9000/aip/tracking/rpc",
])
async def test_custom_self_urls_and_upstream_paths_never_receive_browser_token(url):
    observed = []
    def receive(request):
        observed.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={})
    context = delegation.local_aip_session.set(delegation.LocalAipSession("private-fixture-token", "127.0.0.1", 9000))
    try:
        transport = delegation.BrowserSessionAipTransport(url, transport=httpx.MockTransport(receive))
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post(url, json={}, headers={"Authorization": "Bearer private-fixture-token"})
    finally:
        delegation.local_aip_session.reset(context)
    assert observed == [None]


@pytest.mark.asyncio
async def test_delegate_does_not_cache_session_or_follow_redirects():
    observed = []
    def receive(request):
        observed.append((str(request.url), request.headers.get("Authorization")))
        return httpx.Response(302, headers={"Location": "https://external.example/collect"})
    url = BASE + "/aip/tracking/rpc"
    transport = delegation.BrowserSessionAipTransport(url, transport=httpx.MockTransport(receive))
    async with httpx.AsyncClient(transport=transport) as client:
        context = delegation.local_aip_session.set(delegation.LocalAipSession("private-fixture-token", "127.0.0.1", 9000))
        try:
            response = await client.post(url)
        finally:
            delegation.local_aip_session.reset(context)
        assert response.status_code == 302
        await client.post(url)
    assert observed == [(url, "Bearer private-fixture-token"), (url, None)]
    assert "private-fixture-token" not in repr(delegation.LocalAipSession("private-fixture-token", "127.0.0.1", 9000))


def test_backend_binding_uses_listener_not_host_or_forwarded_headers():
    assert delegation.session_for_backend({"server": ("127.0.0.1", 9001)}, "token").port == 9001
    assert delegation.session_for_backend({"server": ("0.0.0.0", 9000)}, "token").host == "127.0.0.1"
    assert delegation.session_for_backend({"server": ("::1", 9000)}, "token").host == "::1"
    for server in (None, ("external.example", 9000), ("198.51.100.1", 9000), ("127.0.0.1", 0)):
        assert delegation.session_for_backend({"server": server, "headers": [(b"host", b"127.0.0.1:9000")]}, "token") is None


@pytest.mark.asyncio
async def test_request_failure_resets_delegated_session():
    app = FastAPI()
    app.add_middleware(BrowserSecurityMiddleware, token_resolver=resolve)

    @app.post("/api/real-orders/assistant/ask")
    async def fail():
        assert delegation.local_aip_session.get() is not None
        raise RuntimeError("fixture handler failure")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, headers={"Authorization": "Bearer fixture-alice-token"}) as client:
        with pytest.raises(RuntimeError, match="fixture handler failure"):
            await client.post("/api/real-orders/assistant/ask", json={})
    assert delegation.local_aip_session.get() is None
    assert browser_actor.get() is None
