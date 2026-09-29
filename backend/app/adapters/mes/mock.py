"""
Mock MES Adapter。

完整实现 MESAdapter Protocol，基于场景 fixture 和内置模拟数据返回结果。
所有返回数据都标记为 mock，明确区分于真实 MES 记录。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class MockMESAdapter:
    """
    模拟 OpenMES 适配器。

    实现 MESAdapter Protocol 的所有方法，数据来自：
    1. 内置的模拟工单、WIP、质量记录等
    2. 场景 fixture 中提供的配置（如 mock_mes_quality_released）
    3. 基于确定性规则生成的结果

    所有返回的记录都带有 authority="MockMES" 标记。
    明确标注为合成数据，不代表任何真实 MES 记录。
    """

    def __init__(self, seed: int = 20260923):
        self.seed = seed
        self._work_orders = self._build_work_orders()
        self._quality_records = self._build_quality_records()
        self._production_documents = self._build_production_documents()
        self._events = self._build_events()

    # ========== 工单与进度 ==========

    async def get_work_orders(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取工单列表。"""
        project_id = scope.get("project_id", scope.get("order_id", ""))
        product_id = scope.get("product_id", "")
        status = scope.get("status", "")

        results = []
        for wo in self._work_orders.values():
            # 简单过滤
            if status and wo.get("status") != status:
                continue
            if product_id and wo.get("product_id") != product_id:
                continue
            results.append({
                **wo,
                "customer_order_no": wo.get("customer_order_no", ""),
                "authority": "MockMES",
                "data_source": "synthetic_demo_only",
            })

        return results

    async def get_work_orders_strict(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """严格模式获取工单列表。Mock 无连接故障，行为与 get_work_orders 一致。"""
        return await self.get_work_orders(scope)

    async def import_erp_work_orders(
        self, orders: list[dict[str, Any]], strategy: str = "update_or_create"
    ) -> dict[str, Any]:
        """Mock 版 ERP 工单导入：仅更新内存 fixture，标记 synthetic_demo_only。"""
        imported = updated = skipped = 0
        errors: list[dict[str, Any]] = []
        for row in orders:
            order_no = (row.get("order_no") or "").strip()
            wo = self._work_orders.get(order_no)
            if wo is not None:
                if strategy == "skip_existing":
                    skipped += 1
                    continue
                if "customer_order_no" in row:
                    wo["customer_order_no"] = row["customer_order_no"]
                if "quantity" in row:
                    wo["quantity"] = row["quantity"]
                updated += 1
            elif strategy == "error_on_duplicate":
                errors.append({"field": "order_no", "message": f"工单不存在: {order_no}"})
            else:
                self._work_orders[order_no] = {
                    "work_order_id": order_no,
                    "product_id": row.get("product_type_code", ""),
                    "quantity": row.get("planned_qty", 0),
                    "completed_qty": 0,
                    "status": "PENDING",
                    "customer_order_no": row.get("customer_order_no", ""),
                    "operations": [],
                }
                imported += 1
        return {
            "data": {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors},
            "authority": "MockMES",
            "data_source": "synthetic_demo_only",
        }

    async def get_operation_progress(self, work_order_id: str) -> list[dict[str, Any]]:
        """获取工序进度。"""
        wo = self._work_orders.get(work_order_id)
        if wo is None:
            return []
        return [
            {
                "operation_id": op["operation_id"],
                "operation_name": op["operation_name"],
                "sequence": op["sequence"],
                "planned_qty": wo["quantity"],
                "completed_qty": op.get("completed_qty", 0),
                "rejected_qty": op.get("rejected_qty", 0),
                "status": op.get("status", "pending"),
                "work_center": op.get("work_center", ""),
                "start_time": op.get("start_time", ""),
                "end_time": op.get("end_time", ""),
                "authority": "MockMES",
                "data_source": "synthetic_demo_only",
            }
            for op in wo.get("operations", [])
        ]

    async def get_wip(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取在制品列表。"""
        project_id = scope.get("project_id", "")
        results = []
        for wo_id, wo in self._work_orders.items():
            if wo.get("status") in ("In Process", "Started"):
                wip_qty = int(wo.get("quantity", 0)) - int(wo.get("completed_qty", 0)) - int(wo.get("rejected_qty", 0))
                if wip_qty > 0:
                    results.append({
                        "work_order_id": wo_id,
                        "product_id": wo.get("product_id", ""),
                        "product_name": wo.get("product_name", ""),
                        "wip_quantity": wip_qty,
                        "current_operation": wo.get("current_operation", ""),
                        "status": wo.get("status", ""),
                        "warehouse": "WIP - Shop Floor",
                        "authority": "MockMES",
                        "data_source": "synthetic_demo_only",
                    })
        return results

    # ========== 质量与文档 ==========

    async def get_quality_records(self, batch_scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取质量记录。"""
        batch_no = batch_scope.get("batch_no", batch_scope.get("batch_id", ""))
        project_id = batch_scope.get("project_id", "")
        work_order_id = batch_scope.get("work_order_id", "")

        results = []
        for qr in self._quality_records:
            if batch_no and qr.get("batch_no") != batch_no:
                continue
            if work_order_id and qr.get("work_order_id") != work_order_id:
                continue
            results.append({
                **qr,
                "authority": "MockMES",
                "data_source": "synthetic_demo_only",
            })
        return results

    async def get_work_order_documents(self, work_order_id: str) -> list[dict[str, Any]]:
        """Mock 工程文档：返回空列表（Mock 场景不模拟工程文档功能）。"""
        return []

    async def upload_engineering_document(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("MockMES 不执行工程文档写回")

    async def get_inspections(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """Mock 检验记录：返回空列表（Mock 场景不模拟检验功能）。"""
        return []

    async def get_work_order_batches(self, work_order_id: str) -> list[dict[str, Any]]:
        """Mock 批次：返回空列表（Mock 场景不模拟批次追溯）。"""
        return []

    async def resolve_quality_issue(self, issue_id: str, resolution_notes: str) -> dict[str, Any]:
        raise RuntimeError("MockMES 不执行质量问题写回")

    async def close_quality_issue(self, issue_id: str) -> dict[str, Any]:
        raise RuntimeError("MockMES 不执行质量问题写回")

    async def get_production_documents(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取生产文档。"""
        results = []
        for doc in self._production_documents:
            results.append({
                **doc,
                "authority": "MockMES",
                "data_source": "synthetic_demo_only",
            })
        return results

    # ========== 事件与请求 ==========

    async def read_authoritative_events(
        self,
        scope: dict[str, Any],
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取权威事件。"""
        project_id = scope.get("project_id", "")
        entity_type = scope.get("entity_type", "")

        results = []
        for evt in self._events:
            if entity_type and evt.get("entity_type") != entity_type:
                continue
            results.append({
                **evt,
                "authoritative": True,
                "authority": "MockMES",
                "data_source": "synthetic_demo_only",
            })

        # 模拟分页
        if cursor:
            try:
                skip = int(cursor)
                results = results[skip:]
            except (ValueError, TypeError):
                pass

        return results

    async def create_non_authoritative_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """创建非权威请求（不直接改变 MES 状态）。"""
        request_id = f"REQ-MOCK-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        return {
            "request_id": request_id,
            "request_type": request.get("request_type", ""),
            "status": "ACKNOWLEDGED",
            "acknowledged": True,
            "authoritative": False,  # 明确标记为非权威
            "authority": "MockMES",
            "data_source": "synthetic_demo_only",
            "note": "Mock MES 仅确认收到请求，不执行任何权威状态变更。",
        }

    # ========== 内置模拟数据 ==========

    def _build_work_orders(self) -> dict[str, dict[str, Any]]:
        base_date = datetime(2026, 10, 20, 8, 0, 0)
        return {
            "WO-DEMO-001": {
                "work_order_id": "WO-DEMO-001",
                "production_order_id": "PO-DEMO-001",
                "product_id": "DEMO-BRACKET-001",
                "product_name": "演示支架组件",
                "quantity": 120,
                "completed_qty": 80,
                "rejected_qty": 2,
                "status": "In Process",
                "priority": "Normal",
                "planned_start": base_date.isoformat(),
                "planned_end": (base_date + timedelta(days=4)).isoformat(),
                "current_operation": "Machining",
                "operations": [
                    {
                        "operation_id": "OP-001",
                        "operation_name": "下料",
                        "sequence": 10,
                        "status": "Completed",
                        "completed_qty": 120,
                        "rejected_qty": 0,
                        "work_center": "WC-CUT-01",
                        "start_time": base_date.isoformat(),
                        "end_time": (base_date + timedelta(hours=4)).isoformat(),
                    },
                    {
                        "operation_id": "OP-010",
                        "operation_name": "Machining",
                        "sequence": 20,
                        "status": "In Process",
                        "completed_qty": 80,
                        "rejected_qty": 2,
                        "work_center": "WC-MACH-03",
                        "start_time": (base_date + timedelta(days=1)).isoformat(),
                        "end_time": None,
                    },
                    {
                        "operation_id": "OP-020",
                        "operation_name": "Assembly",
                        "sequence": 30,
                        "status": "Pending",
                        "completed_qty": 0,
                        "rejected_qty": 0,
                        "work_center": "WC-ASM-02",
                        "start_time": None,
                        "end_time": None,
                    },
                    {
                        "operation_id": "OP-030",
                        "operation_name": "Inspection",
                        "sequence": 40,
                        "status": "Pending",
                        "completed_qty": 0,
                        "rejected_qty": 0,
                        "work_center": "QC-AREA",
                        "start_time": None,
                        "end_time": None,
                    },
                ],
            },
            "WO-GEAR-001": {
                "work_order_id": "WO-GEAR-001",
                "production_order_id": "PO-GEAR-001",
                "product_id": "PROD-GEAR-001",
                "product_name": "变速箱驱动齿轮",
                "quantity": 5000,
                "completed_qty": 0,
                "rejected_qty": 0,
                "status": "Released",
                "priority": "High",
                "planned_start": (base_date + timedelta(days=10)).isoformat(),
                "planned_end": (base_date + timedelta(days=20)).isoformat(),
                "current_operation": "",
                "operations": [
                    {
                        "operation_id": "OP-G010",
                        "operation_name": "锻造",
                        "sequence": 10,
                        "status": "Pending",
                        "completed_qty": 0,
                        "rejected_qty": 0,
                        "work_center": "WC-FORGE-01",
                    },
                    {
                        "operation_id": "OP-G020",
                        "operation_name": "机加工",
                        "sequence": 20,
                        "status": "Pending",
                        "completed_qty": 0,
                        "rejected_qty": 0,
                        "work_center": "WC-GEAR-MACH",
                    },
                    {
                        "operation_id": "OP-G030",
                        "operation_name": "热处理",
                        "sequence": 30,
                        "status": "Pending",
                        "completed_qty": 0,
                        "rejected_qty": 0,
                        "work_center": "WC-HEAT-TREAT",
                    },
                    {
                        "operation_id": "OP-G040",
                        "operation_name": "磨齿",
                        "sequence": 40,
                        "status": "Pending",
                        "completed_qty": 0,
                        "rejected_qty": 0,
                        "work_center": "WC-GRIND-02",
                    },
                ],
            },
        }

    def _build_quality_records(self) -> list[dict[str, Any]]:
        base_date = datetime(2026, 10, 21, 14, 30, 0)
        return [
            {
                "record_id": "IQC-2026-1001",
                "record_type": "IQC",  # 来料检验
                "batch_no": "BATCH-AL-2026-001",
                "material_id": "DEMO-AL-102",
                "inspection_date": base_date.isoformat(),
                "inspector": "mock-inspector-01",
                "result": "PASSED",
                "samples": 10,
                "accepted": 10,
                "rejected": 0,
                "defects": [],
                "evidence_id": "ev-mock-mes-iqc-al-001",
            },
            {
                "record_id": "IPQC-2026-1002",
                "record_type": "IPQC",  # 过程检验
                "work_order_id": "WO-DEMO-001",
                "batch_no": "WO-DEMO-001-B01",
                "product_id": "DEMO-BRACKET-001",
                "operation_id": "OP-010",
                "inspection_date": (base_date + timedelta(days=1)).isoformat(),
                "inspector": "mock-inspector-02",
                "result": "PASSED",
                "samples": 20,
                "accepted": 19,
                "rejected": 1,
                "defects": [
                    {"defect_type": "dimension_out", "severity": "minor", "quantity": 1}
                ],
                "evidence_id": "ev-mock-mes-ipqc-001",
            },
            {
                "record_id": "FQC-2026-1003",
                "record_type": "FQC",  # 成品检验
                "work_order_id": "WO-DEMO-001",
                "batch_no": "WO-DEMO-001-B01",
                "product_id": "DEMO-BRACKET-001",
                "inspection_date": (base_date + timedelta(days=3)).isoformat(),
                "inspector": "mock-inspector-03",
                "result": "PASSED",
                "samples": 30,
                "accepted": 30,
                "rejected": 0,
                "defects": [],
                "evidence_id": "ev-mock-mes-fqc-001",
            },
            {
                "record_id": "IQC-2026-2001",
                "record_type": "IQC",
                "batch_no": "BATCH-STEEL-2026-001",
                "material_id": "MAT-STEEL-20CrMnTi",
                "inspection_date": (base_date - timedelta(days=5)).isoformat(),
                "inspector": "mock-inspector-01",
                "result": "PASSED",
                "samples": 5,
                "accepted": 5,
                "rejected": 0,
                "defects": [],
                "evidence_id": "ev-mock-mes-iqc-steel-001",
            },
        ]

    def _build_production_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "doc_id": "SOP-DEMO-001",
                "doc_type": "SOP",
                "doc_name": "演示支架组件作业指导书",
                "product_id": "DEMO-BRACKET-001",
                "operation_id": "OP-010",
                "version": "v1.2",
                "status": "RELEASED",
                "uri": "mock://docs/sop/demo-bracket-001-v1.2.pdf",
                "evidence_id": "ev-mock-mes-doc-sop-001",
            },
            {
                "doc_id": "CT-DEMO-001",
                "doc_type": "Control Plan",
                "doc_name": "演示支架组件控制计划",
                "product_id": "DEMO-BRACKET-001",
                "version": "v1.0",
                "status": "RELEASED",
                "uri": "mock://docs/control-plan/demo-bracket-001-v1.0.pdf",
                "evidence_id": "ev-mock-mes-doc-ct-001",
            },
            {
                "doc_id": "PFMEA-DEMO-001",
                "doc_type": "PFMEA",
                "doc_name": "演示支架组件过程FMEA",
                "product_id": "DEMO-BRACKET-001",
                "version": "v2.1",
                "status": "RELEASED",
                "uri": "mock://docs/pfmea/demo-bracket-001-v2.1.pdf",
                "evidence_id": "ev-mock-mes-doc-pfmea-001",
            },
        ]

    def _build_events(self) -> list[dict[str, Any]]:
        base_date = datetime(2026, 10, 20, 9, 0, 0)
        return [
            {
                "event_id": "EVT-MES-001",
                "event_type": "WORK_ORDER_STARTED",
                "entity_type": "work_order",
                "entity_id": "WO-DEMO-001",
                "occurred_at": base_date.isoformat(),
                "payload": {
                    "work_order_id": "WO-DEMO-001",
                    "operation_id": "OP-001",
                    "started_by": "mock-operator-01",
                },
            },
            {
                "event_id": "EVT-MES-002",
                "event_type": "OPERATION_COMPLETED",
                "entity_type": "work_order",
                "entity_id": "WO-DEMO-001",
                "occurred_at": (base_date + timedelta(hours=4)).isoformat(),
                "payload": {
                    "work_order_id": "WO-DEMO-001",
                    "operation_id": "OP-001",
                    "completed_qty": 120,
                    "rejected_qty": 0,
                },
            },
            {
                "event_id": "EVT-MES-003",
                "event_type": "QUALITY_HOLD",
                "entity_type": "work_order",
                "entity_id": "WO-DEMO-001",
                "occurred_at": (base_date + timedelta(days=1, hours=6)).isoformat(),
                "payload": {
                    "work_order_id": "WO-DEMO-001",
                    "operation_id": "OP-010",
                    "reason": "dimension_deviation",
                    "severity": "minor",
                },
            },
            {
                "event_id": "EVT-MES-004",
                "event_type": "QUALITY_RELEASED",
                "entity_type": "work_order",
                "entity_id": "WO-DEMO-001",
                "occurred_at": (base_date + timedelta(days=1, hours=8)).isoformat(),
                "payload": {
                    "work_order_id": "WO-DEMO-001",
                    "operation_id": "OP-010",
                    "released_by": "mock-qc-supervisor-01",
                    "disposition": "use_as_is",
                },
            },
        ]

    # ========== 兼容旧接口（scenarios 中直接使用的方法） ==========

    def quality_status(self, project_id: str, released: bool) -> dict[str, Any]:
        """兼容旧接口：质量状态。"""
        return {
            "object_id": f"QUALITY-{project_id}",
            "event_type": "QUALITY_RELEASED" if released else "QUALITY_HOLD",
            "status": "QUALITY_RELEASED" if released else "QUALITY_HOLD",
            "authority": "MockMES",
            "evidence_id": "ev-mock-mes-quality",
        }


# 全局单例（兼容旧代码）
_mock_mes_adapter: MockMESAdapter | None = None


def get_mock_mes() -> MockMESAdapter:
    """获取全局 Mock MES 实例。"""
    global _mock_mes_adapter
    if _mock_mes_adapter is None:
        _mock_mes_adapter = MockMESAdapter()
    return _mock_mes_adapter
