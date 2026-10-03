"""
ERPNext 真实适配器。

实现 ERPAdapter Protocol，基于 ERPNext REST API 读取业务数据。
所有返回的记录都带有 authority="ERPNext" 标记。
写入操作受 ERPNEXT_DRAFT_WRITES_ENABLED 开关控制，默认为只读模式。
"""
from __future__ import annotations

import logging
from typing import Any

from app.adapters.erp.base import ERPAdapter
from app.adapters.erp.erpnext import ERPNextClient
from app.integrations.errors import IntegrationError, IntegrationNotConfigured

logger = logging.getLogger(__name__)


class ERPNextAdapter:
    """
    ERPNext 真实数据适配器。

    实现 ERPAdapter Protocol，通过 ERPNextClient 调用 Frappe REST API。
    - 所有查询操作：直接读取 ERPNext 数据
    - 草稿创建操作：受 ERPNEXT_DRAFT_WRITES_ENABLED 开关控制
    - 未配置时：抛出 IntegrationNotConfigured，由上层回退到 Mock
    """

    def __init__(self, client: ERPNextClient) -> None:
        self._client = client
        self.authority = "ERPNext"

    # ========== 基础查询方法 ==========

    async def get_customer(self, customer_id: str) -> dict[str, Any]:
        """获取客户信息。"""
        try:
            doc = await self._client.get_document("Customer", customer_id)
            return {
                "customer_id": doc.get("name", customer_id),
                "customer_name": doc.get("customer_name", doc.get("name", "")),
                "customer_group": doc.get("customer_group", ""),
                "territory": doc.get("territory", ""),
                "currency": doc.get("default_currency", "CNY"),
                "payment_terms": doc.get("payment_terms", ""),
                "authority": self.authority,
                "found": True,
            }
        except Exception:
            return {
                "customer_id": customer_id,
                "customer_name": customer_id,
                "currency": "CNY",
                "payment_terms": "",
                "authority": self.authority,
                "found": False,
            }

    async def get_item(self, item_id: str, version: str | None = None) -> dict[str, Any]:
        """获取物料/产品主数据。"""
        try:
            doc = await self._client.get_document("Item", item_id)
            return {
                "item_id": doc.get("name", item_id),
                "item_name": doc.get("item_name", doc.get("name", "")),
                "item_group": doc.get("item_group", ""),
                "description": doc.get("description", ""),
                "stock_uom": doc.get("stock_uom", "Nos"),
                # 采购业务字段（真实值；0/None 表示 ERP 未配置，不得当作业务事实）
                "min_order_qty": str(doc.get("min_order_qty", "0") or "0"),
                "lead_time_days": int(doc.get("lead_time_days", 0) or 0),
                "purchase_uom": doc.get("purchase_uom") or "",
                "default_warehouse": doc.get("default_warehouse") or "",
                # 物料-供应商关系（Item Supplier 子表真实记录）
                "suppliers": [
                    {"supplier": r.get("supplier", ""), "supplier_name": r.get("supplier_name", r.get("supplier", ""))}
                    for r in (doc.get("supplier_items") or [])
                    if r.get("supplier")
                ],
                "is_stock_item": doc.get("is_stock_item", True),
                "version": version or "v1",
                "authority": self.authority,
                "found": True,
            }
        except Exception:
            return {
                "item_id": item_id,
                "item_name": item_id,
                "item_group": "Unknown",
                "version": version or "v1",
                "authority": self.authority,
                "found": False,
            }

    async def get_prices(self, item_code: str, as_of: str, supplier: str | None = None) -> list[dict[str, Any]]:
        """获取物料价格列表（Item Price）。

        supplier 参数用于查询供应商特定价格（Item Price.supplier）；
        不传时返回该物料全部价格记录。
        """
        try:
            filters: list[list[Any]] = [["item_code", "=", item_code]]
            if supplier:
                filters.append(["supplier", "=", supplier])
            rows = await self._client.list_documents(
                "Item Price",
                fields=["name", "item_code", "price_list", "price_list_rate", "currency", "valid_from", "valid_upto", "supplier"],
                filters=filters,
                limit=20,
            )
            return [
                {
                    "price_id": row.get("name", ""),
                    "item_id": row.get("item_code", item_code),
                    "price_list": row.get("price_list", ""),
                    "unit_price": str(row.get("price_list_rate", "0")),
                    "currency": row.get("currency", "CNY"),
                    "valid_from": row.get("valid_from", ""),
                    "valid_upto": row.get("valid_upto", ""),
                    "supplier": row.get("supplier") or "",
                    "as_of": as_of,
                    "authority": self.authority,
                }
                for row in rows
            ]
        except Exception:
            return []

    async def get_bom(self, item_id: str, version: str | None = None) -> dict[str, Any]:
        """获取物料清单（BOM）。"""
        try:
            rows = await self._client.list_documents(
                "BOM",
                fields=["name", "item", "quantity", "is_active"],
                filters=[["item", "=", item_id], ["is_active", "=", 1]],
                limit=10,
            )
            if not rows:
                return {
                    "item_id": item_id,
                    "bom_version": version or "v1",
                    "items": [],
                    "authority": self.authority,
                    "found": False,
                }

            bom_name = rows[0]["name"]
            bom_doc = await self._client.get_document("BOM", bom_name)

            items = []
            for row in bom_doc.get("items", []):
                items.append({
                    "item_code": row.get("item_code", ""),
                    "item_name": row.get("item_name", ""),
                    "qty_per_product": str(row.get("qty", "0")),
                    "uom": row.get("uom", ""),
                    "source_warehouse": row.get("source_warehouse", ""),
                })

            return {
                "item_id": item_id,
                "bom_id": bom_name,
                "bom_version": version or bom_doc.get("version", "v1"),
                "quantity": str(bom_doc.get("quantity", 1)),
                "items": items,
                "authority": self.authority,
                "found": True,
            }
        except Exception as e:
            logger.error("ERPNext get_bom failed (item=%s): %s", item_id, e)
            return {
                "item_id": item_id,
                "bom_version": version or "v1",
                "items": [],
                "authority": self.authority,
                "found": False,
                "error": str(e),
            }

    async def get_inventory(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """获取库存列表（Bin / Stock Ledger）。"""
        results = []
        try:
            for item_id in item_ids:
                rows = await self._client.list_documents(
                    "Bin",
                    fields=["name", "item_code", "warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"],
                    filters=[["item_code", "=", item_id]],
                    limit=20,
                )
                for row in rows:
                    results.append({
                        "item_id": row.get("item_code", item_id),
                        "warehouse": row.get("warehouse", ""),
                        "actual_qty": str(row.get("actual_qty", "0")),
                        "reserved_qty": str(row.get("reserved_qty", "0")),
                        "ordered_qty": str(row.get("ordered_qty", "0")),
                        "projected_qty": str(row.get("projected_qty", "0")),
                        "authority": self.authority,
                    })
        except Exception:
            pass
        return results

    async def list_inventory_overview(self, limit: int = 50) -> list[dict[str, Any]]:
        """全仓库存总览：直接列出 Bin 文档（不按物料过滤）。"""
        rows = await self._client.list_documents(
            "Bin",
            fields=["name", "item_code", "warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"],
            filters=[],
            limit=max(1, min(int(limit), 200)),
        )
        return [
            {
                "item_id": row.get("item_code", ""),
                "warehouse": row.get("warehouse", ""),
                "actual_qty": str(row.get("actual_qty", "0")),
                "reserved_qty": str(row.get("reserved_qty", "0")),
                "ordered_qty": str(row.get("ordered_qty", "0")),
                "projected_qty": str(row.get("projected_qty", "0")),
                "authority": self.authority,
            }
            for row in rows
        ]

    async def search_suppliers(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        """搜索供应商。"""
        try:
            filters = []
            if keyword:
                filters.append(["supplier_name", "like", f"%{keyword}%"])
            rows = await self._client.list_documents(
                "Supplier",
                fields=["name", "supplier_name", "supplier_group", "country", "disabled"],
                filters=filters,
                limit=limit,
            )
            return [self._map_supplier(row) for row in rows]
        except Exception:
            return []

    async def get_supplier(self, supplier_id: str) -> dict[str, Any]:
        """获取单个供应商详情。"""
        try:
            row = await self._client.get_document("Supplier", supplier_id)
            return self._map_supplier(row)
        except Exception:
            return {
                "supplier_id": supplier_id,
                "supplier_name": supplier_id,
                "authority": self.authority,
                "found": False,
            }

    def _map_supplier(self, row: dict[str, Any]) -> dict[str, Any]:
        """映射 ERPNext Supplier 到内部模型。"""
        return {
            "supplier_id": row.get("name", ""),
            "supplier_name": row.get("supplier_name", ""),
            "supplier_group": row.get("supplier_group", ""),
            "country": row.get("country", ""),
            "disabled": row.get("disabled", 0),
            "authority": self.authority,
            "found": True,
        }

    async def get_material_demands(self, order_id: str) -> list[dict[str, Any]]:
        """获取物料需求（Material Request）。"""
        try:
            rows = await self._client.list_documents(
                "Material Request",
                fields=["name", "material_request_type", "status", "schedule_date"],
                filters=[["sales_order", "=", order_id]],
                limit=20,
            )
            demands = []
            for row in rows:
                mr_doc = await self._client.get_document("Material Request", row["name"])
                for item in mr_doc.get("items", []):
                    demands.append({
                        "demand_id": f"{row['name']}-{item.get('name', '0')}",
                        "order_id": order_id,
                        "material_id": item.get("item_code", ""),
                        "required_quantity": str(item.get("qty", "0")),
                        "required_date": item.get("schedule_date", row.get("schedule_date", "")),
                        "status": row.get("status", "Draft"),
                        "authority": self.authority,
                    })
            return demands
        except Exception:
            return []

    async def list_sales_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        """列出销售订单（表头字段）。连接失败时抛出异常，不静默返回空。"""
        rows = await self._client.list_documents(
            "Sales Order",
            fields=["name", "customer", "customer_name", "transaction_date",
                    "delivery_date", "status", "currency", "total", "total_qty"],
            limit=limit,
        )
        return [
            {
                "order_id": row.get("name", ""),
                "customer_id": row.get("customer", ""),
                "customer_name": row.get("customer_name", ""),
                "transaction_date": row.get("transaction_date", ""),
                "delivery_date": row.get("delivery_date", ""),
                "status": row.get("status", ""),
                "currency": row.get("currency", ""),
                "total": str(row.get("total", "0")),
                "total_qty": str(row.get("total_qty", "0")),
                "authority": self.authority,
                "data_source": "erpnext_api",
            }
            for row in rows
        ]

    async def list_warehouses(self, company: str = "") -> list[dict[str, Any]]:
        """列出真实仓库记录（连接失败时抛出异常）。"""
        filters = [["company", "=", company]] if company else []
        rows = await self._client.list_documents(
            "Warehouse",
            fields=["name", "warehouse_type", "company", "is_group"],
            filters=filters,
            limit=50,
        )
        return [
            {
                "warehouse": row.get("name", ""),
                "warehouse_type": row.get("warehouse_type") or "",
                "company": row.get("company", ""),
                "is_group": bool(row.get("is_group", 0)),
                "authority": self.authority,
                "data_source": "erpnext_api",
            }
            for row in rows
            if not row.get("is_group", 0)
        ]

    async def get_default_company(self) -> dict[str, Any]:
        """读取真实 Company 记录（取第一家公司，随 ERP 数据而非硬编码）。"""
        rows = await self._client.list_documents(
            "Company",
            fields=["name", "company_name", "abbr", "default_currency", "country"],
            limit=5,
        )
        if not rows:
            return {"company_id": "", "company_name": "", "abbr": "", "currency": "", "authority": self.authority, "found": False}
        row = rows[0]
        return {
            "company_id": row.get("name", ""),
            "company_name": row.get("company_name", row.get("name", "")),
            "abbr": row.get("abbr", ""),
            "currency": row.get("default_currency", ""),
            "authority": self.authority,
            "data_source": "erpnext_api",
            "found": True,
        }

    async def get_sales_order(self, order_id: str) -> dict[str, Any]:
        """获取销售订单详情（含 items 子表）。

        仅当 ERP 返回 404（单据不存在）时返回 found=False；
        连接失败等其他异常直接抛出，由上层明确报错。
        """
        try:
            doc = await self._client.get_document("Sales Order", order_id)
        except IntegrationError as e:
            if e.status_code == 404:
                return {
                    "order_id": order_id,
                    "customer_id": "",
                    "customer_name": "",
                    "transaction_date": "",
                    "delivery_date": "",
                    "status": "",
                    "currency": "",
                    "total": "0",
                    "items": [],
                    "authority": self.authority,
                    "data_source": "erpnext_api",
                    "found": False,
                }
            raise
        items = []
        for it in doc.get("items", []) or []:
            items.append({
                "item_code": it.get("item_code", ""),
                "item_name": it.get("item_name", ""),
                "qty": str(it.get("qty", "0")),
                "rate": str(it.get("rate", "0")),
                "uom": it.get("uom", ""),
                "delivery_date": it.get("delivery_date", doc.get("delivery_date", "")),
                "warehouse": it.get("warehouse", ""),
            })
        return {
            "order_id": doc.get("name", order_id),
            "customer_id": doc.get("customer", ""),
            "customer_name": doc.get("customer_name", ""),
            "transaction_date": doc.get("transaction_date", ""),
            "delivery_date": doc.get("delivery_date", ""),
            "status": doc.get("status", ""),
            "currency": doc.get("currency", ""),
            "total": str(doc.get("total", "0")),
            "items": items,
            "authority": self.authority,
            "data_source": "erpnext_api",
            "found": True,
        }

    # ========== 草稿创建方法（需启用写入开关） ==========

    async def create_quote_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """创建报价草稿（Quotation）。"""
        if not self._client.draft_writes_enabled:
            return {
                "draft_id": "",
                "doctype": "Quotation",
                "docstatus": -1,
                "status": "WRITE_DISABLED",
                "authority": self.authority,
                "error": "ERPNext 草稿写入未启用",
            }

        items = draft.get("items", [])
        payload = {
            "customer": draft.get("customer_id", ""),
            "company": draft.get("company", "AutoParts Manufacturing"),
            "currency": draft.get("currency", "CNY"),
            "selling_price_list": draft.get("price_list", "Standard Selling"),
            "items": [
                {
                    "item_code": item.get("item_code", ""),
                    "qty": float(item.get("qty", 1)),
                    "rate": float(item.get("rate", 0)),
                    "uom": item.get("uom", "Nos"),
                }
                for item in items
            ],
        }
        try:
            result = await self._client.create_draft(
                "Quotation",
                payload,
                approval_id=draft.get("approval_id", "unknown"),
                approved_by=draft.get("approved_by", "unknown"),
            )
            data = result.get("data", {})
            return {
                "draft_id": data.get("name", ""),
                "doctype": "Quotation",
                "docstatus": data.get("docstatus", 0),
                "customer": data.get("customer", ""),
                "total": str(data.get("total", "0")),
                "currency": data.get("currency", "CNY"),
                "status": "DRAFT",
                "authority": self.authority,
                "approval_id": result.get("approval_id", ""),
            }
        except Exception as e:
            return {
                "draft_id": "",
                "doctype": "Quotation",
                "docstatus": -1,
                "status": "ERROR",
                "authority": self.authority,
                "error": str(e),
            }

    async def create_sales_order_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """创建销售订单草稿（Sales Order）。"""
        if not self._client.draft_writes_enabled:
            return {
                "draft_id": "",
                "doctype": "Sales Order",
                "docstatus": -1,
                "status": "WRITE_DISABLED",
                "authority": self.authority,
                "error": "ERPNext 草稿写入未启用",
            }

        items = draft.get("items", [])
        payload = {
            "customer": draft.get("customer_id", ""),
            "company": draft.get("company", "AutoParts Manufacturing"),
            "currency": draft.get("currency", "CNY"),
            "delivery_date": draft.get("delivery_date", ""),
            "items": [
                {
                    "item_code": item.get("item_code", ""),
                    "qty": float(item.get("qty", 1)),
                    "rate": float(item.get("rate", 0)),
                    "uom": item.get("uom", "Nos"),
                    "delivery_date": item.get("delivery_date", draft.get("delivery_date", "")),
                    "warehouse": item.get("warehouse", "Stores - APM"),
                }
                for item in items
            ],
        }
        try:
            result = await self._client.create_draft(
                "Sales Order",
                payload,
                approval_id=draft.get("approval_id", "unknown"),
                approved_by=draft.get("approved_by", "unknown"),
            )
            data = result.get("data", {})
            return {
                "draft_id": data.get("name", ""),
                "doctype": "Sales Order",
                "docstatus": data.get("docstatus", 0),
                "customer": data.get("customer", ""),
                "delivery_date": data.get("delivery_date", ""),
                "total": str(data.get("total", "0")),
                "currency": data.get("currency", "CNY"),
                "status": "DRAFT",
                "authority": self.authority,
                "approval_id": result.get("approval_id", ""),
            }
        except Exception as e:
            return {
                "draft_id": "",
                "doctype": "Sales Order",
                "docstatus": -1,
                "status": "ERROR",
                "authority": self.authority,
                "error": str(e),
            }

    async def create_purchase_order_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """创建采购订单草稿（Purchase Order）。"""
        if not self._client.draft_writes_enabled:
            return {
                "draft_id": "",
                "doctype": "Purchase Order",
                "docstatus": -1,
                "status": "WRITE_DISABLED",
                "authority": self.authority,
                "error": "ERPNext 草稿写入未启用",
            }

        items = draft.get("items", [])
        payload = {
            "supplier": draft.get("supplier", draft.get("supplier_id", "")),
            "company": draft.get("company", "AutoParts Manufacturing"),
            "currency": draft.get("currency", "CNY"),
            "transaction_date": draft.get("transaction_date", ""),
            "schedule_date": draft.get("schedule_date", ""),
            "items": [
                {
                    "item_code": item.get("item_code", ""),
                    "qty": float(item.get("qty", 1)),
                    "rate": float(item.get("rate", 0)),
                    "uom": item.get("uom", "Nos"),
                    "schedule_date": item.get("schedule_date", draft.get("schedule_date", "")),
                    "warehouse": item.get("warehouse", "Stores - APM"),
                }
                for item in items
            ],
        }
        try:
            result = await self._client.create_draft(
                "Purchase Order",
                payload,
                approval_id=draft.get("approval_id", "unknown"),
                approved_by=draft.get("approved_by", "unknown"),
            )
            data = result.get("data", {})
            return {
                "draft_id": data.get("name", ""),
                "doctype": "Purchase Order",
                "docstatus": data.get("docstatus", 0),
                "supplier": data.get("supplier", ""),
                "schedule_date": data.get("schedule_date", ""),
                "total": str(data.get("total", "0")),
                "currency": data.get("currency", "CNY"),
                "status": "DRAFT",
                "authority": self.authority,
                "approval_id": result.get("approval_id", ""),
            }
        except Exception as e:
            return {
                "draft_id": "",
                "doctype": "Purchase Order",
                "docstatus": -1,
                "status": "ERROR",
                "authority": self.authority,
                "error": str(e),
            }

    async def read_back(self, object_ref: dict[str, str]) -> dict[str, Any]:
        """回读草稿确认。"""
        doctype = object_ref.get("doctype", "")
        obj_id = object_ref.get("id", object_ref.get("name", ""))
        try:
            data = await self._client.get_document(doctype, obj_id)
            return {
                **data,
                "read_back": True,
                "confirmed_docstatus": data.get("docstatus", 0),
                "authority": self.authority,
            }
        except Exception:
            return {
                "id": obj_id,
                "doctype": doctype,
                "found": False,
                "authority": self.authority,
            }
