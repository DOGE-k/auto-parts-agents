"""
真实 ERP 销售订单 ↔ 真实 MES 工单关联查询服务（只读）。

关联字段依据（2026-09-28 实测确认，非推断）：
- OpenMES work_orders.customer_order_no 是官方 ERP 集成字段：
  * POST /api/v1/erp/work-orders/import 的 payload 显式包含 orders.*.customer_order_no
  * StoreWorkOrderRequest / UpdateWorkOrderRequest 均接受该字段（nullable, max:100）
  * ProductionExportController 完工导出会原样返回该字段
- OpenMES product_type.external_system="erpnext" + external_code 是产品主数据级
  集成标识，只能证明"同一产品"，不能证明"同一张订单"。

规则：
- 正式关联仅按 customer_order_no == ERP 销售订单号 精确匹配；
- 找不到时明确返回"未建立关联"，不拿其他工单代替；
- 产品编码一致的工单仅列为人工确认候选，显式标注"不构成正式关联"；
- ERP/MES 连接失败直接抛错，绝不回退 Mock，也不用空结果冒充"未建立关联"。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.adapters.factory import get_adapter_mode, get_erp_adapter, get_mes_adapter
from app.services.real_order import _agent_run

logger = logging.getLogger(__name__)

LINK_FIELD = "customer_order_no"
LINK_FIELD_DESCRIPTION = (
    "OpenMES work_orders.customer_order_no —— OpenMES 官方 ERP 集成字段，"
    "用于承载 ERP/客户订单号（erp/work-orders/import 接口的正式 payload 字段）"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_part(value: str) -> str:
    """取日期部分做一致性比对（'2026-10-15T00:00:00.000000Z' → '2026-10-15'）。"""
    return (value or "")[:10]


async def list_real_sales_orders(limit: int = 50) -> dict[str, Any]:
    """列出真实 ERP 销售订单（供用户选择）。连接失败时异常直接向上抛。"""
    erp = get_erp_adapter()
    orders = await erp.list_sales_orders(limit=limit)
    return {
        "orders": orders,
        "count": len(orders),
        "adapter_mode": get_adapter_mode(),
        "authority": orders[0].get("authority", "") if orders else erp.authority,
        "data_source": orders[0].get("data_source", "") if orders else "",
    }


async def get_sales_order_detail(order_id: str) -> dict[str, Any]:
    """读取真实 ERP 销售订单详情。"""
    erp = get_erp_adapter()
    order = await erp.get_sales_order(order_id)
    return order


def _build_candidates(
    erp_order: dict[str, Any],
    work_orders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """列出产品编码一致的 MES 工单，供人工确认——明确标注不构成正式关联。"""
    so_item_codes = {it["item_code"] for it in erp_order.get("items", []) if it.get("item_code")}
    candidates = []
    for wo in work_orders:
        if wo.get("product_id") in so_item_codes:
            candidates.append({
                "work_order_id": wo.get("work_order_id", ""),
                "work_order_no": wo.get("work_order_no", ""),
                "product_id": wo.get("product_id", ""),
                "product_name": wo.get("product_name", ""),
                "planned_qty": wo.get("quantity", ""),
                "due_date": wo.get("due_date", ""),
                "status": wo.get("status", ""),
                "line_name": wo.get("line_name", ""),
                "match_basis": "product_code_only",
                "not_an_association": True,
                "note": "产品编码与订单物料一致，仅供人工确认关联，不构成正式关联，不得作为业务结果",
            })
    return candidates


def _build_master_data_note(
    erp_order: dict[str, Any],
    work_orders: list[dict[str, Any]],
) -> dict[str, Any]:
    """产品主数据级对应说明（external_code 显式集成字段），仅信息性。"""
    so_item_codes = {it["item_code"] for it in erp_order.get("items", []) if it.get("item_code")}
    wo_by_product: dict[str, dict[str, Any]] = {}
    for wo in work_orders:
        ext = wo.get("product_external_code", "")
        if ext and ext not in wo_by_product:
            wo_by_product[ext] = wo
    items_note = []
    for code in sorted(so_item_codes):
        wo = wo_by_product.get(code)
        items_note.append({
            "erp_item_code": code,
            "mes_product_external_code": wo.get("product_external_code", "") if wo else "",
            "mes_product_external_system": wo.get("product_external_system", "") if wo else "",
            "note": (
                "MES 产品类型声明 external_system=erpnext，external_code 与 ERP 物料编码一致（主数据级对应）"
                if wo else "MES 中没有 external_code 对应的产品类型"
            ),
        })
    return {
        "description": "产品主数据级集成标识（external_system/external_code），仅证明产品对应，不证明订单对应",
        "items": items_note,
        "not_an_association": True,
    }


@_agent_run("linkage", "mes_link")
async def get_order_mes_link(order_id: str) -> dict[str, Any]:
    """查询一个真实 ERP 销售订单与真实 MES 工单的正式关联。

    流程：读取真实 ERP 订单 → 严格读取真实 MES 工单列表 →
    按 customer_order_no 精确匹配 → 返回对应关系与证据，或明确"未建立关联"。
    """
    erp = get_erp_adapter()
    mes = get_mes_adapter()

    # 1. 真实 ERP 订单详情（404 → found=False；连接错误直接抛出）
    erp_order = await erp.get_sales_order(order_id)
    if not erp_order.get("found"):
        return {
            "status": "ERP_ORDER_NOT_FOUND",
            "message": f"ERP 中不存在销售订单 {order_id}，未查询 MES",
            "linked": None,
            "order_id": order_id,
            "authority": {"erp": erp_order.get("authority", "")},
            "data_source": {"erp": erp_order.get("data_source", "")},
            "checked_at": _utc_now(),
        }

    # 2. 真实 MES 工单（严格模式：连接失败抛异常，不用空结果冒充"未建立关联"）
    work_orders = await mes.get_work_orders_strict({"limit": 100})

    # 3. 正式关联匹配：customer_order_no == ERP 订单号（精确匹配）
    matched = [wo for wo in work_orders if (wo.get("customer_order_no") or "").strip() == order_id]

    # 4. 产品编码一致但未正式关联的工单（人工确认候选，非关联）
    candidates = _build_candidates(erp_order, work_orders)

    result: dict[str, Any] = {
        "status": "",
        "order_id": order_id,
        "linked": bool(matched),
        "erp_order": erp_order,
        "association": None,
        "unlinked": None,
        "adapter_mode": get_adapter_mode(),
        "authority": {
            "erp": erp_order.get("authority", ""),
            "mes": work_orders[0].get("authority", "") if work_orders else mes.authority,
        },
        "data_source": {
            "erp": erp_order.get("data_source", ""),
            "mes": work_orders[0].get("data_source", "") if work_orders else "",
        },
        "link_field": LINK_FIELD,
        "link_field_description": LINK_FIELD_DESCRIPTION,
        "checked_at": _utc_now(),
    }

    if matched:
        associations = []
        for wo in matched:
            # 数量/交期一致性是证据核验，不是匹配条件
            so_item = next(
                (it for it in erp_order.get("items", []) if it.get("item_code") == wo.get("product_id")),
                None,
            )
            erp_qty = so_item.get("qty", "") if so_item else ""
            erp_delivery = (so_item.get("delivery_date", "") if so_item else "") or erp_order.get("delivery_date", "")
            mes_qty = wo.get("quantity", "")
            mes_due = wo.get("due_date", "")
            quantity_consistent = bool(erp_qty) and _values_equal(erp_qty, mes_qty)
            due_date_consistent = bool(erp_delivery) and bool(mes_due) and _date_part(erp_delivery) == _date_part(mes_due)

            associations.append({
                "status": "LINKED",
                "link_field": LINK_FIELD,
                "matched_value": wo.get("customer_order_no", ""),
                "erp_record": {
                    "record_id": erp_order.get("order_id", ""),
                    "record_type": "Sales Order",
                    "source": "ERPNext",
                    "authority": erp_order.get("authority", ""),
                    "data_source": erp_order.get("data_source", ""),
                    "customer": erp_order.get("customer_name", ""),
                    "item_code": so_item.get("item_code", "") if so_item else "",
                    "quantity": str(erp_qty),
                    "delivery_date": erp_delivery,
                },
                "mes_record": {
                    "record_id": wo.get("work_order_id", ""),
                    "record_no": wo.get("work_order_no", ""),
                    "record_type": "Work Order",
                    "source": "OpenMES",
                    "authority": wo.get("authority", ""),
                    "data_source": wo.get("data_source", ""),
                    "product_id": wo.get("product_id", ""),
                    "planned_qty": str(mes_qty),
                    "due_date": mes_due,
                    "status": wo.get("status", ""),
                },
                "quantity_consistent": quantity_consistent,
                "due_date_consistent": due_date_consistent,
                "evidence": [
                    f"ERP 销售订单 {erp_order.get('order_id', '')}（来源 ERPNext）",
                    f"MES 工单 {wo.get('work_order_no', '')}（记录 id={wo.get('work_order_id', '')}，来源 OpenMES）",
                    f"关联字段 customer_order_no 的值 = {wo.get('customer_order_no', '')}，与 ERP 订单号精确相等",
                    f"数量核验：ERP {erp_qty} vs MES {mes_qty}（{'一致' if quantity_consistent else '不一致，需人工核对'}）",
                    f"交期核验：ERP {erp_delivery} vs MES {mes_due}（{'一致' if due_date_consistent else '不一致，需人工核对'}）",
                ],
            })
        result["status"] = "LINKED"
        result["association"] = {
            "count": len(associations),
            "links": associations,
        }
    else:
        refs_set = [wo for wo in work_orders if (wo.get("customer_order_no") or "").strip()]
        result["status"] = "NOT_LINKED"
        result["unlinked"] = {
            "message": "未建立关联",
            "detail": (
                f"真实 MES 共 {len(work_orders)} 个工单，其中 {len(refs_set)} 个填写了 customer_order_no，"
                f"没有任何工单的 customer_order_no 等于销售订单号 {order_id}"
            ),
            "mes_work_orders_total": len(work_orders),
            "mes_work_orders_with_order_ref": len(refs_set),
            "not_substituted": True,
            "note": "未找到正式关联，不用其他工单代替，不使用产品编码或数量推断关联",
            "candidates_for_manual_review": candidates,
            "master_data_note": _build_master_data_note(erp_order, work_orders),
        }
    return result


def _values_equal(a: Any, b: Any) -> bool:
    """数值相等比较（'500' vs '500.00'）。"""
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


async def link_work_order_to_erp_order(
    work_order_no: str,
    erp_order_id: str,
    approved_by: str,
    approval_note: str = "",
) -> dict[str, Any]:
    """将真实 MES 工单通过官方导入接口关联到真实 ERP 销售订单（写操作）。

    项目已有的 MES 写入方式：POST /api/v1/erp/work-orders/import
    （X-Api-Key + scope erp:orders:import，strategy=update_or_create 回填
    customer_order_no）。

    约束：
    - 必须携带人工审批信息（approved_by/approval_note），缺失时拒绝写入
    - DEMO_* 模拟工单不参与真实关联
    - update_or_create 会按提交值重写 line/product/planned_qty/priority/
      due_date，因此先读取工单当前值并原样回传，只新增 customer_order_no
    - 写入后立即回读验证，回读不一致视为失败
    """
    if not approved_by.strip() or not approval_note.strip():
        return {
            "success": False,
            "error": "缺少人工审批信息（approved_by/approval_note），拒绝写入真实 MES",
        }

    erp = get_erp_adapter()
    mes = get_mes_adapter()

    # 1. ERP 销售订单必须真实存在
    erp_order = await erp.get_sales_order(erp_order_id)
    if not erp_order.get("found"):
        return {
            "success": False,
            "error": f"ERP 销售订单 {erp_order_id} 不存在，拒绝写入",
            "authority": {"erp": erp_order.get("authority", "")},
        }

    # 2. MES 工单必须真实存在
    work_orders = await mes.get_work_orders_strict({"limit": 100})
    wo = next((w for w in work_orders if w.get("work_order_no") == work_order_no), None)
    if not wo:
        return {
            "success": False,
            "error": f"MES 工单 {work_order_no} 不存在，拒绝写入",
            "authority": {"mes": mes.authority},
        }
    if work_order_no.startswith("DEMO_"):
        return {"success": False, "error": "DEMO_* 模拟工单不参与真实关联"}

    # 3. 构造导入 payload：当前值原样回传 + 关联字段
    payload: dict[str, Any] = {
        "order_no": wo["work_order_no"],
        "line_code": wo.get("line_code", ""),
        "product_type_code": wo.get("product_type_code", ""),
        "planned_qty": float(wo.get("quantity") or 0),
        "priority": wo.get("priority", 0),
        "customer_order_no": erp_order_id,
    }
    due = wo.get("due_date") or ""
    if due:
        payload["due_date"] = due[:10]
    if wo.get("description"):
        payload["description"] = wo["description"]

    if not payload["line_code"] or not payload["product_type_code"]:
        return {
            "success": False,
            "error": "MES 工单缺少 line_code/product_type_code，无法构造导入 payload",
        }

    # 4. 写入（官方导入接口）
    result = await mes.import_erp_work_orders([payload], strategy="update_or_create")
    data = result.get("data", {})
    if data.get("errors"):
        return {
            "success": False,
            "error": f"OpenMES 导入接口返回错误: {data['errors']}",
            "import_payload": payload,
            "authority": {"mes": result.get("authority", mes.authority)},
        }

    # 5. 回读验证
    work_orders_after = await mes.get_work_orders_strict({"limit": 100})
    wo_after = next((w for w in work_orders_after if w.get("work_order_no") == work_order_no), None)
    read_back_value = (wo_after or {}).get("customer_order_no", "")
    read_back_ok = read_back_value.strip() == erp_order_id

    return {
        "success": read_back_ok,
        "work_order_no": work_order_no,
        "erp_order_id": erp_order_id,
        "approved_by": approved_by,
        "approval_note": approval_note,
        "import_payload": payload,
        "import_result": {
            "imported": data.get("imported"),
            "updated": data.get("updated"),
            "skipped": data.get("skipped"),
            "errors": data.get("errors"),
        },
        "read_back": {
            "verified": read_back_ok,
            "customer_order_no": read_back_value,
            "quantity": (wo_after or {}).get("quantity", ""),
            "due_date": (wo_after or {}).get("due_date", ""),
            "status": (wo_after or {}).get("status", ""),
        },
        "authority": {
            "erp": erp_order.get("authority", ""),
            "mes": result.get("authority", mes.authority),
        },
        "written_at": _utc_now(),
    }
