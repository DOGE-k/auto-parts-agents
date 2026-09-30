from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from app.adapters.mes.openmes import OpenMESClient
from app.services import real_order


class OpenMESQualityLifecycleClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposition_and_actions_use_documented_routes(self):
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "PUT":
                return httpx.Response(200, json={"data": {"id": 7, "disposition": "rework"}})
            return httpx.Response(200, json={"data": [{"id": 12, "type": "corrective", "status": "verified"}]})

        transport_client = httpx.AsyncClient(
            base_url="https://mes.example/",
            transport=httpx.MockTransport(handler),
        )
        client = OpenMESClient("https://mes.example", user_token="user-token", client=transport_client)
        try:
            disposition = await client.set_issue_disposition(
                7,
                disposition="rework",
                non_conforming_qty=2,
                root_cause="尺寸偏差",
                containment_action="隔离并返工",
                nc_source="internal",
            )
            actions = await client.list_issue_actions(7)
        finally:
            await client.aclose()
            await transport_client.aclose()

        self.assertEqual(disposition["data"]["disposition"], "rework")
        self.assertEqual(actions[0]["id"], 12)
        self.assertEqual(seen[0].method, "PUT")
        self.assertEqual(seen[0].url.path, "/api/v1/issues/7/disposition")
        self.assertEqual(json.loads(seen[0].content)["containment_action"], "隔离并返工")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer user-token")
        self.assertEqual(seen[1].method, "GET")
        self.assertEqual(seen[1].url.path, "/api/v1/issues/7/actions")


class QualityLifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    def _package(self, issue: dict) -> dict:
        return {
            "work_order_id": "2",
            "quality_records": [issue],
            "open_issues_count": 0 if issue["status"] in {"RESOLVED", "CLOSED"} else 1,
            "missing_documents": [],
            "inspections": [],
            "quality_gate_passed": issue["status"] in {"RESOLVED", "CLOSED"},
            "gate_details": {},
        }

    async def test_disposition_requires_root_cause_and_containment(self):
        result = real_order.request_quality_issue_disposition(
            "7", "2", "operator", disposition="scrap", root_cause="", containment_action="",
        )
        self.assertFalse(result["success"])
        self.assertIn("root_cause", result["error"])

    async def test_disposition_approval_writes_and_verifies_read_back(self):
        before = {
            "record_id": "7", "status": "OPEN", "disposition": "pending",
            "root_cause": "", "containment_action": "",
        }
        after = {
            **before, "status": "RESOLVED", "disposition": "rework",
            "root_cause": "尺寸偏差", "containment_action": "隔离并返工",
        }

        class FakeMES:
            async def set_quality_issue_disposition(self, issue_id, **kwargs):
                self.payload = kwargs
                return {"data": {"id": issue_id, **kwargs}}

        fake_mes = FakeMES()
        with patch.object(real_order, "quality_package", side_effect=[self._package(before), self._package(after)]), \
             patch.object(real_order, "get_mes_adapter", return_value=fake_mes):
            requested = real_order.request_quality_issue_disposition(
                "7", "2", "requester", disposition="rework",
                non_conforming_qty=2, root_cause="尺寸偏差", containment_action="隔离并返工",
            )
            approval_id = requested["approval"]["approval_id"]
            approved = real_order.approve_quality_issue_disposition(approval_id, "quality-manager")
            result = await real_order.set_quality_issue_disposition("7", approval_id, "quality-manager")

        self.assertTrue(approved["success"])
        self.assertTrue(result["success"])
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(fake_mes.payload["disposition"], "rework")

    async def test_disposition_qty_format_difference_does_not_fail_verification(self):
        """审批载荷 1800.0（JSON number）与回读 "1800" 的格式差异不得误判为回读不一致。

        2026-09-30 真实验收发现：写入已到达 OpenMES，但 non_conforming_qty
        按字符串比较（"1800.0" vs "1800"）导致误报 WRITE_UNVERIFIED。
        """
        before = {
            "record_id": "7", "status": "OPEN", "disposition": "pending",
            "root_cause": "", "containment_action": "",
        }
        after = {
            **before, "status": "RESOLVED", "disposition": "rework",
            "root_cause": "尺寸偏差", "containment_action": "隔离并返工",
            "non_conforming_qty": "1800", "nc_source": "internal",
        }

        class FakeMES:
            async def set_quality_issue_disposition(self, issue_id, **kwargs):
                return {"data": {"id": issue_id, **kwargs}}

        with patch.object(real_order, "quality_package", side_effect=[self._package(before), self._package(after)]),              patch.object(real_order, "get_mes_adapter", return_value=FakeMES()):
            requested = real_order.request_quality_issue_disposition(
                "7", "2", "requester", disposition="rework",
                non_conforming_qty=1800.0, root_cause="尺寸偏差", containment_action="隔离并返工",
                nc_source="internal",
            )
            approval_id = requested["approval"]["approval_id"]
            real_order.approve_quality_issue_disposition(approval_id, "quality-manager")
            result = await real_order.set_quality_issue_disposition("7", approval_id, "quality-manager")

        self.assertTrue(result["success"], result)
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(result["status"], "DISPOSITION_RECORDED")

    async def test_quality_package_scopes_inspections_to_work_order_batch_lots(self):
        """检验维度按工单批次 lot 关联（前缀扩展也算），不做全局计数。"""
        from app.services import real_order as ro

        captured = {}

        class FakeMES:
            async def get_quality_records(self, scope):
                return []

            async def get_work_order_documents(self, work_order_id):
                return [{"doc_type": "SOP"}]

            async def get_inspections(self, scope):
                return [
                    {"inspection_id": "1", "lot_number": "TEST_LOT_PAGE_9-IQC-01"},
                    {"inspection_id": "2", "lot_number": "TEST_LOT_PAGE_9"},
                    {"inspection_id": "3", "lot_number": "OTHER-LOT"},
                ]

            async def get_work_order_batches(self, work_order_id):
                captured["wo"] = work_order_id
                return [{"batch_id": "3", "lot_number": "TEST_LOT_PAGE_9", "steps": []}]

        with patch.object(ro, "get_mes_adapter", return_value=FakeMES()):
            result = await ro.quality_package("9")
        lots = [i["lot_number"] for i in result["inspections"]]
        self.assertEqual(captured["wo"], "9")
        self.assertEqual(sorted(lots), ["TEST_LOT_PAGE_9", "TEST_LOT_PAGE_9-IQC-01"])
        self.assertEqual(result["gate_details"]["inspections_total"], 3)
        self.assertEqual(result["gate_details"]["inspection_count"], 2)
        self.assertEqual(result["gate_details"]["inspections_scope"], "work_order_batch_lots")

    async def test_workflow_states_restore_disposition_progress(self):
        """刷新恢复：按 issue 返回最近的处置/关闭审批与载荷。"""
        requested = real_order.request_quality_issue_disposition(
            "42", "2", "requester", disposition="rework",
            non_conforming_qty=3, root_cause="根因", containment_action="遏制",
        )
        approval_id = requested["approval"]["approval_id"]
        real_order.approve_quality_issue_disposition(approval_id, "quality-manager")

        states = real_order.get_quality_issue_workflow_states()
        entry = states["42"]
        self.assertEqual(entry["disposition"]["approval_id"], approval_id)
        self.assertTrue(entry["disposition"]["approved"])
        self.assertEqual(entry["disposition"]["work_order_id"], "2")
        self.assertEqual(entry["disposition"]["disposition"], "rework")
        self.assertNotIn("close", entry)

    async def test_closure_check_requires_verified_actions(self):
        issue = {
            "record_id": "7", "status": "RESOLVED", "disposition": "rework",
            "root_cause": "尺寸偏差", "containment_action": "隔离并返工",
        }

        class FakeMES:
            def __init__(self, actions):
                self.actions = actions

            async def get_quality_issue_actions(self, issue_id):
                return self.actions

        with patch.object(real_order, "quality_package", return_value=self._package(issue)), \
             patch.object(real_order, "get_mes_adapter", return_value=FakeMES([{"action_id": "A1", "status": "done"}])):
            blocked = await real_order.assess_quality_issue_closure("2", "7")
        self.assertFalse(blocked["closure_ready"])
        self.assertFalse(blocked["checks"]["corrective_actions_verified"])

        with patch.object(real_order, "quality_package", return_value=self._package(issue)), \
             patch.object(real_order, "get_mes_adapter", return_value=FakeMES([{"action_id": "A1", "status": "verified"}])):
            ready = await real_order.assess_quality_issue_closure("2", "7")
        self.assertTrue(ready["closure_ready"])

    async def test_close_requires_check_then_reads_closed_status(self):
        before = {
            "record_id": "7", "status": "RESOLVED", "disposition": "rework",
            "root_cause": "尺寸偏差", "containment_action": "隔离并返工",
        }
        after = {**before, "status": "CLOSED"}

        class FakeMES:
            async def get_quality_issue_actions(self, issue_id):
                return [{"action_id": "A1", "status": "verified"}]

            async def close_quality_issue(self, issue_id):
                return {"data": {"id": issue_id, "status": "CLOSED"}}

        with patch.object(real_order, "quality_package", side_effect=[self._package(before), self._package(after)]), \
             patch.object(real_order, "get_mes_adapter", return_value=FakeMES()):
            requested = real_order.request_quality_issue_close("7", "2", "requester")
            approval_id = requested["approval"]["approval_id"]
            real_order.approve_quality_issue_close(approval_id, "quality-manager")
            result = await real_order.close_quality_issue_with_approval("7", approval_id, "quality-manager")

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "CLOSED")
        self.assertTrue(result["read_back_verified"])


if __name__ == "__main__":
    unittest.main()
