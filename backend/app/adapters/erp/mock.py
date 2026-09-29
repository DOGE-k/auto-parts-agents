"""
Mock ERP Adapter。

完整实现 ERPAdapter Protocol，基于场景 fixture 和内置模拟数据返回结果。
所有返回数据都标记为 mock，明确区分于真实 ERP 记录。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.tools.demo_rules import RULE_VERSION


class MockERPAdapter:
    """
    模拟 ERPNext 适配器。

    实现 ERPAdapter Protocol 的所有方法，数据来自：
    1. 内置的模拟主数据（客户、物料、BOM、价格等）
    2. 场景 fixture 中提供的数据
    3. 基于确定性规则生成的结果

    所有返回的记录都带有 authority="MockERP" 标记。
    """

    def __init__(self, seed: int = 20260923):
        self.seed = seed
        self._mock_customers = self._build_customers()
        self._mock_items = self._build_items()
        self._mock_prices = self._build_prices()
        self._mock_boms = self._build_boms()
        self._mock_inventory = self._build_inventory()
        self._drafts: dict[str, dict[str, Any]] = {}
        self._next_draft_seq = 1

    # ========== 基础查询方法 ==========

    async def get_customer(self, customer_id: str) -> dict[str, Any]:
        """获取客户信息。"""
        customer = self._mock_customers.get(customer_id)
        if customer is None:
            return {
                "customer_id": customer_id,
                "customer_name": f"模拟客户-{customer_id}",
                "currency": "CNY",
                "payment_terms": "Net 30",
                "authority": "MockERP",
                "found": False,
            }
        return {**customer, "authority": "MockERP", "found": True}

    async def get_item(self, item_id: str, version: str | None = None) -> dict[str, Any]:
        """获取物料/产品主数据。"""
        item = self._mock_items.get(item_id)
        if item is None:
            return {
                "item_id": item_id,
                "item_name": f"模拟物料-{item_id}",
                "item_group": "Unknown",
                "version": version or "v1",
                "authority": "MockERP",
                "found": False,
            }
        result = {**item, "authority": "MockERP", "found": True}
        if version:
            result["version"] = version
        return result

    async def get_prices(self, item_id: str, as_of: str, supplier: str | None = None) -> list[dict[str, Any]]:
        """获取物料价格列表。supplier 参数在 Mock 中按记录 supplier 字段过滤。"""
        prices = self._mock_prices.get(item_id, [])
        if supplier:
            prices = [p for p in prices if p.get("supplier") == supplier]
        return [
            {
                **p,
                "item_id": item_id,
                "as_of": as_of,
                "authority": "MockERP",
            }
            for p in prices
        ]

    async def get_bom(self, item_id: str, version: str | None = None) -> dict[str, Any]:
        """获取物料清单。"""
        bom = self._mock_boms.get(item_id)
        if bom is None:
            return {
                "item_id": item_id,
                "bom_version": version or "v1",
                "items": [],
                "authority": "MockERP",
                "found": False,
            }
        return {
            "item_id": item_id,
            "bom_version": version or bom.get("version", "v1"),
            "items": bom.get("items", []),
            "authority": "MockERP",
            "found": True,
        }

    async def get_inventory(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """获取库存列表。"""
        results = []
        for item_id in item_ids:
            inv_list = self._mock_inventory.get(item_id, [])
            for inv in inv_list:
                results.append({
                    **inv,
                    "item_id": item_id,
                    "authority": "MockERP",
                })
        return results

    async def search_suppliers(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        """搜索供应商（Mock）。"""
        all_suppliers = [
            {"supplier_id": "SUP-001", "supplier_name": "上海铸锻厂", "supplier_group": "铸造", "country": "China"},
            {"supplier_id": "SUP-002", "supplier_name": "宁波紧固件有限公司", "supplier_group": "标准件", "country": "China"},
            {"supplier_id": "SUP-003", "supplier_name": "江苏轴承制造有限公司", "supplier_group": "轴承", "country": "China"},
        ]
        filtered = [s for s in all_suppliers if not keyword or keyword in s["supplier_name"]]
        return [{**s, "authority": "MockERP", "found": True} for s in filtered[:limit]]

    async def get_supplier(self, supplier_id: str) -> dict[str, Any]:
        """获取单个供应商（Mock）。"""
        suppliers = {
            "SUP-001": {"supplier_id": "SUP-001", "supplier_name": "上海铸锻厂", "supplier_group": "铸造", "country": "China"},
            "SUP-002": {"supplier_id": "SUP-002", "supplier_name": "宁波紧固件有限公司", "supplier_group": "标准件", "country": "China"},
            "SUP-003": {"supplier_id": "SUP-003", "supplier_name": "江苏轴承制造有限公司", "supplier_group": "轴承", "country": "China"},
        }
        s = suppliers.get(supplier_id, {"supplier_id": supplier_id, "supplier_name": supplier_id, "found": False})
        return {**s, "authority": "MockERP"}

    async def get_material_demands(self, order_id: str) -> list[dict[str, Any]]:
        """获取物料需求。"""
        # 模拟：基于订单ID生成确定性的物料需求
        return [
            {
                "demand_id": f"DEM-{order_id}-001",
                "order_id": order_id,
                "material_id": "DEMO-AL-102",
                "required_quantity": "240",
                "required_date": "2026-10-15",
                "status": "PLANNED",
                "authority": "MockERP",
            }
        ]

    async def list_sales_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        """列出模拟销售订单（表头字段）。"""
        rows = self._build_sales_orders()
        return [
            {
                "order_id": row["order_id"],
                "customer_id": row["customer_id"],
                "customer_name": row["customer_id"],
                "transaction_date": row["transaction_date"],
                "delivery_date": row["delivery_date"],
                "status": row["status"],
                "currency": "CNY",
                "total": row["total"],
                "total_qty": row["total_qty"],
                "authority": "MockERP",
                "data_source": "synthetic_demo_only",
            }
            for row in rows[:limit]
        ]

    async def list_warehouses(self, company: str = "") -> list[dict[str, Any]]:
        """列出模拟仓库（fixture）。"""
        rows = [
            {"warehouse": "FG-DEMO", "warehouse_type": "Finished Goods", "company": "DEMO Manufacturing"},
            {"warehouse": "Stores-DEMO", "warehouse_type": "Stores", "company": "DEMO Manufacturing"},
        ]
        if company:
            rows = [r for r in rows if r["company"] == company]
        return [{**r, "is_group": False, "authority": "MockERP", "data_source": "synthetic_demo_only"} for r in rows]

    async def get_default_company(self) -> dict[str, Any]:
        """读取模拟公司记录。"""
        return {
            "company_id": "DEMO Manufacturing",
            "company_name": "DEMO Manufacturing",
            "abbr": "DEMO",
            "currency": "CNY",
            "authority": "MockERP",
            "data_source": "synthetic_demo_only",
            "found": True,
        }

    async def get_sales_order(self, order_id: str) -> dict[str, Any]:
        """获取模拟销售订单详情。"""
        for row in self._build_sales_orders():
            if row["order_id"] == order_id:
                return {
                    **row,
                    "currency": "CNY",
                    "authority": "MockERP",
                    "data_source": "synthetic_demo_only",
                    "found": True,
                }
        return {
            "order_id": order_id,
            "customer_id": "",
            "customer_name": "",
            "transaction_date": "",
            "delivery_date": "",
            "status": "",
            "currency": "CNY",
            "total": "0",
            "items": [],
            "authority": "MockERP",
            "data_source": "synthetic_demo_only",
            "found": False,
        }

    def _build_sales_orders(self) -> list[dict[str, Any]]:
        """模拟销售订单 fixture，customer_order_no 关联链路可在 Mock 模式下自测。"""
        return [
            {
                "order_id": "SO-DEMO-2026-001",
                "customer_id": "DEMO-CUST-001",
                "transaction_date": "2026-09-20",
                "delivery_date": "2026-10-20",
                "status": "Draft",
                "total": "45600",
                "total_qty": "120",
                "items": [
                    {
                        "item_code": "DEMO-BRACKET-001",
                        "item_name": "演示支架组件",
                        "qty": "120",
                        "rate": "380",
                        "uom": "Nos",
                        "delivery_date": "2026-10-20",
                        "warehouse": "FG-DEMO",
                    }
                ],
            }
        ]

    # ========== 草稿创建方法 ==========

    async def create_quote_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """创建报价草稿。"""
        draft_id = f"Q-MOCK-{self._next_draft_seq:04d}"
        self._next_draft_seq += 1
        record = {
            "draft_id": draft_id,
            "doctype": "Quotation",
            "docstatus": 0,  # 0=草稿
            "customer": draft.get("customer_id", ""),
            "items": draft.get("items", []),
            "total": draft.get("total_price", "0"),
            "currency": draft.get("currency", "CNY"),
            "status": "DRAFT",
            "authority": "MockERP",
            "data_source": "synthetic_demo_only",
        }
        self._drafts[draft_id] = record
        return record

    async def create_sales_order_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """创建销售订单草稿。"""
        draft_id = f"SO-MOCK-{self._next_draft_seq:04d}"
        self._next_draft_seq += 1
        record = {
            "draft_id": draft_id,
            "doctype": "Sales Order",
            "docstatus": 0,
            "customer": draft.get("customer_id", ""),
            "items": draft.get("items", []),
            "delivery_date": draft.get("delivery_date", ""),
            "total": draft.get("total", "0"),
            "currency": draft.get("currency", "CNY"),
            "status": "DRAFT",
            "authority": "MockERP",
            "data_source": "synthetic_demo_only",
        }
        self._drafts[draft_id] = record
        return record

    async def create_purchase_order_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """创建采购订单草稿。"""
        draft_id = f"PO-MOCK-{self._next_draft_seq:04d}"
        self._next_draft_seq += 1
        record = {
            "draft_id": draft_id,
            "doctype": "Purchase Order",
            "docstatus": 0,
            "supplier": draft.get("supplier_id", ""),
            "items": draft.get("items", []),
            "schedule_date": draft.get("schedule_date", ""),
            "total": draft.get("total", "0"),
            "currency": draft.get("currency", "CNY"),
            "status": "DRAFT",
            "authority": "MockERP",
            "data_source": "synthetic_demo_only",
        }
        self._drafts[draft_id] = record
        return record

    async def read_back(self, object_ref: dict[str, str]) -> dict[str, Any]:
        """回读草稿确认。"""
        obj_id = object_ref.get("id", object_ref.get("name", ""))
        doctype = object_ref.get("doctype", "")
        if obj_id in self._drafts:
            return {
                **self._drafts[obj_id],
                "read_back": True,
                "confirmed_docstatus": 0,
            }
        return {
            "id": obj_id,
            "doctype": doctype,
            "found": False,
            "authority": "MockERP",
        }

    # ========== 内置模拟数据 ==========

    def _build_customers(self) -> dict[str, dict[str, Any]]:
        return {
            "DEMO-CUSTOMER-001": {
                "customer_id": "DEMO-CUSTOMER-001",
                "customer_name": "演示汽车零部件有限公司",
                "customer_group": "Automotive",
                "territory": "China",
                "currency": "CNY",
                "payment_terms": "Net 30",
                "credit_limit": "1000000.00",
            },
            "DEMO-CUSTOMER-002": {
                "customer_id": "DEMO-CUSTOMER-002",
                "customer_name": "比亚迪汽车演示客户",
                "customer_group": "Automotive",
                "territory": "China",
                "currency": "CNY",
                "payment_terms": "Net 45",
                "credit_limit": "5000000.00",
            },
        }

    def _build_items(self) -> dict[str, dict[str, Any]]:
        return {
            "DEMO-BRACKET-001": {
                "item_id": "DEMO-BRACKET-001",
                "item_name": "演示支架组件",
                "item_group": "Finished Goods",
                "item_code": "DEMO-BRACKET-001",
                "description": "汽车座椅调节支架总成（演示用）",
                "uom": "Piece",
                "is_stock_item": True,
                "default_warehouse": "Finished Goods - M",
            },
            "DEMO-AL-102": {
                "item_id": "DEMO-AL-102",
                "item_name": "铝合金型材 6061-T6",
                "item_group": "Raw Material",
                "item_code": "DEMO-AL-102",
                "description": "6061-T6 铝合金挤压型材（演示用）",
                "uom": "Kg",
                "is_stock_item": True,
                "default_warehouse": "Raw Material - M",
            },
            "DEMO-BOLT-008": {
                "item_id": "DEMO-BOLT-008",
                "item_name": "高强度螺栓 M8x25",
                "item_group": "Raw Material",
                "item_code": "DEMO-BOLT-008",
                "description": "8.8级高强度螺栓（演示用）",
                "uom": "Piece",
                "is_stock_item": True,
                "default_warehouse": "Raw Material - M",
            },
            "MAT-STEEL-20CrMnTi": {
                "item_id": "MAT-STEEL-20CrMnTi",
                "item_name": "20CrMnTi 齿轮钢",
                "item_group": "Raw Material",
                "item_code": "MAT-STEEL-20CrMnTi",
                "description": "渗碳齿轮钢棒料（演示用）",
                "uom": "Kg",
                "is_stock_item": True,
                "default_warehouse": "Raw Material - M",
            },
        }

    def _build_prices(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "DEMO-AL-102": [
                {
                    "price_list": "Standard Buying",
                    "rate": "12.50",
                    "currency": "CNY",
                    "uom": "Kg",
                    "supplier": "SUP-ALUMINUM-001",
                },
                {
                    "price_list": "Standard Buying",
                    "rate": "12.80",
                    "currency": "CNY",
                    "uom": "Kg",
                    "supplier": "SUP-ALUMINUM-002",
                },
            ],
            "DEMO-BOLT-008": [
                {
                    "price_list": "Standard Buying",
                    "rate": "2.00",
                    "currency": "CNY",
                    "uom": "Piece",
                    "supplier": "SUP-FASTENER-001",
                },
            ],
            "MAT-STEEL-20CrMnTi": [
                {
                    "price_list": "Standard Buying",
                    "rate": "18.50",
                    "currency": "CNY",
                    "uom": "Kg",
                    "supplier": "SUP-BAOSTEEL",
                },
            ],
        }

    def _build_boms(self) -> dict[str, dict[str, Any]]:
        return {
            "DEMO-BRACKET-001": {
                "version": "v1",
                "items": [
                    {
                        "item_code": "DEMO-AL-102",
                        "qty": 2,
                        "uom": "Kg",
                        "operation": "Machining",
                    },
                    {
                        "item_code": "DEMO-BOLT-008",
                        "qty": 4,
                        "uom": "Piece",
                        "operation": "Assembly",
                    },
                ],
            },
        }

    def _build_inventory(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "DEMO-AL-102": [
                {
                    "warehouse": "Raw Material - M",
                    "quantity": "300",
                    "actual_qty": "300",
                    "reserved_qty": "0",
                    "status": "available",
                    "batch_no": "BATCH-AL-2026-001",
                },
                {
                    "warehouse": "Quality Inspection - M",
                    "quantity": "50",
                    "actual_qty": "50",
                    "reserved_qty": "0",
                    "status": "inspection",
                    "batch_no": "BATCH-AL-2026-002",
                },
            ],
            "DEMO-BOLT-008": [
                {
                    "warehouse": "Raw Material - M",
                    "quantity": "1000",
                    "actual_qty": "1000",
                    "reserved_qty": "0",
                    "status": "available",
                    "batch_no": "BATCH-BOLT-2026-001",
                },
            ],
            "MAT-STEEL-20CrMnTi": [
                {
                    "warehouse": "Raw Material - M",
                    "quantity": "1200",
                    "actual_qty": "1200",
                    "reserved_qty": "0",
                    "status": "available",
                    "batch_no": "BATCH-STEEL-2026-001",
                },
                {
                    "warehouse": "Quality Inspection - M",
                    "quantity": "300",
                    "actual_qty": "300",
                    "reserved_qty": "0",
                    "status": "inspection",
                    "batch_no": "BATCH-STEEL-2026-002",
                },
            ],
        }

    # ========== 兼容旧接口（scenarios 中直接使用的方法） ==========

    def sales_order_release(self, order_id: str, quantity: int) -> dict[str, Any]:
        """兼容旧接口：销售订单发布。"""
        return {
            "object_id": order_id,
            "status": "RELEASED",
            "quantity": quantity,
            "authority": "MockERP",
        }

    def material_demand(self, demand_id: str, material_id: str, quantity: str) -> dict[str, Any]:
        """兼容旧接口：物料需求。"""
        return {
            "object_id": demand_id,
            "material_id": material_id,
            "required_quantity": quantity,
            "authority": "MockERP",
        }

    def purchase_order_draft(self, order_id: str, selected_option: dict[str, Any]) -> dict[str, Any]:
        """兼容旧接口：采购订单草稿。"""
        return {
            "object_id": order_id,
            "status": "DRAFT",
            "selected_option": selected_option,
            "authority": "MockERP",
        }

    def delivered(self, delivery_id: str, shipment_event_id: str) -> dict[str, Any]:
        """兼容旧接口：发货完成。"""
        return {
            "object_id": delivery_id,
            "status": "DELIVERED",
            "source_shipment_event": shipment_event_id,
            "authority": "MockERP",
        }


# 全局单例（兼容旧代码）
_mock_erp_adapter: MockERPAdapter | None = None


def get_mock_erp() -> MockERPAdapter:
    """获取全局 Mock ERP 实例。"""
    global _mock_erp_adapter
    if _mock_erp_adapter is None:
        _mock_erp_adapter = MockERPAdapter()
    return _mock_erp_adapter
