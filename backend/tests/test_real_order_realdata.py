"""报价/采购 Agent 真实化逻辑单元测试（假适配器，不依赖真实系统）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import real_order


class _FakeERP:
    authority = "ERPNext"

    def __init__(self, prices: dict[str, list[dict]] | None = None, bom: dict | None = None,
                 inventory: list[dict] | None = None,
                 warehouses: list[dict] | None = None, company: dict | None = None,
                 suppliers: list[dict] | None = None, items: dict[str, dict] | None = None,
                 supplier_prices: dict[str, dict[str, list[dict]]] | None = None):
        self._prices = prices or {}
        self._bom = bom or {"found": False, "items": [], "authority": "ERPNext"}
        self._inventory = inventory or []
        self._warehouses = warehouses or []
        self._company = company or {"company_name": "", "found": False}
        self._suppliers = suppliers or []
        self._items = items or {}
        self._supplier_prices = supplier_prices or {}

    async def get_customer(self, customer_id):
        return {"customer_id": customer_id, "customer_name": customer_id, "authority": "ERPNext", "found": True}

    async def get_item(self, item_id, version=None):
        base = {"item_id": item_id, "item_name": item_id, "stock_uom": "Nos", "authority": "ERPNext", "found": True}
        base.update(self._items.get(item_id, {}))
        return base

    async def get_prices(self, item_code, as_of, supplier=None):
        if supplier:
            return self._supplier_prices.get(item_code, {}).get(supplier, [])
        return self._prices.get(item_code, [])

    async def get_bom(self, item_id, version=None):
        return self._bom

    async def get_inventory(self, item_ids):
        return self._inventory

    async def list_warehouses(self, company=""):
        return self._warehouses

    async def get_default_company(self):
        return self._company

    async def search_suppliers(self, keyword, limit):
        return self._suppliers


class _FakeMES:
    authority = "OpenMES"

    def __init__(self, work_orders=None):
        self._work_orders = work_orders or []

    async def get_work_orders_strict(self, scope):
        return self._work_orders


def _bom_bd2401():
    return {
        "found": True,
        "bom_id": "BOM-BD-2401-001",
        "items": [
            {"item_code": "CI-RAW", "qty_per_product": 1},
            {"item_code": "M10-BOLT", "qty_per_product": 4},
        ],
        "authority": "ERPNext",
    }


class QuotationRealDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_bom_cost_uses_real_buying_prices_without_markup(self):
        """无 Selling 价时用真实子项 Buying 价计算成本，无 1.3 加成。"""
        erp = _FakeERP(
            prices={
                "CI-RAW": [{"price_id": "P1", "price_list": "Standard Buying", "unit_price": "12.5", "currency": "CNY"}],
                "M10-BOLT": [{"price_id": "P2", "price_list": "Standard Buying", "unit_price": "0.8", "currency": "CNY"}],
            },
            bom=_bom_bd2401(),
        )
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_mes_adapter", return_value=_FakeMES()), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.analyze_quotation("客户A", "DEMO-PROD", 10)

        # 成本 = 1*12.5 + 4*0.8 = 15.7，无加成
        self.assertEqual(float(result["base_price"]), 15.7)
        self.assertEqual(result["pricing_basis"], "erp_bom_cost_no_markup")
        self.assertEqual(result["status"], "DRAFT")
        self.assertFalse(any(m["field"].startswith(("bom_sub_item_price:", "selling_price:")) for m in result["missing_data"]))

    async def test_missing_sub_price_reports_data_missing(self):
        """子项缺真实价格时必须 DATA_MISSING，不得伪造成本。"""
        erp = _FakeERP(
            prices={"CI-RAW": [{"price_id": "P1", "price_list": "Standard Buying", "unit_price": "12.5", "currency": "CNY"}]},
            bom=_bom_bd2401(),
        )
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_mes_adapter", return_value=_FakeMES()), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.analyze_quotation("客户A", "DEMO-PROD", 10)

        self.assertEqual(result["status"], "DATA_MISSING")
        self.assertEqual(result["base_price"], "0")
        self.assertTrue(any(m["field"] == "bom_sub_item_price:M10-BOLT" for m in result["missing_data"]))

    async def test_delivery_estimate_from_real_mes_schedule(self):
        """库存不足时交付估算来自真实 MES 工单 due_date，而非固定公式。"""
        erp = _FakeERP(
            prices={"DEMO-PROD": [{"price_id": "P9", "price_list": "Standard Selling", "unit_price": "85", "currency": "CNY"}]},
            bom=_bom_bd2401(),
            inventory=[{"item_id": "DEMO-PROD", "warehouse": "W", "actual_qty": "0", "authority": "ERPNext"}],
        )
        mes = _FakeMES([{
            "work_order_id": "2", "work_order_no": "WO-2026-001", "product_id": "DEMO-PROD",
            "due_date": "2099-01-01T00:00:00Z", "status": "ACCEPTED", "quantity": "500",
            "customer_order_no": "", "authority": "OpenMES", "data_source": "openmes_api",
        }])
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_mes_adapter", return_value=mes), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.analyze_quotation("客户A", "DEMO-PROD", 500)

        de = result["delivery_estimate"]
        self.assertEqual(de["basis"], "mes_work_order_schedule")
        self.assertIn("WO-2026-001", de["source"])
        self.assertIsNotNone(de["estimated_days"])

    async def test_delivery_estimate_missing_when_no_data(self):
        """无库存无工单无 lead_time 时如实报缺失。"""
        erp = _FakeERP(
            prices={"DEMO-PROD": [{"price_id": "P9", "price_list": "Standard Selling", "unit_price": "85", "currency": "CNY"}]},
            bom=_bom_bd2401(),
            inventory=[{"item_id": "DEMO-PROD", "warehouse": "W", "actual_qty": "0", "authority": "ERPNext"}],
        )
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_mes_adapter", return_value=_FakeMES()), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.analyze_quotation("客户A", "DEMO-PROD", 500)

        de = result["delivery_estimate"]
        self.assertEqual(de["basis"], "missing")
        self.assertIsNone(de["estimated_days"])
        self.assertTrue(any(m["field"].startswith("lead_time:") for m in result["missing_data"]))


class ProcurementRealDataTests(unittest.IsolatedAsyncioTestCase):
    def _quotation(self, item_id="DEMO-PROD", qty=2000):
        q = {
            "quotation_id": "QUO-TEST", "item": {"item_id": item_id}, "quantity": qty,
            "unit_price": "76.5", "currency": "CNY", "status": "DRAFT",
        }
        real_order.save_quotation(q)
        return q

    async def test_net_requirement_missing_price_is_not_fabricated(self):
        """缺 Buying 价时价格如实缺失，不得使用 0.7 系数或 50 元占位。"""
        erp = _FakeERP(prices={}, bom=_bom_bd2401(), items={
            "CI-RAW": {"min_order_qty": "0"},
        })
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.compute_net_requirement("DEMO-PROD", 100)

        self.assertTrue(all(nr["price_status"] == "missing" for nr in result["net_requirements"]))
        self.assertEqual(result["total_estimated_cost"], "")
        self.assertFalse(result["total_estimated_cost_complete"])
        self.assertTrue(any(m["field"].startswith("buying_price:") for m in result["missing_data"]))

    async def test_min_order_qty_raises_order_quantity(self):
        """净需求低于 MOQ 时按 MOQ 上调采购数量，来源标注 min_order_qty。"""
        erp = _FakeERP(
            prices={
                "CI-RAW": [{"price_id": "P1", "price_list": "Standard Buying", "unit_price": "12.5", "currency": "CNY"}],
            },
            bom={"found": True, "bom_id": "BOM-X", "authority": "ERPNext",
                 "items": [{"item_code": "CI-RAW", "qty_per_product": 1}]},
            items={"CI-RAW": {"min_order_qty": "500", "lead_time_days": 9,
                              "suppliers": [{"supplier": "S1"}]}},
        )
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.compute_net_requirement("DEMO-PROD", 100)

        ci = result["net_requirements"][0]
        self.assertEqual(ci["net_requirement"], "100")   # 缺 100
        self.assertEqual(ci["order_qty"], "500")          # 按 MOQ 上调
        self.assertEqual(ci["qty_basis"], "min_order_qty")
        self.assertEqual(ci["lead_time_days"], 9)

    async def test_supplier_options_filtered_by_item_supplier_relation(self):
        """方案只包含实际供应缺料物料的供应商，价格/交期/MOQ 用真实数据。"""
        self._quotation()
        erp = _FakeERP(
            prices={
                "CI-RAW": [{"price_id": "P1", "price_list": "Standard Buying", "unit_price": "12.5", "currency": "CNY"}],
                "M10-BOLT": [{"price_id": "P2", "price_list": "Standard Buying", "unit_price": "0.8", "currency": "CNY"}],
            },
            bom=_bom_bd2401(),
            inventory=[
                {"item_id": "CI-RAW", "warehouse": "W", "actual_qty": "0"},
                {"item_id": "M10-BOLT", "warehouse": "W", "actual_qty": "0"},
            ],
            suppliers=[
                {"supplier_id": "S1", "supplier_name": "供应商甲", "authority": "ERPNext"},
                {"supplier_id": "S2", "supplier_name": "供应商乙", "authority": "ERPNext"},
                {"supplier_id": "S3", "supplier_name": "无关供应商", "authority": "ERPNext"},
            ],
            items={
                "CI-RAW": {"min_order_qty": "100", "lead_time_days": 12,
                           "suppliers": [{"supplier": "S1"}, {"supplier": "S2"}]},
                "M10-BOLT": {"min_order_qty": "500", "lead_time_days": 7,
                             "suppliers": [{"supplier": "S1"}]},
            },
            # 供应商特定价格：S2 供应 CI-RAW 但价格更低
            supplier_prices={
                "CI-RAW": {
                    "S2": [{"price_id": "SP-CI-2", "price_list": "Standard Buying", "unit_price": "11.0", "currency": "CNY", "supplier": "S2"}],
                },
            },
        )
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            plan = await real_order.analyze_procurement("QUO-TEST")

        opts = {o["supplier_id"]: o for o in plan["supplier_options"]}
        # 无关供应商不生成方案
        self.assertNotIn("S3", opts)
        # S1 覆盖两项（CI-RAW 12.5 + M10-BOLT 0.8），交期取 max(12,7)=12
        self.assertEqual(opts["S1"]["coverage"], "2/2")
        self.assertEqual(opts["S1"]["lead_time_days"], 12)
        # CI-RAW 净需求 2000 ≥ MOQ 100 → 按净需求
        ci = next(it for it in opts["S1"]["items"] if it["item_id"] == "CI-RAW")
        self.assertEqual(ci["qty_basis"], "net_requirement")
        # S1 用标准价回退（无特定价）
        self.assertEqual(ci["price_basis"], "standard_buying_price_fallback")
        # S2 只覆盖 CI-RAW，用供应商特定价 11.0
        self.assertEqual(opts["S2"]["coverage"], "1/2")
        ci2 = opts["S2"]["items"][0]
        self.assertEqual(ci2["price_basis"], "supplier_specific_price")
        self.assertEqual(float(ci2["unit_price"]), 11.0)
        self.assertEqual(ci2["price_record"], "SP-CI-2")
        # 推荐规则：S1 覆盖全部 → 推荐 S1 而非更便宜的 S2（只覆盖部分）
        self.assertEqual(plan["recommended_option_id"], "OPT-1")
        self.assertIn("lowest_total_cost_v2", plan["recommendation"])
        # S1 的 CI-RAW 行是标准价回退
        self.assertTrue(opts["S1"]["covers_all_shortage_items"])

    async def test_no_shortage_plan_keeps_frontend_schema(self):
        """库存充足分支也返回页面渲染所需的统一字段，避免采购页白屏。"""
        self._quotation(item_id="DEMO-PROD", qty=100)
        erp = _FakeERP(
            bom={"found": True, "bom_id": "BOM-X", "authority": "ERPNext",
                 "items": [{"item_code": "CI-RAW", "qty_per_product": 1}]},
            inventory=[{"item_id": "CI-RAW", "warehouse": "W", "actual_qty": "1000"}],
        )
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            plan = await real_order.analyze_procurement("QUO-TEST")

        self.assertEqual(plan["status"], "NO_SHORTAGE")
        self.assertEqual(plan["quotation_status"], "DRAFT")
        self.assertEqual(plan["recommendation_rule"], "not_applicable_no_shortage")
        self.assertEqual(plan["supplier_options"], [])
        self.assertIsInstance(plan["data_limitations"], list)
        self.assertIsInstance(plan["evidence"], list)

    async def test_po_draft_rejected_when_price_missing(self):
        """方案中有物料缺真实价格时，拒绝创建 PO 草稿。"""
        self._quotation()
        plan = {
            "plan_id": "PROC-TEST",
            "status": "APPROVED",
            "selected_option_id": "OPT-1",
            "supplier_options": [{
                "option_id": "OPT-1",
                "supplier_id": "S1",
                "supplier_name": "供应商甲",
                "lead_time_days": None,
                "total_cost": "",
                "total_cost_complete": False,
                "currency": "CNY",
                "items": [
                    {"item_id": "CI-RAW", "item_name": "铸铁", "quantity": "100", "uom": "Nos",
                     "unit_price": "12.5", "price_basis": "standard_buying_price", "price_record": "P1", "line_total": "1250"},
                    {"item_id": "M10-BOLT", "item_name": "螺栓", "quantity": "400", "uom": "Nos",
                     "unit_price": "", "price_basis": "missing", "price_record": "", "line_total": ""},
                ],
            }],
        }
        real_order.save_procurement_plan(plan)
        erp = _FakeERP(
            company={"company_name": "AutoParts Manufacturing", "found": True},
            warehouses=[{"warehouse": "Stores - APM", "warehouse_type": "Stores", "is_group": False}],
        )
        # 审批（使用返回的真实审批编号）
        approved = await real_order.approve_procurement_plan("PROC-TEST", "OPT-1", True, "user", notes="t")
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            result = await real_order.create_erp_purchase_order_from_plan(
                "PROC-TEST", approved["approval_id"], "user"
            )
        self.assertFalse(result["success"])
        self.assertIn("缺少真实价格", result["error"])


if __name__ == "__main__":
    unittest.main()
