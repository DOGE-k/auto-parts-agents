"""阶段九：跨工单质量待办队列（list_open_issues / quality_todo_list / 端点）。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.adapters.mes.openmes_adapter import OpenMESAdapter
from app.integrations.errors import IntegrationError
from app.main import real_order_quality_todo
from app.services.identity import RealIdentity
from app.services.real_order import quality_todo_list


def _raw(issue_id, status, reported_at, *, work_order_no="WO-2026-001",
         severity="MEDIUM", disposition=None, assigned=None):
    """按 2026-09-30 实测 payload 形状构造 issue（severity 在 issue_type 上）。"""
    return {
        "id": issue_id,
        "work_order_id": 2,
        "title": f"Issue {issue_id}",
        "status": status,
        "disposition": disposition,
        "reported_at": reported_at,
        "work_order": {"id": 2, "order_no": work_order_no},
        "issue_type": {"code": "MEASUREMENT_ERROR", "name": "Dimensional", "severity": severity},
        "assigned_to": assigned,
    }


def _page(items, page=1, last_page=1):
    return {
        "data": items,
        "meta": {"current_page": page, "per_page": 20, "total": len(items), "last_page": last_page},
    }


class _FakeIssuesClient:
    def __init__(self, pages_by_status):
        self.pages_by_status = pages_by_status
        self.calls: list[tuple[str, int]] = []

    async def list_issues(self, *, status, page=1):
        self.calls.append((status, page))
        pages = self.pages_by_status[status]
        return pages[page - 1]


class ListOpenIssuesAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_three_statuses_and_maps_contract_fields(self):
        client = _FakeIssuesClient({
            "OPEN": [_page([
                _raw(2, "OPEN", "2026-09-29T10:00:00.000000Z",
                     work_order_no="WO-2026-002", severity="CRITICAL",
                     assigned={"username": "op1", "name": "Operator One"}),
            ])],
            "ACKNOWLEDGED": [_page([])],
            "RESOLVED": [_page([_raw(1, "RESOLVED", "2026-09-28T04:35:31.000000Z", disposition="pending")])],
        })
        items = await OpenMESAdapter(client).list_open_issues()

        # 按 reported_at 倒序
        self.assertEqual([i["issue_id"] for i in items], ["2", "1"])
        by_id = {i["issue_id"]: i for i in items}
        self.assertEqual(by_id["1"]["work_order_no"], "WO-2026-001")
        self.assertEqual(by_id["1"]["work_order_id"], "2")
        self.assertEqual(by_id["1"]["severity"], "MEDIUM")
        self.assertEqual(by_id["1"]["status"], "RESOLVED")
        self.assertEqual(by_id["1"]["disposition"], "pending")
        self.assertEqual(by_id["1"]["assigned_to"], "")
        self.assertEqual(by_id["2"]["severity"], "CRITICAL")
        self.assertEqual(by_id["2"]["assigned_to"], "op1")
        self.assertEqual(by_id["2"]["authority"], "OpenMES")
        self.assertEqual(by_id["2"]["data_source"], "openmes_issues")
        # CLOSED 不进待办：从不请求该状态
        self.assertEqual({s for s, _ in client.calls}, {"OPEN", "ACKNOWLEDGED", "RESOLVED"})

    async def test_follows_pagination_meta(self):
        client = _FakeIssuesClient({
            "OPEN": [
                _page([_raw(1, "OPEN", "2026-09-29T10:00:00.000000Z")], page=1, last_page=2),
                _page([_raw(3, "OPEN", "2026-09-27T10:00:00.000000Z")], page=2, last_page=2),
            ],
            "ACKNOWLEDGED": [_page([])],
            "RESOLVED": [_page([])],
        })
        items = await OpenMESAdapter(client).list_open_issues()
        self.assertEqual({i["issue_id"] for i in items}, {"1", "3"})
        self.assertIn(("OPEN", 2), client.calls)

    async def test_failure_propagates_and_is_not_masked_as_empty(self):
        class FailingClient(_FakeIssuesClient):
            async def list_issues(self, *, status, page=1):
                raise ConnectionError("OpenMES unavailable")

        with self.assertRaises(ConnectionError):
            await OpenMESAdapter(FailingClient({})).list_open_issues()


class QualityTodoServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_quality_todo_list_adds_reported_days_and_authority(self):
        reported = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        class _Adapter:
            async def list_open_issues(self, *, statuses):
                assert statuses == ("OPEN", "ACKNOWLEDGED", "RESOLVED")
                return [{
                    "issue_id": "1", "work_order_id": "2", "work_order_no": "WO-2026-001",
                    "title": "制动盘外径尺寸超差", "severity": "MEDIUM", "status": "RESOLVED",
                    "disposition": "pending", "reported_at": reported, "assigned_to": "",
                    "authority": "OpenMES", "data_source": "openmes_issues",
                }]

        with patch("app.services.real_order.get_mes_adapter", lambda: _Adapter()):
            result = await quality_todo_list()

        self.assertEqual(result["authority"], "OpenMES")
        self.assertEqual(result["data_source"], "openmes_issues")
        self.assertEqual(result["items"][0]["reported_days"], 5)

    async def test_missing_reported_at_yields_none_not_zero(self):
        class _Adapter:
            async def list_open_issues(self, *, statuses):
                return [{
                    "issue_id": "9", "reported_at": "",
                    "authority": "OpenMES", "data_source": "openmes_issues",
                }]

        with patch("app.services.real_order.get_mes_adapter", lambda: _Adapter()):
            result = await quality_todo_list()
        self.assertIsNone(result["items"][0]["reported_days"])


class QualityTodoEndpointTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _identity():
        return RealIdentity("admin", "Administrator", ("quality_manager",), "OpenMES", "openmes")

    async def test_endpoint_returns_items_with_resolved_identity(self):
        class _Adapter:
            async def list_open_issues(self, *, statuses):
                return [{
                    "issue_id": "1", "work_order_no": "WO-2026-001", "title": "超差",
                    "reported_at": "", "authority": "OpenMES", "data_source": "openmes_issues",
                }]

        with patch("app.services.real_order.get_mes_adapter", lambda: _Adapter()):
            result = await real_order_quality_todo(identity=self._identity())

        self.assertEqual(result["items"][0]["issue_id"], "1")
        self.assertEqual(result["authenticated_identity"]["actor_id"], "admin")

    async def test_endpoint_maps_integration_error_to_502(self):
        class _Adapter:
            async def list_open_issues(self, *, statuses):
                raise IntegrationError("网络连接失败", code="network_error")

        with patch("app.services.real_order.get_mes_adapter", lambda: _Adapter()):
            with self.assertRaises(HTTPException) as ctx:
                await real_order_quality_todo(identity=self._identity())
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_endpoint_maps_invalid_payload_to_502(self):
        class _Adapter:
            async def list_open_issues(self, *, statuses):
                raise ValueError("OpenMES 质量问题列表返回格式不符合其 API 文档")

        with patch("app.services.real_order.get_mes_adapter", lambda: _Adapter()):
            with self.assertRaises(HTTPException) as ctx:
                await real_order_quality_todo(identity=self._identity())
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["code"], "openmes_issues_invalid")


if __name__ == "__main__":
    unittest.main()
