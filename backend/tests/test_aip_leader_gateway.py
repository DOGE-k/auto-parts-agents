"""No network, ERP/MES, LLM or credential files are used in these gateway tests."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from acps_sdk.aip import TaskState
from acps_sdk.aip.aip_base_model import Product, StructuredDataItem, TaskCommand, TaskCommandType

from app.aip.aip_agent_client import AipAgentClient
from app.aip.aip_agent_service import AipAgentService
from app.aip.leader_service import PARTNER_ROLES, create_leader_aip_service
from app.aip.natural_language import attach_natural_language, request_namespace
from app.services.coordinator import BusinessCoordinator


def command(caller="caller-a", session="same-session", *, user=None):
    return TaskCommand(
        id="command-1", sentAt="2026-10-16T09:00:00Z", senderRole="leader",
        senderId=caller, sessionId=session, taskId="task-1", command=TaskCommandType.Start,
        commandParams={"userId": user} if user else None,
    )


class NamespaceTests(unittest.TestCase):
    def test_same_session_name_does_not_merge_callers_users_or_services(self):
        a = request_namespace("leader", command())
        self.assertEqual(a, request_namespace("leader", command()))
        self.assertNotEqual(a, request_namespace("leader", command("caller-b")))
        self.assertNotEqual(a, request_namespace("partner", command()))
        self.assertNotEqual(a, request_namespace("leader", command(user="another-user")))
        self.assertNotIn("same-session", a)


class FakeCoordinator:
    def __init__(self, *, skill_invoker, allowed_skill_ids):
        self.invoke = skill_invoker
        self.allowed = allowed_skill_ids
        self.closed = False
        self.ask_calls = []

    async def ask(self, question, **kwargs):
        self.ask_calls.append((question, kwargs))
        return {"answer": "仅测试接线", "question": question}

    async def aclose(self):
        self.closed = True


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def capture_handler(self, service, handler, **kwargs):
        self.handlers[service.aic] = handler

    async def asyncSetUp(self):
        self.handlers = {}
        self.coordinators = []

    def coordinator_factory(self, **kwargs):
        coordinator = FakeCoordinator(**kwargs)
        self.coordinators.append(coordinator)
        return coordinator

    async def test_leader_uses_new_coordinator_and_scoped_outbound_session(self):
        clients = {role: SimpleNamespace(invoke_skill=AsyncMock(return_value={"found": False})) for role in PARTNER_ROLES}
        with patch.object(AipAgentService, "set_natural_language_handler", lambda service, handler, **kw: self.capture_handler(service, handler, **kw)):
            create_leader_aip_service("leader-aic", outbound_clients=clients, coordinator_factory=self.coordinator_factory)
        await self.handlers["leader-aic"]("查报价", command())
        await self.handlers["leader-aic"]("查报价", command("caller-b"))
        first, second = self.coordinators
        self.assertIsNot(first, second)
        self.assertTrue(first.closed and second.closed)
        first_session = first.ask_calls[0][1]["run_meta"]["session_id"]
        second_session = second.ask_calls[0][1]["run_meta"]["session_id"]
        self.assertNotEqual(first_session, second_session)
        await first.invoke("quotation", "quotation.get_real", {"quotation_id": "Q-1"})
        self.assertEqual(clients["quotation"].invoke_skill.await_args.kwargs["session_id"], first_session)
        with self.assertRaises(PermissionError):
            await first.invoke("quotation", "quotation.create_draft", {})

    async def test_partner_restricts_question_tools_and_keeps_read_only_handlers(self):
        service = AipAgentService("采购", "procurement", "procurement-aic")
        real_handler = AsyncMock(return_value={"found": False})
        service.register_skill("procurement.get_real_plan", "查询采购方案", real_handler)
        service.register_skill("procurement.create_po_draft", "写入草稿", AsyncMock())
        with patch.object(AipAgentService, "set_natural_language_handler", lambda service, handler, **kw: self.capture_handler(service, handler, **kw)):
            result = attach_natural_language(service, coordinator_factory=self.coordinator_factory)
        self.assertIs(result, service)
        self.assertEqual(service.registered_skill_ids(), ["procurement.get_real_plan"])
        await self.handlers["procurement-aic"]("查采购方案", command())
        coordinator = self.coordinators[-1]
        self.assertEqual(coordinator.allowed, {"procurement.get_real_plan"})
        await coordinator.invoke("procurement", "procurement.get_real_plan", {"plan_id": "P-1"})
        real_handler.assert_awaited_once_with({"plan_id": "P-1"})
        with self.assertRaises(PermissionError):
            await coordinator.invoke("quotation", "quotation.get_real", {})
        with self.assertRaises(PermissionError):
            await coordinator.invoke("procurement", "procurement.create_po_draft", {})

    async def test_request_coordinator_is_closed_when_question_fails(self):
        clients = {role: SimpleNamespace() for role in PARTNER_ROLES}
        def failing_factory(**kwargs):
            coord = self.coordinator_factory(**kwargs)
            coord.ask = AsyncMock(side_effect=RuntimeError("LLM 未配置"))
            return coord
        with patch.object(AipAgentService, "set_natural_language_handler", lambda service, handler, **kw: self.capture_handler(service, handler, **kw)):
            create_leader_aip_service("leader-aic", outbound_clients=clients, coordinator_factory=failing_factory)
        with self.assertRaisesRegex(RuntimeError, "LLM 未配置"):
            await self.handlers["leader-aic"]("查订单", command())
        self.assertTrue(self.coordinators[-1].closed)

    async def test_missing_llm_is_explicit_not_a_fake_success(self):
        from app.integrations.errors import IntegrationNotConfigured
        clients = {role: SimpleNamespace() for role in PARTNER_ROLES}
        with patch.object(AipAgentService, "set_natural_language_handler", lambda service, handler, **kw: self.capture_handler(service, handler, **kw)):
            create_leader_aip_service("leader-aic", outbound_clients=clients)
        with patch("app.aip.natural_language.build_coordinator", side_effect=IntegrationNotConfigured("LLM", ["DEEPSEEK_API_KEY"])):
            with self.assertRaises(IntegrationNotConfigured):
                await self.handlers["leader-aic"]("查订单", command())

    async def test_coordinator_filter_is_local_and_cannot_invoke_other_role(self):
        tool_call = {"id": "c1", "function": {"name": "quotation__get_real", "arguments": '{"quotation_id":"Q1"}'}}
        llm = SimpleNamespace(chat_completion=AsyncMock(side_effect=[
            {"choices": [{"message": {"role": "assistant", "tool_calls": [tool_call]}}]},
            {"choices": [{"message": {"role": "assistant", "content": "当前岗位不能查询报价"}}]},
        ]))
        invoker = AsyncMock()
        coord = BusinessCoordinator(llm, skill_invoker=invoker, allowed_skill_ids={"procurement.get_real_plan"})
        with patch("app.services.coordinator._save_agent_run"):
            result = await coord.ask("查报价")
        invoker.assert_not_awaited()
        self.assertEqual(result["call_chain"][0]["status"], "error")
        supplied = llm.chat_completion.await_args_list[0].kwargs["tools"]
        self.assertEqual([t["function"]["name"] for t in supplied], ["procurement__get_real_plan"])


def result(task_id, state, *, products=None, data_items=None):
    return SimpleNamespace(taskId=task_id, status=SimpleNamespace(state=state, dataItems=data_items), products=products)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_sdk_data_product_and_acknowledge_completion(self):
        sdk = SimpleNamespace(complete_task=AsyncMock(), cancel_task=AsyncMock(), get_task=AsyncMock())
        async def start(**kwargs):
            task_id = kwargs["task_id"]
            sdk.complete_task.return_value = result(task_id, TaskState.Completed)
            return result(task_id, TaskState.AwaitingCompletion, products=[Product(
                id="p1", dataItems=[StructuredDataItem(data={"found": False})],
            )])
        sdk.start_task = AsyncMock(side_effect=start)
        client = AipAgentClient("https://partner/rpc", leader_id="leader-aic")
        with patch.object(client, "_get_client", AsyncMock(return_value=sdk)):
            self.assertEqual(await client.invoke_skill("quotation.get_real", {}, session_id="s"), {"found": False})
        sdk.complete_task.assert_awaited_once()
        sdk.get_task.assert_not_awaited()
        sdk.cancel_task.assert_not_awaited()

    async def test_timeout_in_start_is_bounded_and_attempts_remote_cancel(self):
        async def delayed_start(**kwargs):
            await asyncio.sleep(10)
        sdk = SimpleNamespace(start_task=AsyncMock(side_effect=delayed_start), cancel_task=AsyncMock())
        client = AipAgentClient("https://partner/rpc")
        with patch.object(client, "_get_client", AsyncMock(return_value=sdk)):
            with self.assertRaises(TimeoutError):
                await client.invoke_skill("quotation.get_real", {}, timeout=0.01)
        sdk.cancel_task.assert_awaited_once()
        self.assertEqual(sdk.cancel_task.await_args.args[0], sdk.start_task.await_args.kwargs["task_id"])

    async def test_caller_cancellation_propagates_and_cancels_remote(self):
        started = asyncio.Event()
        async def delayed_start(**kwargs):
            started.set()
            await asyncio.sleep(10)
        sdk = SimpleNamespace(start_task=AsyncMock(side_effect=delayed_start), cancel_task=AsyncMock())
        client = AipAgentClient("https://partner/rpc")
        with patch.object(client, "_get_client", AsyncMock(return_value=sdk)):
            task = asyncio.create_task(client.invoke_skill("quotation.get_real", {}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        sdk.cancel_task.assert_awaited_once()

    def test_empty_products_are_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "没有可读取"):
            AipAgentClient("https://partner/rpc")._extract_result(result("task", TaskState.Completed))
