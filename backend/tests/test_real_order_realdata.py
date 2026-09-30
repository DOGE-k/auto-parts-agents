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

    async def test_missing_bom_blocks_shortage_evaluation(self):
        """无 BOM 时缺料评估不可用：如实返回 EVALUATION_BLOCKED，不得伪造成"无缺料/库存充足"。"""
        self._quotation(item_id="DEMO-PROD", qty=800)
        # _FakeERP 默认 bom found=False（模拟未配置 BOM 的成品物料）
        erp = _FakeERP(inventory=[])
        with patch.object(real_order, "get_erp_adapter", return_value=erp), \
             patch.object(real_order, "get_adapter_mode", return_value="real"):
            net = await real_order.compute_net_requirement("DEMO-PROD", 800)
            plan = await real_order.analyze_procurement("QUO-TEST")

        self.assertFalse(net["shortage_evaluable"])
        self.assertTrue(any(m["field"] == "bom" for m in net["missing_data"]))
        self.assertEqual(plan["status"], "EVALUATION_BLOCKED")
        self.assertEqual(plan["recommendation_rule"], "not_applicable_bom_missing")
        self.assertIn("BOM", plan["recommendation"])
        self.assertNotIn("库存充足", plan["recommendation"])
        self.assertEqual(plan["supplier_options"], [])
        self.assertTrue(any(d.get("field") == "bom" for d in plan["data_limitations"]))
        self.assertFalse(plan["net_requirement"]["shortage_evaluable"])

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


class _FakeMESForDispatch:
    """工单下达测试用假 OpenMES 适配器（记录创建调用，可注入回读篡改）。"""

    authority = "OpenMES"

    def __init__(self, corrupt_read_back: bool = False):
        self.created: list[dict] = []
        self._orders: list[dict] = []
        self._corrupt = corrupt_read_back

    async def get_work_orders_strict(self, scope):
        limit = int((scope or {}).get("limit", 50))
        return self._orders[:limit]

    async def create_work_order(self, payload):
        self.created.append(payload)
        wo = {
            "id": 100 + len(self._orders),
            "order_no": payload.get("order_no"),
            "customer_order_no": payload.get("customer_order_no"),
            "planned_qty": payload.get("planned_qty"),
            "status": "PENDING",
        }
        if self._corrupt:
            wo["customer_order_no"] = "SAL-ORD-WRONG"
        self._orders.append(wo)
        return {"data": wo}

    async def get_work_order_raw(self, work_order_id):
        for wo in self._orders:
            if str(wo.get("id")) == str(work_order_id):
                return wo
        return {}


class WorkOrderDispatchTests(unittest.IsolatedAsyncioTestCase):
    """工单下达（ERP 草稿 → OpenMES）：三步审批门禁 + customer_order_no 幂等 + 回读验证。"""

    def _quotation_with_draft(self, erp_draft_id="SAL-ORD-2026-90001", qty=800):
        q = {
            "quotation_id": "QUO-WOD", "item": {"item_id": "BD-2401"}, "quantity": qty,
            "unit_price": "85", "currency": "CNY", "status": "APPROVED",
            "delivery_date": "2026-11-20", "erp_draft_id": erp_draft_id,
            "customer": {"customer_id": "长城汽车", "customer_name": "长城汽车"},
        }
        real_order.save_quotation(q)
        return q

    async def test_dispatch_requires_approval_before_writing(self):
        """审批未批准时写入被拒绝，OpenMES 零写入。"""
        self._quotation_with_draft()
        mes = _FakeMESForDispatch()
        requested = await real_order.request_work_order_dispatch("QUO-WOD", "user-a")
        self.assertTrue(requested["success"])
        self.assertFalse(requested["written"])
        self.assertEqual(requested["dispatch_plan"]["customer_order_no"], "SAL-ORD-2026-90001")
        self.assertTrue(requested["dispatch_plan"]["order_no"].startswith("WO-SO-"))

        approval_id = requested["approval"]["approval_id"]
        denied = await real_order.dispatch_work_order_to_openmes("QUO-WOD", approval_id, "user-a")
        self.assertFalse(denied["success"])
        self.assertEqual(mes.created, [])

    async def test_dispatch_creates_work_order_with_read_back_and_idempotency(self):
        """批准后创建工单并回读验证；重复下达幂等返回既有工单。"""
        self._quotation_with_draft()
        mes = _FakeMESForDispatch()
        requested = await real_order.request_work_order_dispatch("QUO-WOD", "user-a")
        approval_id = requested["approval"]["approval_id"]
        approved = real_order.approve_work_order_dispatch(approval_id, "user-a")
        self.assertTrue(approved["success"])

        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            result = await real_order.dispatch_work_order_to_openmes("QUO-WOD", approval_id, "user-a")
            self.assertTrue(result["success"])
            self.assertTrue(result["written"])
            self.assertTrue(result["read_back_verified"])
            self.assertEqual(result["work_order"]["customer_order_no"], "SAL-ORD-2026-90001")
            self.assertEqual(len(mes.created), 1)
            payload = mes.created[0]
            self.assertEqual(payload["customer_order_no"], "SAL-ORD-2026-90001")
            self.assertEqual(payload["planned_qty"], 800)
            self.assertTrue(payload["description"].startswith("工单下达（人工审批"))

            # 幂等：同 ERP 订单再次下达，返回既有工单且零新写入
            again = await real_order.dispatch_work_order_to_openmes("QUO-WOD", approval_id, "user-a")
        self.assertTrue(again["success"])
        self.assertTrue(again["idempotent"])
        self.assertFalse(again["written"])
        self.assertEqual(len(mes.created), 1)

    async def test_dispatch_fails_when_read_back_mismatch(self):
        """创建成功但回读 customer_order_no 不匹配时如实报错（不伪装成功）。"""
        self._quotation_with_draft()
        mes = _FakeMESForDispatch(corrupt_read_back=True)
        requested = await real_order.request_work_order_dispatch("QUO-WOD", "user-a")
        approval_id = requested["approval"]["approval_id"]
        real_order.approve_work_order_dispatch(approval_id, "user-a")
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            result = await real_order.dispatch_work_order_to_openmes("QUO-WOD", approval_id, "user-a")
        self.assertFalse(result["success"])
        self.assertIn("回读验证未通过", result["error"])

    async def test_dispatch_rejected_without_erp_draft(self):
        """报价没有 ERP 销售订单草稿时不允许下达工单。"""
        q = self._quotation_with_draft()
        q["erp_draft_id"] = None
        real_order.save_quotation(q)
        result = await real_order.request_work_order_dispatch("QUO-WOD", "user-a")
        self.assertFalse(result["success"])
        self.assertIn("ERP 销售订单草稿", result["error"])


if __name__ == "__main__":
    unittest.main()
