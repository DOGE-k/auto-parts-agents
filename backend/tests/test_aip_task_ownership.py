from __future__ import annotations

import asyncio
import unittest
import uuid

import httpx
from acps_sdk.aip.aip_base_model import TaskState
from acps_sdk.aip.aip_rpc_model import RpcRequest
from acps_sdk.aip.aip_rpc_server import TaskManager
from fastapi import FastAPI, HTTPException

from app.aip.aip_agent_service import AipAgentService, register_aip_agent_router

LEADER = "1.2.156.3088.1.BUPT.R2QTUC.O321QO.LHD18D.18NY"
PARTNER = "1.2.156.3088.1.BUPT.R2QTUC.L0628O.R6C3J5.11WB"
OTHER = "1.2.156.3088.1.BUPT.R2QTUC.VK300S.9Q16X9.0I5X"


def rpc(task_id: str | None, command: str = "start", *, sender: str = LEADER,
        session: str | None = "session-a", group: str | None = "group-a",
        user: str | None = None, data=None, role="leader") -> RpcRequest:
    payload = {"id": str(uuid.uuid4()), "sentAt": "2026-10-16T00:00:00Z",
               "senderRole": role, "senderId": sender, "command": command,
               "taskId": task_id, "sessionId": session, "groupId": group,
               "commandParams": {"userId": user} if user is not None else None,
               "dataItems": data if data is not None else [
                   {"type": "data", "data": {"skill_id": "echo", "inputs": {"value": 7}}}]}
    return RpcRequest.model_validate({"id": str(uuid.uuid4()), "params": {"command": payload}})


class AipTaskOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = AipAgentService("Test", "quotation", PARTNER)
        self.calls = []

        async def echo(inputs):
            self.calls.append(inputs)
            return {"echo": inputs}

        self.service.register_skill("echo", "Echo", echo)

    async def drain(self, service=None):
        service = service or self.service
        executions = list(service._executions.values())
        if executions:
            await asyncio.gather(*executions, return_exceptions=True)

    async def test_structured_and_legacy_inputs_have_sdk_result_fields_and_snapshots(self):
        for task_id, data in (
            ("structured", [{"type": "data", "data": {
                "skill_id": "echo", "inputs": {"value": 3}}}]),
            ("legacy", [{"type": "text", "text": 'echo|{"value": 3}'}]),
        ):
            initial = await self.service.dispatch_rpc(rpc(task_id, data=data))
            self.assertEqual(initial.result.status.state, TaskState.Working)
            await self.drain()
            fetched = await self.service.dispatch_rpc(rpc(task_id, "get"))
            self.assertEqual(fetched.result.senderId, PARTNER)
            self.assertEqual(fetched.result.groupId, "group-a")
            self.assertEqual(fetched.result.status.state, TaskState.AwaitingCompletion)
            item = fetched.result.products[0].dataItems[0]
            self.assertEqual(item.model_dump(exclude_none=True), {
                "type": "data", "data": {"echo": {"value": 3}},
                "metadata": {"skill_id": "echo"}})
            self.assertEqual(initial.result.status.state, TaskState.Working)
            self.assertIsNone(TaskManager.get_task(task_id))
            complete = await self.service.dispatch_rpc(rpc(task_id, "complete"))
            self.assertEqual(complete.result.status.state, TaskState.Completed)

    async def test_invalid_payload_never_executes_or_reserves_task_id(self):
        invalid = [
            [{"type": "text", "text": "echo|not json"}],
            [{"type": "text", "text": "echo|[]"}],
            [{"type": "data", "data": {"skill_id": "echo", "inputs": None}}],
            [{"type": "data", "data": {"skill_id": "echo", "inputs": []}}],
            [{"type": "data", "data": {"skill_id": 7, "inputs": {}}}],
            [{"type": "data", "data": {"inputs": {}}}],
            [{"type": "text", "text": "echo"}, {"type": "text", "text": "ignored?"}],
            [],
        ]
        for data in invalid:
            result = await self.service.dispatch_rpc(rpc("bad", data=data))
            self.assertEqual(result.error.code, -32602)
            self.assertNotIn("bad", self.service._tasks)
        self.assertEqual(self.calls, [])
        good = await self.service.dispatch_rpc(rpc("bad"))
        self.assertIsNone(good.error)
        await self.drain()
        self.assertEqual(len(self.calls), 1)

    async def test_all_commands_require_same_caller_and_context(self):
        await self.service.dispatch_rpc(rpc("owned", user="alice"))
        await self.drain()
        variants = [dict(sender=OTHER), dict(session="session-b"),
                    dict(group="group-b"), dict(user="bob"), dict(role="partner")]
        for command in ("start", "get", "cancel", "complete", "continue"):
            for variant in variants:
                params = {"user": "alice", **variant}
                result = await self.service.dispatch_rpc(rpc("owned", command, **params))
                self.assertEqual(result.error.code, -32003, (command, variant))
                self.assertIsNone(result.result)
        self.assertEqual(len(self.calls), 1)
        own = await self.service.dispatch_rpc(rpc("owned", "get", user="alice"))
        self.assertEqual(own.result.status.state, TaskState.AwaitingCompletion)
        self.assertEqual(len(own.result.commandHistory), 1)
        missing_user = await self.service.dispatch_rpc(rpc("owned", "get"))
        self.assertEqual(missing_user.error.code, -32003)

    async def test_same_task_id_is_isolated_between_services_and_retry_is_idempotent(self):
        other = AipAgentService("Other", "tracking", OTHER)
        other.register_skill("echo", "Echo", self.service._skill_handlers["echo"])
        await self.service.dispatch_rpc(rpc("same"))
        await self.drain()
        for command in ("get", "cancel", "complete"):
            missing = await other.dispatch_rpc(rpc("same", command))
            self.assertEqual(missing.error.code, -32001)
        retried = await self.service.dispatch_rpc(rpc("same"))
        self.assertEqual(retried.result.status.state, TaskState.AwaitingCompletion)
        conflict = await self.service.dispatch_rpc(rpc("same", data=[{
            "type": "data", "data": {"skill_id": "echo", "inputs": {"new": True}}}]))
        self.assertEqual(conflict.error.code, -32602)
        self.assertEqual(len(self.calls), 1)
        second = await other.dispatch_rpc(rpc("same", sender=OTHER))
        self.assertEqual(second.result.senderId, OTHER)
        await self.drain(other)
        await other.dispatch_rpc(rpc("same", "cancel", sender=OTHER))
        original = await self.service.dispatch_rpc(rpc("same", "get"))
        self.assertEqual(original.result.status.state, TaskState.AwaitingCompletion)

    async def test_cancel_cannot_be_revived_by_handler_swallowing_cancellation(self):
        started = asyncio.Event()
        released = asyncio.Event()

        async def ignores_cancel(_inputs):
            started.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                # Real third-party integrations sometimes swallow cancellation.
                return {"late": "result"}
            return {"late": "result"}

        self.service.register_skill("slow", "Slow", ignores_cancel)
        await self.service.dispatch_rpc(rpc("cancelled", data=[{
            "type": "data", "data": {"skill_id": "slow", "inputs": {}}}]))
        await started.wait()
        denied = await self.service.dispatch_rpc(rpc("cancelled", "cancel", sender=OTHER))
        self.assertEqual(denied.error.code, -32003)
        self.assertFalse(list(self.service._executions.values())[0].cancelling())
        cancelled = await self.service.dispatch_rpc(rpc("cancelled", "cancel"))
        self.assertEqual(cancelled.result.status.state, TaskState.Canceled)
        await self.drain()
        final = await self.service.dispatch_rpc(rpc("cancelled", "get"))
        self.assertEqual(final.result.status.state, TaskState.Canceled)
        self.assertIsNone(final.result.products)

    async def test_natural_language_handler_gets_original_context_not_input_claim(self):
        observed = []

        async def ask(text, command):
            observed.append((text, command.senderId, command.sessionId, command.groupId))
            return {"answer": text}

        self.service.set_natural_language_handler(ask, skill_id="leader.ask", skill_name="问答")
        self.assertIn("leader.ask", self.service.registered_skill_ids())
        for tid, data in (("nl", [{"type": "text", "text": "查询工单"}]),
                          ("structured-nl", [{"type": "data", "data": {
                              "skill_id": "leader.ask", "inputs": {
                                  "question": "查询报价", "senderId": OTHER,
                                  "sessionId": "forged"}}}])):
            response = await self.service.dispatch_rpc(rpc(tid, data=data))
            self.assertIsNone(response.error)
            await self.drain()
        self.assertEqual(observed, [
            ("查询工单", LEADER, "session-a", "group-a"),
            ("查询报价", LEADER, "session-a", "group-a")])

    async def test_start_without_task_id_assigns_service_id(self):
        response = await self.service.dispatch_rpc(rpc(None))
        self.assertTrue(response.result.taskId)
        self.assertEqual(response.result.senderId, PARTNER)
        await self.drain()

    async def test_router_keeps_sdk_identity_binding_before_guard_for_every_command(self):
        app = FastAPI()
        authorized = []

        @app.middleware("http")
        async def trusted_transport(request, call_next):
            # A transport fixture, not a header-derived production identity.
            request.state.peer_aic = LEADER
            return await call_next(request)

        def authorize(_request, command):
            authorized.append(command.command.value)
            if command.groupId == "blocked":
                raise HTTPException(403, "denied")

        register_aip_agent_router(app, self.service, "/aip/rpc",
                                 identity_binding_enabled=True, command_authorizer=authorize)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="https://test") as client:
            spoof = await client.post("/aip/rpc", json=rpc("route", sender=OTHER).model_dump())
            self.assertEqual(spoof.json()["error"]["code"], -32009)
            self.assertEqual(authorized, [])
            for command in ("start", "get", "complete", "cancel", "continue"):
                response = await client.post("/aip/rpc", json=rpc("route", command).model_dump())
                self.assertEqual(response.status_code, 200)
            self.assertEqual(authorized, ["start", "get", "complete", "cancel", "continue"])
            blocked = await client.post("/aip/rpc", json=rpc("route", "get", group="blocked").model_dump())
            self.assertEqual(blocked.status_code, 403)
        await self.drain()

    async def test_payload_and_header_identity_alone_are_not_authentication(self):
        app = FastAPI()
        register_aip_agent_router(app, self.service, "/aip/rpc", identity_binding_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="https://test") as client:
            response = await client.post("/aip/rpc", json=rpc("fake-peer").model_dump(),
                headers={"X-Forwarded-AIC": LEADER, "X-Peer-AIC": LEADER})
        self.assertEqual(response.json()["error"]["code"], -32008)
        self.assertEqual(self.service._tasks, {})

    async def test_legacy_dev_route_still_works_without_peer_certificate(self):
        app = FastAPI()
        register_aip_agent_router(app, self.service, "/aip/rpc")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            response = await client.post("/aip/rpc", json=rpc("dev", data=[{
                "type": "text", "text": "echo|{}"}]).model_dump())
        self.assertIsNone(response.json()["error"])
        await self.drain()
