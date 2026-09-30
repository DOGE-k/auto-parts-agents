from __future__ import annotations

import unittest
from unittest.mock import patch

from app.integrations.errors import IntegrationError
from app.integrations.settings import IntegrationSettings
from app.services.identity import RealIdentity, ensure_role, resolve_real_identity


def settings(**overrides) -> IntegrationSettings:
    values = dict(
        deepseek_base_url="https://deepseek.example",
        deepseek_model="deepseek-flash",
        deepseek_api_key="",
        erpnext_base_url="https://erp.example",
        erpnext_api_key="key",
        erpnext_api_secret="secret",
        erpnext_draft_writes_enabled=False,
        openmes_base_url="https://mes.example",
        openmes_user_token="mes-token",
        openmes_erp_api_key="",
    )
    values.update(overrides)
    return IntegrationSettings(**values)


class FakeERPClient:
    def __init__(self, *args, **kwargs):
        self.closed = False

    async def get_logged_user(self):
        return {"user": "alice@example.com"}

    async def get_document(self, doctype, name):
        assert doctype == "User"
        return {"full_name": "Alice", "roles": [{"role": "Sales User"}]}

    async def aclose(self):
        self.closed = True


class FakeMESClient:
    last_token = ""

    def __init__(self, *args, **kwargs):
        FakeMESClient.last_token = kwargs.get("user_token", "")

    async def current_user(self):
        return {"username": "mes-user", "roles": ["quality_manager"]}

    async def aclose(self):
        pass


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_erpnext_identity_uses_logged_user_and_configured_role_map(self):
        configured = settings(
            real_identity_provider="erpnext",
            real_identity_role_map='{"alice@example.com": ["sales_manager"]}',
        )
        with patch("app.services.identity.ERPNextClient", FakeERPClient):
            identity = await resolve_real_identity(configured)

        self.assertEqual(identity.actor_id, "alice@example.com")
        self.assertEqual(identity.display_name, "Alice")
        self.assertIn("Sales User", identity.roles)
        self.assertIn("sales_manager", identity.roles)
        self.assertEqual(identity.authority, "ERPNext")
        self.assertTrue(identity.has_role("sales_manager"))
        self.assertFalse(identity.has_role("purchase_manager"))

    async def test_openmes_provider_is_supported(self):
        configured = settings(
            real_identity_provider="openmes",
            erpnext_base_url="",
            erpnext_api_key="",
            erpnext_api_secret="",
        )
        with patch("app.services.identity.OpenMESClient", FakeMESClient):
            identity = await resolve_real_identity(configured)

        self.assertEqual(identity.actor_id, "mes-user")
        self.assertIn("quality_manager", identity.roles)
        self.assertEqual(identity.provider, "openmes")

    async def test_request_bearer_session_takes_precedence_over_service_token(self):
        configured = settings(
            real_identity_provider="auto",
            openmes_user_token="service-account-token",
        )
        with patch("app.services.identity.OpenMESClient", FakeMESClient):
            identity = await resolve_real_identity(
                configured,
                request_bearer_token="browser-session-token",
            )

        self.assertEqual(identity.actor_id, "mes-user")
        self.assertEqual(FakeMESClient.last_token, "browser-session-token")

    async def test_request_bearer_is_not_silently_downgraded_to_erp_service_account(self):
        configured = settings(
            real_identity_provider="erpnext",
            openmes_base_url="",
        )
        with self.assertRaisesRegex(IntegrationError, "Bearer"):
            await resolve_real_identity(configured, request_bearer_token="browser-session-token")

    async def test_openmes_nested_user_envelope_and_role_objects(self):
        class NestedMESClient(FakeMESClient):
            async def current_user(self):
                return {
                    "data": {"user": {"email": "nested@example.com", "roles": [
                        {"id": "random-row-id", "name": "Quality Manager"},
                    ]}}
                }

        configured = settings(
            real_identity_provider="openmes",
            erpnext_base_url="",
            erpnext_api_key="",
            erpnext_api_secret="",
        )
        with patch("app.services.identity.OpenMESClient", NestedMESClient):
            identity = await resolve_real_identity(configured)

        self.assertEqual(identity.actor_id, "nested@example.com")
        self.assertIn("Quality Manager", identity.roles)
        self.assertNotIn("random-row-id", identity.roles)

    async def test_auto_provider_falls_back_to_openmes(self):
        configured = settings(
            real_identity_provider="auto",
            erpnext_base_url="",
            erpnext_api_key="",
            erpnext_api_secret="",
        )
        with patch("app.services.identity.OpenMESClient", FakeMESClient):
            identity = await resolve_real_identity(configured)
        self.assertEqual(identity.actor_id, "mes-user")

    async def test_invalid_role_map_is_explicit_error(self):
        configured = settings(
            real_identity_provider="erpnext",
            real_identity_role_map="not-json",
        )
        with patch("app.services.identity.ERPNextClient", FakeERPClient):
            with self.assertRaisesRegex(IntegrationError, "REAL_IDENTITY_ROLE_MAP"):
                await resolve_real_identity(configured)

    def test_role_check_rejects_missing_business_role(self):
        identity = RealIdentity("user", "User", ("sales_user",), "ERPNext", "erpnext")
        with self.assertRaisesRegex(PermissionError, "purchase_manager"):
            ensure_role(identity, "procurement_approval")

    def test_upstream_spaced_role_matches_business_role(self):
        identity = RealIdentity("user", "User", ("Sales Manager",), "ERPNext", "erpnext")
        ensure_role(identity, "quotation_approval")

    def test_administrator_has_explicit_platform_admin_access(self):
        identity = RealIdentity("Administrator", "Administrator", ("Administrator",), "ERPNext", "erpnext")
        ensure_role(identity, "procurement_approval")


    async def test_erpnext_random_role_docnames_are_filtered(self):
        class NoisyERPClient(FakeERPClient):
            async def get_document(self, doctype, name):
                return {
                    "full_name": "Alice",
                    "roles": [
                        {"role": "Sales User"},
                        {"role": "hn3orhl918"},
                        {"role": "i2802am1gr"},
                        "Quality Manager",
                    ],
                }

        configured = settings(real_identity_provider="erpnext")
        with patch("app.services.identity.ERPNextClient", NoisyERPClient):
            identity = await resolve_real_identity(configured)

        names = set(identity.roles)
        self.assertIn("Sales User", names)
        self.assertIn("Quality Manager", names)
        self.assertNotIn("hn3orhl918", names)
        self.assertNotIn("i2802am1gr", names)


if __name__ == "__main__":
    unittest.main()
