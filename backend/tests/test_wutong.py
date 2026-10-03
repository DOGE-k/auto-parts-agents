from __future__ import annotations

import json
import unittest

import httpx

from app.integrations.errors import IntegrationError
from app.integrations.wutong import WutongDiscoveryClient, WutongRegistryClient


class WutongDiscoveryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_normalizes_acps_response_and_tenant_header(self):
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"result": {"agents": [{"aic": "AIC-1"}], "acsMap": {"AIC-1": {"name": "远程跟单"}}, "routes": []}})

        transport = httpx.AsyncClient(base_url="https://discovery.example/", transport=httpx.MockTransport(handler))
        client = WutongDiscoveryClient("https://discovery.example/api", tenant="tenant-a", client=transport)
        try:
            result = await client.discover("真实跟单能力", limit=3)
        finally:
            await client.aclose()
            await transport.aclose()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["authority"], "Wutong")
        self.assertEqual(result["acs_map"]["AIC-1"]["name"], "远程跟单")
        self.assertEqual(seen[0].url.path, "/api/discover")
        self.assertEqual(seen[0].headers["X-Wutong-Tenant"], "tenant-a")
        self.assertEqual(json.loads(seen[0].content)["limit"], 3)

    async def test_remote_error_is_explicit(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": {"code": 40001, "message": "无可用服务"}})

        transport = httpx.AsyncClient(base_url="https://discovery.example/", transport=httpx.MockTransport(handler))
        client = WutongDiscoveryClient("https://discovery.example", client=transport)
        try:
            with self.assertRaisesRegex(IntegrationError, "无可用服务"):
                await client.discover("报价")
        finally:
            await client.aclose()
            await transport.aclose()

    async def test_invalid_result_does_not_fallback_to_local_directory(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": {"agents": []}})

        transport = httpx.AsyncClient(base_url="https://discovery.example/", transport=httpx.MockTransport(handler))
        client = WutongDiscoveryClient("https://discovery.example", client=transport)
        try:
            with self.assertRaisesRegex(IntegrationError, "acsMap"):
                await client.discover("质量")
        finally:
            await client.aclose()
            await transport.aclose()


class WutongRegistryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_health_and_recent_agents_are_read_only_contract(self):
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(200, json={"items": [{"aic": "AIC-2"}], "total": 1, "page_num": 1, "page_size": 1})

        transport = httpx.AsyncClient(base_url="https://registry.example/", transport=httpx.MockTransport(handler))
        client = WutongRegistryClient("https://registry.example/api/v1", tenant="tenant-b", client=transport)
        try:
            health = await client.health()
            agents = await client.list_recent_agents(limit=1)
        finally:
            await client.aclose()
            await transport.aclose()

        self.assertEqual(health["authority"], "Wutong Registry")
        self.assertEqual(agents["items"][0]["aic"], "AIC-2")
        self.assertEqual(seen[0].url.path, "/health")
        self.assertEqual(seen[1].url.path, "/api/v1/agent/public/recent")
        self.assertEqual(seen[1].headers["X-Wutong-Tenant"], "tenant-b")
        self.assertNotIn("Authorization", seen[1].headers)

    async def test_registry_invalid_listing_does_not_fallback(self):
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": []})

        transport = httpx.AsyncClient(base_url="https://registry.example/", transport=httpx.MockTransport(handler))
        client = WutongRegistryClient("https://registry.example", client=transport)
        try:
            with self.assertRaisesRegex(IntegrationError, "items"):
                await client.list_recent_agents()
        finally:
            await client.aclose()
            await transport.aclose()


if __name__ == "__main__":
    unittest.main()
