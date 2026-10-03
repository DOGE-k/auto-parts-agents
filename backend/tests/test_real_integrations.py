from __future__ import annotations

import json
import unittest

import httpx

from app.adapters.erp.erpnext import ERPNextClient
from app.adapters.mes.openmes import OpenMESClient
from app.adapters.mes.openmes_adapter import OpenMESAdapter
from app.integrations.deepseek import DeepSeekClient
from app.integrations.errors import IntegrationError, IntegrationNotConfigured, IntegrationPermissionDenied
from app.integrations.http import JsonHttpClient
from app.integrations.settings import IntegrationSettings


class RealIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_erpnext_uses_frappe_token_and_explicit_fields(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": [{"name": "ITEM-1"}]})

        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://erp.example/", transport=transport)
        client = ERPNextClient("https://erp.example", "key", "secret", client=transport_client)
        try:
            rows = await client.list_documents("Item", fields=["name"], limit=10)
        finally:
            await client.aclose()
            await transport_client.aclose()

        self.assertEqual(rows, [{"name": "ITEM-1"}])
        self.assertEqual(seen[0].url.path, "/api/resource/Item")
        self.assertEqual(seen[0].headers["Authorization"], "token key:secret")
        self.assertEqual(json.loads(seen[0].url.params["fields"]), ["name"])

    async def test_erpnext_drafts_require_human_approval_and_stay_unsubmitted(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "POST":
                body = json.loads(request.content)
                return httpx.Response(200, json={"data": {"name": "PO-1", "docstatus": body["docstatus"]}})
            return httpx.Response(200, json={"data": {"name": "PO-1", "docstatus": 0}})

        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://erp.example/", transport=transport)
        disabled = ERPNextClient("https://erp.example", "key", "secret", client=transport_client)
        with self.assertRaises(IntegrationPermissionDenied):
            await disabled.create_draft(
                "Purchase Order", {"supplier": "SUP-1"}, approval_id="A-1", approved_by="operator"
            )
        await disabled.aclose()

        enabled = ERPNextClient(
            "https://erp.example", "key", "secret", draft_writes_enabled=True, client=transport_client
        )
        with self.assertRaises(IntegrationPermissionDenied):
            await enabled.create_draft(
                "Purchase Order", {"supplier": "SUP-1", "docstatus": 1}, approval_id="A-1", approved_by="operator"
            )
        with self.assertRaises(IntegrationPermissionDenied):
            await enabled.create_draft(
                "Purchase Order", {"supplier": "SUP-1"}, approval_id="A-1", approved_by="operator"
            )
        await enabled.aclose()

        async def approval_verifier(approval_id: str, approved_by: str) -> bool:
            return approval_id == "A-1" and approved_by == "operator"

        enabled = ERPNextClient(
            "https://erp.example",
            "key",
            "secret",
            draft_writes_enabled=True,
            approval_verifier=approval_verifier,
            client=transport_client,
        )
        result = await enabled.create_draft(
            "Purchase Order", {"supplier": "SUP-1"}, approval_id="A-1", approved_by="operator"
        )
        await enabled.aclose()
        await transport_client.aclose()

        self.assertEqual(len(seen), 2)
        self.assertEqual(json.loads(seen[0].content)["docstatus"], 0)
        self.assertEqual(result["data"]["docstatus"], 0)

    async def test_openmes_user_and_erp_api_credentials_are_separate(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/work-orders"):
                return httpx.Response(200, json={"data": [], "meta": {"current_page": 1}})
            return httpx.Response(200, json={"data": []})

        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
        client = OpenMESClient(
            "https://mes.example", user_token="user-token", erp_api_key="erp-key", client=transport_client
        )
        try:
            await client.list_work_orders({"status": "in_progress", "per_page": 10})
            await client.list_erp_quality_issues(since="2026-09-01T00:00:00Z")
        finally:
            await client.aclose()
            await transport_client.aclose()

        self.assertEqual(seen[0].headers["Authorization"], "Bearer user-token")
        self.assertNotIn("X-Api-Key", seen[0].headers)
        self.assertEqual(seen[1].headers["X-Api-Key"], "erp-key")
        self.assertNotIn("Authorization", seen[1].headers)

    async def test_openmes_rejects_undocumented_filter_and_write(self) -> None:
        client = OpenMESClient("https://mes.example", user_token="user-token")
        try:
            with self.assertRaises(ValueError):
                await client.list_work_orders({"made_up_field": "value"})
            with self.assertRaises(IntegrationPermissionDenied):
                await client.write_request({"action": "release_quality"})
        finally:
            await client.aclose()

    async def test_openmes_quality_resolution_uses_user_token_and_documented_payload(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"id": 7, "status": "RESOLVED"}})

        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
        client = OpenMESClient("https://mes.example", user_token="user-token", client=transport_client)
        try:
            result = await client.resolve_issue(7, "TEST_已完成纠正措施")
        finally:
            await client.aclose()
            await transport_client.aclose()

        self.assertEqual(result["data"]["status"], "RESOLVED")
        self.assertEqual(seen[0].method, "POST")
        self.assertEqual(seen[0].url.path, "/api/v1/issues/7/resolve")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer user-token")
        self.assertEqual(json.loads(seen[0].content), {"resolution_notes": "TEST_已完成纠正措施"})

    async def test_openmes_engineering_document_upload_uses_multipart_and_user_token(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"data": {"id": 99, "original_filename": "TEST_SOP.html"}})

        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://mes.example/", transport=transport)
        client = OpenMESClient("https://mes.example", user_token="user-token", client=transport_client)
        try:
            result = await client.upload_engineering_document(
                filename="TEST_SOP.html",
                content=b"<html><body>TEST SOP</body></html>",
                entity_type="product_type",
                entity_id=2,
                revision="TEST-R1",
                document_type="SOP",
            )
        finally:
            await client.aclose()
            await transport_client.aclose()

        self.assertEqual(result["data"]["id"], 99)
        self.assertEqual(seen[0].method, "POST")
        self.assertEqual(seen[0].url.path, "/api/v1/engineering-documents")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer user-token")
        body = seen[0].content
        self.assertIn(b"TEST_SOP.html", body)
        self.assertIn(b"name=\"document_type\"", body)
        self.assertIn(b"SOP", body)

    async def test_openmes_frozen_documents_use_verified_detail_type(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/work-orders/6/engineering-documents"):
                return httpx.Response(200, json={"data": [{
                    "document_id": 1, "original_filename": "TEST_SOP.html",
                    "revision": "TEST-R1", "package_type": "interactive_html",
                    "lifecycle_at_release": "released",
                }]})
            return httpx.Response(200, json={"data": {"id": 1, "document_type": "SOP"}})

        transport_client = httpx.AsyncClient(
            base_url="https://mes.example/", transport=httpx.MockTransport(handler)
        )
        client = OpenMESClient("https://mes.example", user_token="user-token", client=transport_client)
        try:
            documents = await OpenMESAdapter(client).get_work_order_documents("6")
        finally:
            await client.aclose()
            await transport_client.aclose()

        self.assertEqual(documents[0]["doc_id"], "1")
        self.assertEqual(documents[0]["doc_type"], "SOP")
        self.assertEqual(documents[0]["lifecycle_status"], "released")
        self.assertEqual(seen[1].url.path, "/api/v1/engineering-documents/1")

    async def test_missing_openmes_user_token_fails_without_fallback(self) -> None:
        client = OpenMESClient("https://mes.example")
        try:
            with self.assertRaises(IntegrationNotConfigured):
                await client.list_work_orders()
        finally:
            await client.aclose()

    async def test_deepseek_uses_flash_tool_call_contract(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"model": "deepseek-flash", "choices": [{"message": {"role": "assistant", "content": "OK"}}]},
            )

        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://api.deepseek.com/", transport=transport)
        client = DeepSeekClient("secret", client=transport_client)
        try:
            result = await client.chat_completion(
                [{"role": "user", "content": "Analyze"}],
                tools=[{"type": "function", "function": {"name": "read_order"}}],
            )
        finally:
            await client.aclose()
            await transport_client.aclose()

        request_body = json.loads(seen[0].content)
        self.assertEqual(seen[0].url.path, "/chat/completions")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer secret")
        self.assertEqual(request_body["model"], "deepseek-flash")
        self.assertEqual(request_body["tools"][0]["function"]["name"], "read_order")
        self.assertEqual(result["model"], "deepseek-flash")

    def test_public_status_does_not_expose_secrets(self) -> None:
        settings = IntegrationSettings(
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-flash",
            deepseek_api_key="do-not-return",
            erpnext_base_url="https://erp.example",
            erpnext_api_key="key-secret",
            erpnext_api_secret="private-secret",
            erpnext_draft_writes_enabled=False,
            openmes_base_url="https://mes.example",
            openmes_user_token="token-secret",
            openmes_erp_api_key="integration-secret",
        )
        status = settings.public_status()
        serialized = json.dumps(status)
        for secret in ("do-not-return", "private-secret", "token-secret", "integration-secret"):
            self.assertNotIn(secret, serialized)
        self.assertTrue(status["openmes"]["configured"])


