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

    def __init__(self, corrupt_read_back: bool = False, product_types: list | None = None):
        self.created: list[dict] = []
        self._orders: list[dict] = []
        self._corrupt = corrupt_read_back
        self._product_types = product_types or []

    async def get_work_orders_strict(self, scope):
        limit = int((scope or {}).get("limit", 50))
        return self._orders[:limit]

    async def list_product_types(self, query: str = ""):
        return list(self._product_types)

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

    async def test_dispatch_resolves_product_type_by_item_code(self):
        """下达计划按 item_code 精确匹配 OpenMES 产品类型，命中时创建带 product_type_id（报工前置）。"""
        self._quotation_with_draft()
        mes = _FakeMESForDispatch(product_types=[{"id": 2, "code": "BD-2401", "name": "制动盘-前轮"}])
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            requested = await real_order.request_work_order_dispatch("QUO-WOD", "user-a")
        self.assertTrue(requested["success"])
        plan = requested["dispatch_plan"]
        self.assertEqual(plan["product_type_id"], 2)
        self.assertEqual(plan["product_type_match"], "resolved")
        approval_id = requested["approval"]["approval_id"]
        real_order.approve_work_order_dispatch(approval_id, "user-a")
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            result = await real_order.dispatch_work_order_to_openmes("QUO-WOD", approval_id, "user-a")
        self.assertTrue(result["success"])
        self.assertEqual(mes.created[0].get("product_type_id"), 2)

    async def test_dispatch_records_missing_product_type_honestly(self):
        """产品类型未命中时如实记录 not_found，不阻断下达（工单仍可创建）。"""
        self._quotation_with_draft()
        mes = _FakeMESForDispatch(product_types=[{"id": 5, "code": "TS-4501", "name": "传动轴"}])
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            requested = await real_order.request_work_order_dispatch("QUO-WOD", "user-a")
        self.assertTrue(requested["success"])
        plan = requested["dispatch_plan"]
        self.assertIsNone(plan["product_type_id"])
        self.assertEqual(plan["product_type_match"], "not_found")
        self.assertIn("不可报工", plan["product_type_note"])


class _FakeMESForReport:
    """真实报工测试用假 OpenMES 适配器（模拟官方报工链路，可注入无快照/既有批次）。"""

    authority = "OpenMES"

    def __init__(self, planned_qty="800.00", with_snapshot=True, existing_batches=None):
        self.planned_qty = planned_qty
        self.with_snapshot = with_snapshot
        self._batches = [dict(b) for b in (existing_batches or [])]
        self._next_id = 50
        self.created_batches: list[dict] = []
        self.started_steps: list[str] = []
        self.completed_steps: list[dict] = []
        self.produced_qty = sum(float(b.get("produced_qty") or 0) for b in self._batches)

    async def get_work_order_raw(self, work_order_id):
        snapshot = (
            {"steps": [{"step_number": 1, "name": "TEST_Final_Assembly"}]}
            if self.with_snapshot
            else None
        )
        return {
            "id": int(work_order_id),
            "order_no": "WO-SO-2026-90002",
            "customer_order_no": "SAL-ORD-2026-90002",
            "planned_qty": self.planned_qty,
            "produced_qty": str(self.produced_qty),
            "process_snapshot": snapshot,
        }

    async def get_work_order_batches(self, work_order_id):
        return [dict(b) for b in self._batches]

    async def create_batch(self, work_order_id, payload):
        self.created_batches.append(dict(payload))
        self._next_id += 1
        batch_id = self._next_id
        batch = {
            "batch_id": str(batch_id),
            "lot_number": payload.get("lot_number", ""),
            "target_qty": str(payload.get("target_qty", "")),
            "produced_qty": "0",
            "status": "PENDING",
            "steps": [{
                "step_id": f"step-{batch_id}", "step_number": 1, "name": "TEST_Final_Assembly",
                "status": "PENDING", "passed_qty": "0", "started_at": "", "completed_at": "",
                "actual_elapsed_minutes": None, "actual_run_minutes": None,
            }],
        }
        self._batches.append(batch)
        return {"data": {"id": batch_id, "target_qty": payload.get("target_qty"),
                         "steps": [{"id": f"step-{batch_id}", "status": "PENDING"}]}}

    async def start_batch_step(self, batch_step_id):
        self.started_steps.append(str(batch_step_id))
        return {"data": {"id": batch_step_id, "status": "IN_PROGRESS"}}

    async def complete_batch_step(self, batch_step_id, payload):
        self.completed_steps.append({"step_id": str(batch_step_id), **payload})
        for b in self._batches:
            if any(s.get("step_id") == str(batch_step_id) for s in b.get("steps", [])):
                for s in b["steps"]:
                    if s.get("step_id") == str(batch_step_id):
                        s["status"] = "DONE"
                        s["actual_elapsed_minutes"] = payload.get("actual_elapsed_minutes")
                        s["passed_qty"] = str(payload.get("produced_qty", ""))
                b["status"] = "DONE"
                b["produced_qty"] = str(payload.get("produced_qty", b.get("produced_qty")))
                self.produced_qty += float(payload.get("produced_qty") or 0)
        return {"data": {"id": batch_step_id, "status": "DONE"}}


