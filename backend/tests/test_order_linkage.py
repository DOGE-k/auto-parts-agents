"""真实订单关联服务单元测试（假适配器，不依赖真实系统）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.integrations.errors import IntegrationError
from app.services import order_linkage


def _make_erp_order(order_id: str = "SAL-ORD-2026-00001") -> dict:
    return {
        "order_id": order_id,
        "customer_id": "上汽集团",
        "customer_name": "上汽集团",
        "transaction_date": "2026-09-28",
        "delivery_date": "2026-10-15",
        "status": "Draft",
        "currency": "CNY",
        "total": "42500",
        "items": [
            {
                "item_code": "BD-2401",
                "item_name": "制动盘-前轮 Brake Disc Front",
                "qty": "500.00",
                "rate": "85.0",
                "uom": "Nos",
                "delivery_date": "2026-10-15",
                "warehouse": "Stores - APM",
            }
        ],
        "authority": "ERPNext",
        "data_source": "erpnext_api",
        "found": True,
    }


def _make_wo(work_order_no: str, product_id: str, customer_order_no: str = "") -> dict:
    return {
        "work_order_id": "2",
        "work_order_no": work_order_no,
        "customer_order_no": customer_order_no,
        "product_id": product_id,
        "product_name": "制动盘-前轮 Brake Disc Front",
        "product_external_code": "BD-2401",
        "product_external_system": "erpnext",
        "quantity": "500.00",
        "completed_qty": "0.00",
        "status": "ACCEPTED",
        "due_date": "2026-10-15T00:00:00.000000Z",
        "line_id": "1",
        "line_name": "DEMO Assembly Line 01",
        "authority": "OpenMES",
        "data_source": "openmes_api",
    }


class _FakeERP:
    authority = "ERPNext"

    def __init__(self, order: dict | None, error: Exception | None = None):
        self._order = order
        self._error = error

    async def get_sales_order(self, order_id: str) -> dict:
        if self._error:
            raise self._error
        if self._order is None:
            return {**_make_erp_order(order_id), "found": False}
        return self._order


class _FakeMES:
    authority = "OpenMES"

    def __init__(self, work_orders: list[dict], error: Exception | None = None):
        self._work_orders = work_orders
        self._error = error

    async def get_work_orders_strict(self, scope: dict) -> list[dict]:
        if self._error:
            raise self._error
        return self._work_orders


class OrderLinkageTests(unittest.IsolatedAsyncioTestCase):
    async def test_linked_when_customer_order_no_matches(self) -> None:
        erp = _FakeERP(_make_erp_order())
        mes = _FakeMES([_make_wo("WO-2026-001", "BD-2401", customer_order_no="SAL-ORD-2026-00001")])
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.get_order_mes_link("SAL-ORD-2026-00001")

        self.assertTrue(result["linked"])
        self.assertEqual(result["status"], "LINKED")
        link = result["association"]["links"][0]
        self.assertEqual(link["link_field"], "customer_order_no")
        self.assertEqual(link["matched_value"], "SAL-ORD-2026-00001")
        self.assertEqual(link["erp_record"]["record_id"], "SAL-ORD-2026-00001")
        self.assertEqual(link["mes_record"]["record_no"], "WO-2026-001")
        self.assertEqual(link["erp_record"]["source"], "ERPNext")
        self.assertEqual(link["mes_record"]["source"], "OpenMES")
        self.assertTrue(link["quantity_consistent"])
        self.assertTrue(link["due_date_consistent"])

    async def test_unlinked_when_no_work_order_references_the_order(self) -> None:
        erp = _FakeERP(_make_erp_order())
        # 产品编码一致但 customer_order_no 为空 —— 不得作为关联
        mes = _FakeMES([_make_wo("WO-2026-001", "BD-2401")])
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.get_order_mes_link("SAL-ORD-2026-00001")

        self.assertFalse(result["linked"])
        self.assertIsNone(result["association"])
        self.assertEqual(result["unlinked"]["message"], "未建立关联")
        self.assertEqual(result["unlinked"]["mes_work_orders_total"], 1)
        self.assertEqual(result["unlinked"]["mes_work_orders_with_order_ref"], 0)
        # 候选必须显式标注"不构成正式关联"
        candidates = result["unlinked"]["candidates_for_manual_review"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["work_order_no"], "WO-2026-001")
        self.assertTrue(candidates[0]["not_an_association"])
        self.assertEqual(candidates[0]["match_basis"], "product_code_only")

    async def test_product_code_alone_never_creates_association(self) -> None:
        """即使产品编码和数量交期完全一致，customer_order_no 不同就不算关联。"""
        erp = _FakeERP(_make_erp_order())
        mes = _FakeMES([_make_wo("WO-2026-001", "BD-2401", customer_order_no="SOME-OTHER-ORDER")])
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.get_order_mes_link("SAL-ORD-2026-00001")

        self.assertFalse(result["linked"])
        self.assertEqual(result["unlinked"]["message"], "未建立关联")

    async def test_erp_order_not_found_reports_explicitly(self) -> None:
        erp = _FakeERP(None)
        mes = _FakeMES([_make_wo("WO-2026-001", "BD-2401", customer_order_no="SAL-ORD-2026-00001")])
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.get_order_mes_link("SAL-ORD-2026-99999")

        self.assertEqual(result["status"], "ERP_ORDER_NOT_FOUND")
        self.assertIsNone(result["linked"])
        self.assertNotIn("association", {k for k, v in result.items() if v})

    async def test_erp_connection_error_propagates(self) -> None:
        """ERP 连接失败必须抛错，不得静默当作"未建立关联"。"""
        erp = _FakeERP(None, error=IntegrationError("boom", status_code=503))
        mes = _FakeMES([])
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            with self.assertRaises(IntegrationError):
                await order_linkage.get_order_mes_link("SAL-ORD-2026-00001")

    async def test_mes_connection_error_propagates(self) -> None:
        """MES 连接失败必须抛错，不得把失败伪装成"未建立关联"。"""
        erp = _FakeERP(_make_erp_order())
        mes = _FakeMES([], error=IntegrationError("mes down", status_code=503))
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            with self.assertRaises(IntegrationError):
                await order_linkage.get_order_mes_link("SAL-ORD-2026-00001")

    async def test_quantity_mismatch_is_reported_as_evidence_not_blocker(self) -> None:
        """customer_order_no 是正式关联依据；数量不一致只作为需人工核对的证据。"""
        erp = _FakeERP(_make_erp_order())
        wo = _make_wo("WO-2026-001", "BD-2401", customer_order_no="SAL-ORD-2026-00001")
        wo["quantity"] = "300.00"
        mes = _FakeMES([wo])
        with patch.object(order_linkage, "get_erp_adapter", return_value=erp), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.get_order_mes_link("SAL-ORD-2026-00001")

        self.assertTrue(result["linked"])
        link = result["association"]["links"][0]
        self.assertFalse(link["quantity_consistent"])


class MockAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_adapters_implement_strict_and_sales_order_methods(self) -> None:
        from app.adapters.erp.mock import MockERPAdapter
        from app.adapters.mes.mock import MockMESAdapter

        erp = MockERPAdapter()
        orders = await erp.list_sales_orders()
        self.assertTrue(all(o["authority"] == "MockERP" for o in orders))
        detail = await erp.get_sales_order(orders[0]["order_id"])
        self.assertTrue(detail["found"])

        mes = MockMESAdapter()
        strict = await mes.get_work_orders_strict({})
        self.assertTrue(all("customer_order_no" in wo for wo in strict))


class LinkWorkOrderTests(unittest.IsolatedAsyncioTestCase):
    """link_work_order_to_erp_order 写入路径测试（假适配器）。"""

    def _wo(self) -> dict:
        wo = _make_wo("WO-2026-001", "BD-2401")
        wo.update({
            "line_code": "DEMO_LINE_01",
            "product_type_code": "BD-2401",
            "description": "",
            "due_date": "2026-10-15T00:00:00.000000Z",
        })
        return wo

    def _mes(self) -> "_RecordingMES":
        return _RecordingMES([self._wo()])

    async def test_link_writes_and_verifies_read_back(self) -> None:
        mes = self._mes()
        with patch.object(order_linkage, "get_erp_adapter", return_value=_FakeERP(_make_erp_order())), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.link_work_order_to_erp_order(
                "WO-2026-001", "SAL-ORD-2026-00001", approved_by="user", approval_note="test"
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["read_back"]["verified"])
        self.assertEqual(result["read_back"]["customer_order_no"], "SAL-ORD-2026-00001")
        self.assertEqual(result["import_result"]["updated"], 1)
        # payload 携带原值回传，防止 update_or_create 覆盖
        payload = result["import_payload"]
        self.assertEqual(payload["line_code"], "DEMO_LINE_01")
        self.assertEqual(payload["product_type_code"], "BD-2401")
        self.assertEqual(payload["planned_qty"], 500.0)
        self.assertEqual(payload["due_date"], "2026-10-15")

    async def test_link_rejects_missing_approval(self) -> None:
        mes = self._mes()
        with patch.object(order_linkage, "get_erp_adapter", return_value=_FakeERP(_make_erp_order())), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.link_work_order_to_erp_order(
                "WO-2026-001", "SAL-ORD-2026-00001", approved_by="", approval_note=""
            )
        self.assertFalse(result["success"])
        self.assertIn("审批", result["error"])
        self.assertEqual(mes.import_calls, 0, "缺少审批时不得调用写入接口")

    async def test_link_rejects_demo_work_order(self) -> None:
        mes = _RecordingMES([_make_wo("DEMO_WO_001", "DEMO_BRACKET_001")])
        with patch.object(order_linkage, "get_erp_adapter", return_value=_FakeERP(_make_erp_order())), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.link_work_order_to_erp_order(
                "DEMO_WO_001", "SAL-ORD-2026-00001", approved_by="user", approval_note="test"
            )
        self.assertFalse(result["success"])
        self.assertIn("DEMO", result["error"])
        self.assertEqual(mes.import_calls, 0)

    async def test_link_rejects_missing_erp_order(self) -> None:
        mes = self._mes()
        with patch.object(order_linkage, "get_erp_adapter", return_value=_FakeERP(None)), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.link_work_order_to_erp_order(
                "WO-2026-001", "SAL-ORD-2026-99999", approved_by="user", approval_note="test"
            )
        self.assertFalse(result["success"])
        self.assertEqual(mes.import_calls, 0)

    async def test_link_fails_when_read_back_mismatches(self) -> None:
        class _StubbornMES(_RecordingMES):
            """import 后仍读不到关联值——回读不一致必须判失败。"""

            async def get_work_orders_strict(self, scope: dict) -> list[dict]:
                if self.import_calls > 0:
                    return [{**w, "customer_order_no": ""} for w in self._work_orders]
                return self._work_orders

        mes = _StubbornMES([self._wo()])
        with patch.object(order_linkage, "get_erp_adapter", return_value=_FakeERP(_make_erp_order())), \
             patch.object(order_linkage, "get_mes_adapter", return_value=mes), \
             patch.object(order_linkage, "get_adapter_mode", return_value="real"):
            result = await order_linkage.link_work_order_to_erp_order(
                "WO-2026-001", "SAL-ORD-2026-00001", approved_by="user", approval_note="test"
            )
        self.assertFalse(result["success"])
        self.assertFalse(result["read_back"]["verified"])


class _RecordingMES(_FakeMES):
    """记录 import 调用并真正更新内存工单的假 MES。"""

    def __init__(self, work_orders: list[dict]):
        super().__init__(work_orders)
        self.import_calls = 0

    async def import_erp_work_orders(
        self, orders: list[dict], strategy: str = "update_or_create"
    ) -> dict:
        self.import_calls += 1
        updated = 0
        for row in orders:
            for wo in self._work_orders:
                if wo["work_order_no"] == row.get("order_no"):
                    wo["customer_order_no"] = row.get("customer_order_no", "")
                    updated += 1
        return {"data": {"imported": 0, "updated": updated, "skipped": 0, "errors": []}, "authority": "OpenMES"}


if __name__ == "__main__":
    unittest.main()
