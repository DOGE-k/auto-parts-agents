"""OpenMES quality records must stay scoped to their work order."""

import unittest

from app.adapters.mes.openmes_adapter import OpenMESAdapter


class _Client:
    def __init__(self, issues, nested=(), order_no="WO-2026-001"):
        self.issues = issues
        self.nested = nested
        self.order_no = order_no

    async def get_work_order(self, work_order_id):
        return {"data": {"id": work_order_id, "order_no": self.order_no, "issues": self.nested}}

    async def list_erp_quality_issues(self, *, since=None):
        return {"data": self.issues}


class QualityScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_other_work_orders_and_deduplicates_nested_issues(self):
        client = _Client(
            [
                {"id": 1, "work_order_no": "WO-2026-001", "status": "OPEN"},
                {"id": 2, "work_order_no": "WO-2026-002", "status": "OPEN"},
            ],
            nested=[{"id": 1, "status": "OPEN"}, {"id": 3, "status": "RESOLVED"}],
        )
        records = await OpenMESAdapter(client).get_quality_records({"work_order_id": "2"})
        self.assertEqual({r["record_id"] for r in records}, {"1", "3"})
        self.assertTrue(all(r["work_order_no"] == "WO-2026-001" for r in records))

    async def test_missing_work_order_number_fails_closed(self):
        client = _Client([{"id": 1, "work_order_no": "WO-2026-001"}], order_no="")
        with self.assertRaises(ValueError):
            await OpenMESAdapter(client).get_quality_records({"work_order_id": "2"})

    async def test_export_failure_is_not_turned_into_empty_quality_result(self):
        class FailingClient(_Client):
            async def list_erp_quality_issues(self, *, since=None):
                raise ConnectionError("OpenMES unavailable")

        with self.assertRaises(ConnectionError):
            await OpenMESAdapter(FailingClient([])).get_quality_records({"work_order_id": "2"})