class ProductionReportTests(unittest.IsolatedAsyncioTestCase):
    """真实报工（OpenMES 官方报工链路）：三步审批门禁 + lot_number 幂等 + 回读验证。"""

    async def _request(self, mes, *args, **kwargs):
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            return await real_order.request_production_report(*args, **kwargs)

    async def _execute(self, mes, wo_id, approval_id):
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            return await real_order.execute_production_report(wo_id, approval_id, "user-a")

    async def test_report_requires_approval_before_writing(self):
        """审批未批准时拒绝执行，OpenMES 零写入。"""
        mes = _FakeMESForReport()
        requested = await self._request(mes, "11", 500, 90, "user-a", lot_number="TEST_LOT_RPT_1")
        self.assertTrue(requested["success"])
        self.assertFalse(requested["written"])
        plan = requested["report_plan"]
        self.assertEqual(plan["lot_number"], "TEST_LOT_RPT_1")
        self.assertEqual(plan["batch_target_qty"], "500")
        self.assertEqual(plan["actual_elapsed_minutes"], 90)

        approval_id = requested["approval"]["approval_id"]
        denied = await self._execute(mes, "11", approval_id)
        self.assertFalse(denied["success"])
        self.assertEqual(mes.created_batches, [])

    async def test_report_executes_official_chain_with_read_back(self):
        """批准后按 建批次→开工→完工 执行并回读验证；工单 produced_qty 增加。"""
        mes = _FakeMESForReport()
        requested = await self._request(mes, "11", 500, 90, "user-a", lot_number="TEST_LOT_RPT_1")
        approval_id = requested["approval"]["approval_id"]
        approved = real_order.approve_production_report(approval_id, "user-a")
        self.assertTrue(approved["success"])

        result = await self._execute(mes, "11", approval_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["written"])
        self.assertTrue(result["read_back_verified"])
        # 官方链路三步按序执行，完工载荷带数量与整数耗时
        self.assertEqual(len(mes.created_batches), 1)
        self.assertEqual(len(mes.started_steps), 1)
        self.assertEqual(len(mes.completed_steps), 1)
        payload = mes.completed_steps[0]
        self.assertEqual(payload["produced_qty"], "500")
        self.assertEqual(payload["actual_elapsed_minutes"], 90)
        self.assertEqual(result["batch"]["produced_qty"], "500")
        self.assertEqual(result["work_order_produced_qty"], "500.0")

    async def test_report_idempotent_on_same_lot(self):
        """同工单同批次号重复报工幂等返回既有批次，零新写入。"""
        mes = _FakeMESForReport()
        requested = await self._request(mes, "11", 500, 90, "user-a", lot_number="TEST_LOT_RPT_1")
        approval_id = requested["approval"]["approval_id"]
        real_order.approve_production_report(approval_id, "user-a")
        first = await self._execute(mes, "11", approval_id)
        again = await self._execute(mes, "11", approval_id)
        self.assertTrue(first["success"])
        self.assertTrue(again["success"])
        self.assertTrue(again["idempotent"])
        self.assertFalse(again["written"])
        self.assertEqual(len(mes.created_batches), 1)

    async def test_report_rejected_without_snapshot_steps(self):
        """无工艺快照步骤的工单写前如实拒绝（不做伪造报工、不产生孤儿批次）。"""
        mes = _FakeMESForReport(with_snapshot=False)
        result = await self._request(mes, "11", 500, 90, "user-a", lot_number="TEST_LOT_RPT_2")
        self.assertFalse(result["success"])
        self.assertIn("工艺快照步骤", result["error"])
        self.assertEqual(mes.created_batches, [])

    async def test_report_rejected_when_batch_exceeds_planned(self):
        """既有批次合计 + 本次数量超过 planned_qty 时写前拦截。"""
        existing = [{
            "batch_id": "3", "lot_number": "TEST_LOT_PAGE_9", "target_qty": "600",
            "produced_qty": "600", "status": "DONE", "steps": [],
        }]
        mes = _FakeMESForReport(planned_qty="800.00", existing_batches=existing)
        result = await self._request(mes, "11", 300, 60, "user-a", lot_number="TEST_LOT_RPT_3")
        self.assertFalse(result["success"])
        self.assertIn("超过", result["error"])
        self.assertEqual(mes.created_batches, [])

    async def test_report_rejects_setup_run_over_elapsed(self):
        """setup + run 超过 elapsed 时写前拦截（OpenMES 契约校验前置）。"""
        result = await real_order.request_production_report(
            "11", 500, 60, "user-a", lot_number="TEST_LOT_RPT_4",
            actual_setup_minutes=30, actual_run_minutes=50,
        )
        self.assertFalse(result["success"])
        self.assertIn("不能超过", result["error"])

    async def test_report_lot_auto_generated_from_approval_when_missing(self):
        """未提供批次号时自动派生（幂等键仍确定）。"""
        mes = _FakeMESForReport()
        requested = await self._request(mes, "11", 100, 30, "user-a")
        self.assertTrue(requested["success"])
        self.assertTrue(requested["report_plan"]["lot_number"].startswith("LOT-"))


