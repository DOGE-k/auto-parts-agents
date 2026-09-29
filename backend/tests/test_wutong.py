from __future__ import annotations

import json
import unittest

import httpx

from app.integrations.errors import IntegrationError
from app.integrations.wutong import WutongDiscoveryClient


class WutongDiscoveryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_normalizes_acps_response_and_tenant_header(self):
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "result": {
                        "agents": [{"aic": "AIC-1"}],
                        "acsMap": {"AIC-1": {"name": "远程跟单"}},
                        "routes": [],
                    }
                },
            )

        transport = httpx.AsyncClient(
            base_url="https://discovery.example/",
            transport=httpx.MockTransport(handler),
        )
        client = WutongDiscoveryClient(
            "https://discovery.example/api",
            tenant="tenant-a",
            client=transport,
        )
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

        transport = httpx.AsyncClient(
            base_url="https://discovery.example/",
            transport=httpx.MockTransport(handler),
        )
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

        transport = httpx.AsyncClient(
            base_url="https://discovery.example/",
            transport=httpx.MockTransport(handler),
        )
        client = WutongDiscoveryClient("https://discovery.example", client=transport)
        try:
            with self.assertRaisesRegex(IntegrationError, "acsMap"):
                await client.discover("质量")
        finally:
            await client.aclose()
            await transport.aclose()


if __name__ == "__main__":
    unittest.main()