if __name__ == "__main__":
    unittest.main()


class JsonHttpFailureMappingTests(unittest.IsolatedAsyncioTestCase):
    """失败验证：超时/网络中断/远端错误必须映射为明确的 IntegrationError，
    不得伪装成"查询结果为空"（next_development_plan 阶段四）。"""

    def _client(self, handler) -> tuple[JsonHttpClient, httpx.AsyncClient]:
        transport = httpx.MockTransport(handler)
        transport_client = httpx.AsyncClient(base_url="https://erp.example/", transport=transport)
        http = JsonHttpClient("https://erp.example", client=transport_client)
        return http, transport_client

    async def test_timeout_maps_to_retryable_integration_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        http, transport_client = self._client(handler)
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await http.request_json("GET", "api/resource/Item")
        finally:
            await http.aclose()
            await transport_client.aclose()
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertTrue(ctx.exception.retryable)

    async def test_network_error_maps_to_retryable_integration_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        http, transport_client = self._client(handler)
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await http.request_json("GET", "api/resource/Item")
        finally:
            await http.aclose()
            await transport_client.aclose()
        self.assertEqual(ctx.exception.code, "network_error")
        self.assertTrue(ctx.exception.retryable)

    async def test_remote_500_maps_to_retryable_remote_http_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "boom"})

        http, transport_client = self._client(handler)
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await http.request_json("GET", "api/resource/Item")
        finally:
            await http.aclose()
            await transport_client.aclose()
        self.assertEqual(ctx.exception.code, "remote_http_error")
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("boom", str(ctx.exception))

    async def test_remote_404_is_not_retryable_and_keeps_detail(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "not found"})

        http, transport_client = self._client(handler)
        try:
            with self.assertRaises(IntegrationError) as ctx:
                await http.request_json("GET", "api/resource/Item")
        finally:
            await http.aclose()
            await transport_client.aclose()
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertFalse(ctx.exception.retryable)