class _FakeMESForIssue:
    """质量问题登记测试用假 OpenMES 适配器（可注入既有未关闭问题做幂等）。"""

    authority = "OpenMES"

    def __init__(self, existing_open=None):
        self._types = [
            {"id": 4, "name": "Quality Issue", "severity": "MEDIUM"},
            {"id": 1, "name": "Material Defect", "severity": "HIGH"},
        ]
        self._issues = [dict(i) for i in (existing_open or [])]
        self.created: list[dict] = []
        self._next_id = 90

    async def get_work_order_raw(self, work_order_id):
        if str(work_order_id) == "404":
            return {}
        return {"id": int(work_order_id), "order_no": "WO-SO-2026-90003", "planned_qty": "500"}

    async def list_issue_types(self):
        return list(self._types)

    async def list_open_issues(self, statuses=("OPEN", "ACKNOWLEDGED")):
        wanted = set(statuses or ())
        return [
            {
                "issue_id": str(raw["id"]),
                "work_order_id": str(raw.get("work_order_id", "")),
                "title": str(raw.get("title", "")),
                "status": raw.get("status"),
            }
            for raw in self._issues
            if raw.get("status") in wanted
        ]

    async def create_issue(self, payload):
        self.created.append(dict(payload))
        self._next_id += 1
        issue = {
            "id": self._next_id,
            "work_order_id": payload.get("work_order_id"),
            "issue_type_id": payload.get("issue_type_id"),
            "title": payload.get("title"),
            "description": payload.get("description", ""),
            "status": "OPEN",
        }
        self._issues.append(issue)
        return {"data": issue}

    async def get_issue_raw(self, issue_id):
        for raw in self._issues:
            if str(raw.get("id")) == str(issue_id):
                return raw
        return {}


class IssueRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """质量问题登记（OpenMES NCR）：三步审批门禁 + work_order_id+title 幂等 + 回读验证。"""

    async def _request(self, mes, *args, **kwargs):
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            return await real_order.request_issue_registration(*args, **kwargs)

    async def _execute(self, mes, wo_id, approval_id):
        with patch.object(real_order, "get_mes_adapter", return_value=mes):
            return await real_order.execute_issue_registration(wo_id, approval_id, "user-a")

    async def test_issue_requires_approval_before_writing(self):
        """审批未批准时拒绝执行，OpenMES 零写入。"""
        mes = _FakeMESForIssue()
        requested = await self._request(mes, "13", 4, "制动盘外径超差", "user-a", description="直径超差 0.05mm")
        self.assertTrue(requested["success"])
        self.assertFalse(requested["written"])
        plan = requested["issue_plan"]
        self.assertEqual(plan["work_order_no"], "WO-SO-2026-90003")
        self.assertEqual(plan["issue_type_name"], "Quality Issue")
        self.assertEqual(plan["severity"], "MEDIUM")

        approval_id = requested["approval"]["approval_id"]
        denied = await self._execute(mes, "13", approval_id)
        self.assertFalse(denied["success"])
        self.assertEqual(mes.created, [])

    async def test_issue_creates_with_read_back(self):
        """批准后创建质量问题并回读验证 work_order_id 与标题。"""
        mes = _FakeMESForIssue()
        requested = await self._request(mes, "13", 4, "制动盘外径超差", "user-a")
        approval_id = requested["approval"]["approval_id"]
        approved = real_order.approve_issue_registration(approval_id, "user-a")
        self.assertTrue(approved["success"])

        result = await self._execute(mes, "13", approval_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["written"])
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(len(mes.created), 1)
        payload = mes.created[0]
        self.assertEqual(payload["work_order_id"], 13)
        self.assertEqual(payload["issue_type_id"], 4)
        self.assertEqual(payload["title"], "制动盘外径超差")
        self.assertEqual(result["issue_id"], "91")

    async def test_issue_idempotent_same_work_order_and_title(self):
        """同工单同标题的未关闭问题已存在时幂等返回既有，零新写入。"""
        existing = [{
            "id": 77, "work_order_id": 13, "title": "制动盘外径超差",
            "status": "OPEN",
        }]
        mes = _FakeMESForIssue(existing_open=existing)
        requested = await self._request(mes, "13", 4, "制动盘外径超差", "user-a")
        approval_id = requested["approval"]["approval_id"]
        real_order.approve_issue_registration(approval_id, "user-a")
        result = await self._execute(mes, "13", approval_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["idempotent"])
        self.assertFalse(result["written"])
        self.assertEqual(result["issue_id"], "77")
        self.assertEqual(mes.created, [])

    async def test_issue_rejected_when_work_order_missing(self):
        """工单不存在时写前如实拒绝。"""
        mes = _FakeMESForIssue()
        result = await self._request(mes, "404", 4, "任意标题", "user-a")
        self.assertFalse(result["success"])
        self.assertIn("不存在", result["error"])
        self.assertEqual(mes.created, [])

    async def test_issue_rejected_with_unknown_issue_type(self):
        """issue_type_id 不在真实类型列表时写前拦截（避免 422 试错）。"""
        mes = _FakeMESForIssue()
        result = await self._request(mes, "13", 999, "任意标题", "user-a")
        self.assertFalse(result["success"])
        self.assertIn("issue_type_id=999", result["error"])
        self.assertEqual(mes.created, [])

    async def test_issue_rejected_with_empty_title(self):
        """缺少标题时拒绝建立审批。"""
        result = await real_order.request_issue_registration("13", 4, "  ", "user-a")
        self.assertFalse(result["success"])
        self.assertIn("title", result["error"])


if __name__ == "__main__":
    unittest.main()
