"""
OpenMES 真实适配器。

实现 MESAdapter Protocol，基于 OpenMES REST API 读取生产数据。
所有返回的记录都带有 authority="OpenMES" 标记。
写操作默认禁用，仅支持只读查询。

字段映射基于 OpenMES API 实测（2026-09-28）：
- 工单列表: GET /api/v1/work-orders (Bearer)
  返回字段: id, order_no, planned_qty, produced_qty, status, priority,
            due_date, line_id, line(nested), product_type(nested)
- 工单详情: GET /api/v1/work-orders/{id} (Bearer)
  返回字段: 同上 + issues(nested array), batches(nested array)
- 产线列表: GET /api/v1/lines (Bearer)
- 生产完工: GET /api/v1/erp/production/completions (X-Api-Key)
- 质量问题: GET /api/v1/erp/quality/issues (X-Api-Key)
"""
from __future__ import annotations

import logging
from typing import Any

from app.adapters.mes.base import MESAdapter
from app.adapters.mes.openmes import OpenMESClient
from app.integrations.errors import IntegrationNotConfigured

logger = logging.getLogger(__name__)


class OpenMESAdapter:
    """
    OpenMES 真实数据适配器。

    实现 MESAdapter Protocol，通过 OpenMESClient 调用 OpenMES REST API。
    - 查询操作：直接读取 OpenMES 数据
    - 写操作：默认禁用，抛出权限错误
    - 连接失败时：记录错误并返回带 error 信息的空结果，不静默退回 Mock
    """

    def __init__(self, client: OpenMESClient) -> None:
        self._client = client
        self.authority = "OpenMES"

    def _map_work_order(self, wo: dict[str, Any]) -> dict[str, Any]:
        """将 OpenMES 工单 API 响应映射为内部标准格式。"""
        product_type = wo.get("product_type") or {}
        line = wo.get("line") or {}
        return {
            "work_order_id": str(wo.get("id", "")),
            "work_order_no": wo.get("order_no", ""),
            # OpenMES 官方 ERP 集成字段：承载 ERP/客户订单号（当前真实数据中可为空）
            "customer_order_no": wo.get("customer_order_no") or "",
            "product_id": product_type.get("code", ""),
            "product_name": product_type.get("name", ""),
            # 产品主数据级集成标识（external_system="erpnext" 时 external_code 对应 ERP 物料编码）
            "product_external_code": product_type.get("external_code") or "",
            "product_external_system": product_type.get("external_system") or "",
            # ERP 导入回传 payload 需要的原始编码字段（update_or_create 会重写这些字段）
            "line_code": line.get("code", ""),
            "product_type_code": product_type.get("code", ""),
            "description": wo.get("description") or "",
            "quantity": str(wo.get("planned_qty", 0)),
            "completed_qty": str(wo.get("produced_qty", 0)),
            "rejected_qty": str(wo.get("rejected_qty", 0)),
            "status": wo.get("status", ""),
            "priority": wo.get("priority", ""),
            "due_date": wo.get("due_date", ""),
            "line_id": str(wo.get("line_id", "")),
            "line_name": line.get("name", ""),
            "planned_start": wo.get("planned_start_at", ""),
            "planned_end": wo.get("planned_end_at", ""),
            "actual_start": wo.get("completed_at", ""),
            "authority": self.authority,
            "data_source": "openmes_api",
        }

    def _map_quality_issue(self, item: dict[str, Any]) -> dict[str, Any]:
        """将 OpenMES 质量问题映射为内部标准格式。"""
        issue_type = item.get("issue_type") or {}
        if isinstance(issue_type, str):
            issue_type_name = issue_type
            issue_type_code = ""
        else:
            issue_type_name = issue_type.get("name", "")
            issue_type_code = issue_type.get("code", "")
        return {
            "record_id": str(item.get("id", "")),
            "record_type": issue_type_name or "quality_issue",
            "issue_code": issue_type_code,
            "work_order_id": str(item.get("work_order_id", "")),
            "work_order_no": item.get("work_order_no") or "",
            "title": item.get("title", ""),
            "description": item.get("description", ""),
            "severity": item.get("severity", "medium"),
            "status": item.get("status", "open"),
            "disposition": item.get("disposition", "pending"),
            "non_conforming_qty": str(item.get("non_conforming_qty") or 0),
            "nc_source": item.get("nc_source", ""),
            "root_cause": item.get("root_cause", ""),
            "containment_action": item.get("containment_action", ""),
            "reported_at": item.get("reported_at", ""),
            "resolved_at": item.get("resolved_at", ""),
            "authority": self.authority,
            "data_source": "openmes_api",
        }

    # ========== 工单与进度 ==========

    async def get_work_orders(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取工单列表。"""
        try:
            filters: dict[str, Any] = {}
            if scope.get("status"):
                filters["status"] = scope["status"]
            if scope.get("search"):
                filters["search"] = scope["search"]
            filters["per_page"] = min(scope.get("limit", 50), 100)

            result = await self._client.list_work_orders(filters)
            data = result.get("data", [])
            return [self._map_work_order(wo) for wo in data]
        except Exception as e:
            logger.error("OpenMES get_work_orders failed: %s", e)
            return []

    async def get_work_orders_strict(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取工单列表（严格模式）。

        与 get_work_orders 的区别：连接失败时抛出异常而不是返回空列表。
        用于订单关联查询——空列表会被误判为"未建立关联"，必须区分
        "连接失败"和"确实没有匹配工单"。
        """
        filters: dict[str, Any] = {}
        if scope.get("status"):
            filters["status"] = scope["status"]
        if scope.get("search"):
            filters["search"] = scope["search"]
        filters["per_page"] = min(scope.get("limit", 50), 100)

        result = await self._client.list_work_orders(filters)
        data = result.get("data", [])
        return [self._map_work_order(wo) for wo in data]

    async def import_erp_work_orders(
        self, orders: list[dict[str, Any]], strategy: str = "update_or_create"
    ) -> dict[str, Any]:
        """通过官方 ERP 导入接口创建/更新工单（写操作，X-Api-Key）。

        仅用于经过人工审批的关联回填；update_or_create 会按提交值重写
        line/product/planned_qty/priority/due_date，调用方必须先读当前值。
        """
        return await self._client.import_erp_work_orders(orders, strategy=strategy)

    async def get_operation_progress(self, work_order_id: str) -> list[dict[str, Any]]:
        """获取工序进度。

        OpenMES API 没有独立的工序端点，工单详情中包含 process_snapshot
        和 issues。将工单级别的进度作为整体工序返回。
        """
        try:
            result = await self._client.get_work_order(work_order_id)
            data = result.get("data", {})

            planned = data.get("planned_qty", 0)
            produced = data.get("produced_qty", 0)
            packed = data.get("packed_qty", 0)
            status = data.get("status", "pending")
            line = data.get("line") or {}

            process_snapshot = data.get("process_snapshot")
            if isinstance(process_snapshot, dict) and process_snapshot.get("steps"):
                steps = process_snapshot.get("steps", [])
                return [
                    {
                        "operation_id": str(step.get("id", f"step-{i}")),
                        "operation_name": step.get("name", f"工序 {i+1}"),
                        "sequence": i + 1,
                        "planned_qty": str(planned),
                        "completed_qty": str(step.get("completed_qty", 0)),
                        "rejected_qty": str(step.get("rejected_qty", 0)),
                        "status": step.get("status", "pending"),
                        "work_center": step.get("workstation", ""),
                        "start_time": step.get("started_at", ""),
                        "end_time": step.get("finished_at", ""),
                        # These are execution facts, not planned schedule values.
                        # Keep them explicit so ETA cannot accidentally use a
                        # planned start as an observed production rate sample.
                        "actual_start_at": step.get("started_at", ""),
                        "actual_end_at": step.get("finished_at", ""),
                        "actual_elapsed_minutes": step.get("actual_elapsed_minutes"),
                        "actual_run_minutes": step.get("actual_run_minutes"),
                        "run_time_per_unit_minutes": step.get("run_time_per_unit_minutes"),
                        "passed_qty": str(step.get("passed_qty", step.get("completed_qty", 0))),
                        "authority": self.authority,
                        "data_source": "openmes_api",
                    }
                    for i, step in enumerate(steps)
                ]

            return [
                {
                    "operation_id": f"WO-{work_order_id}-OVERALL",
                    "operation_name": "整体生产进度",
                    "sequence": 1,
                    "planned_qty": str(planned),
                    "completed_qty": str(produced),
                    "packed_qty": str(packed),
                    "rejected_qty": "0",
                    "status": status,
                    "work_center": line.get("name", ""),
                    # The overall row has no observed timing when the order has
                    # not started. Planned dates are kept separately and are
                    # never used as rate observations.
                    "start_time": data.get("actual_start_at", ""),
                    "end_time": data.get("completed_at", ""),
                    "actual_start_at": data.get("actual_start_at", ""),
                    "actual_end_at": data.get("completed_at", ""),
                    "planned_start_at": data.get("planned_start_at", ""),
                    "planned_end_at": data.get("planned_end_at", ""),
                    "actual_elapsed_minutes": data.get("actual_elapsed_minutes"),
                    "actual_run_minutes": data.get("actual_run_minutes"),
                    "passed_qty": str(produced),
                    "due_date": data.get("due_date", ""),
                    "authority": self.authority,
                    "data_source": "openmes_api",
                }
            ]
        except Exception as e:
            logger.error("OpenMES get_operation_progress failed (wo=%s): %s", work_order_id, e)
            return []

    async def get_wip(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取在制品列表。"""
        try:
            filters: dict[str, Any] = {"per_page": min(scope.get("limit", 100), 100)}
            if scope.get("status"):
                filters["status"] = scope["status"]
            result = await self._client.list_work_orders(filters)
            data = result.get("data", [])
            wip = [self._map_work_order(wo) for wo in data
                    if wo.get("status") in ("ACCEPTED", "IN_PROGRESS", "PLANNED")]
            return wip
        except Exception as e:
            logger.error("OpenMES get_wip failed: %s", e)
            return []

    async def get_quality_records(self, batch_scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取质量记录（质量问题/NCR 等）。

        工单范围内只返回与该工单编号或 ID 明确关联的问题。
        OpenMES 的 ERP 导出字段是 work_order_no，而不是 work_order_id。
        读取失败直接抛出，避免把未知状态误判成没有质量问题。
        """
        wo_id = str(batch_scope.get("work_order_id") or "")
        wo_data: dict[str, Any] = {}
        order_no = ""
        if wo_id:
            wo_result = await self._client.get_work_order(wo_id)
            wo_data = wo_result.get("data") or {}
            order_no = str(wo_data.get("order_no") or "")
            if not order_no:
                raise ValueError(f"OpenMES 工单 {wo_id} 缺少 order_no，无法核对质量问题归属")

        result = await self._client.list_erp_quality_issues(since=batch_scope.get("since"))
        records = []
        for item in result.get("data", []):
            if wo_id and str(item.get("work_order_no") or "") != order_no:
                continue
            records.append(self._map_quality_issue(item))

        if wo_id:
            existing_ids = {r["record_id"] for r in records}
            for issue in wo_data.get("issues") or []:
                issue_id = str(issue.get("id", ""))
                if issue_id not in existing_ids:
                    records.append(self._map_quality_issue({
                        **issue, "work_order_id": wo_id, "work_order_no": order_no,
                    }))
                    existing_ids.add(issue_id)
        return records

    async def list_open_issues(
        self,
        *,
        statuses: tuple[str, ...] = ("OPEN", "ACKNOWLEDGED", "RESOLVED"),
    ) -> list[dict[str, Any]]:
        """跨工单未关闭质量问题队列（MRB 待办视角，只读）。

        三态分别拉取（CLOSED 不进待办），翻页跟随 ``meta.last_page``（封顶
        10 页防失控）。任何一态读取失败直接抛出——绝不把失败伪装成空待办。
        字段映射以 2026-09-30 实测 payload 为准：``work_order.order_no``、
        ``issue_type.severity``（severity 在类型上，不在 issue 上）、
        ``assigned_to`` 为用户对象或 null。
        """
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for status in statuses:
            page = 1
            while True:
                result = await self._client.list_issues(status=status, page=page)
                meta = result.get("meta") or {}
                for raw in result.get("data", []):
                    issue_id = str(raw.get("id", ""))
                    if not issue_id or issue_id in seen:
                        continue
                    seen.add(issue_id)
                    work_order = raw.get("work_order") or {}
                    issue_type = raw.get("issue_type") or {}
                    assigned = raw.get("assigned_to") or {}
                    items.append({
                        "issue_id": issue_id,
                        "work_order_id": str(raw.get("work_order_id", "")),
                        "work_order_no": str(work_order.get("order_no", "")),
                        "title": str(raw.get("title", "")),
                        "severity": str(issue_type.get("severity", "")),
                        "status": str(raw.get("status", "")),
                        "disposition": str(raw.get("disposition") or ""),
                        "reported_at": str(raw.get("reported_at", "")),
                        "assigned_to": str(
                            assigned.get("username") or assigned.get("name") or ""
                        ),
                        "authority": self.authority,
                        "data_source": "openmes_issues",
                    })
                last_page = int(meta.get("last_page") or 1)
                if page >= min(last_page, 10):
                    break
                page += 1
        items.sort(key=lambda row: row.get("reported_at") or "", reverse=True)
        return items

    async def get_work_order_documents(self, work_order_id: str) -> list[dict[str, Any]]:
        """获取工单关联工程文档（SOP/Control Plan 类载体的真实记录）。

        接口真实存在；当前 OpenMES 数据库中可能为 0 条，如实返回空列表。
        连接失败抛出异常。
        """
        rows = await self._client.list_work_order_engineering_documents(work_order_id)
        # Older OpenMES builds omit document_type from the frozen snapshot and
        # work-order document list, while the documented detail endpoint still
        # exposes it. Enrich by id instead of guessing from filenames.
        enriched: list[dict[str, Any]] = []
        for row in rows:
            if not row.get("document_type") and row.get("document_id"):
                try:
                    detail = await self._client.get_engineering_document(row["document_id"])
                    row = {**row, "document_type": detail.get("document_type")}
                except Exception as exc:
                    logger.warning("OpenMES engineering document detail unavailable (id=%s): %s", row.get("document_id"), exc)
            enriched.append(row)
        return [
            {
                "doc_id": str(r.get("document_id", r.get("id", ""))),
                "doc_type": r.get("document_type") or r.get("package_type") or "",
                "title": r.get("original_filename", ""),
                "revision": r.get("revision", ""),
                "lifecycle_status": r.get("lifecycle_at_release") or r.get("lifecycle_status", ""),
                "released_at": r.get("released_at") or "",
                "authority": self.authority,
                "data_source": "openmes_api",
            }
            for r in enriched
        ]

    async def upload_engineering_document(self, **kwargs: Any) -> dict[str, Any]:
        return await self._client.upload_engineering_document(**kwargs)

    async def get_inspections(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取检验记录（来料/过程质量）。

        接口真实存在；当前 OpenMES 数据库中为 0 条时返回空列表。
        连接失败抛出异常。
        """
        rows = await self._client.list_inspections({"per_page": min(scope.get("limit", 50), 100)})
        return [
            {
                "inspection_id": str(r.get("id", "")),
                "lot_number": str(r.get("lot_number", "")),
                "inspection_no": r.get("inspection_no", ""),
                "status": r.get("status", ""),
                "disposition": r.get("disposition") or "",
                "inspector": (r.get("inspector") or {}).get("name", "") if isinstance(r.get("inspector"), dict) else "",
                "inspection_date": r.get("inspection_date") or r.get("created_at") or "",
                "authority": self.authority,
                "data_source": "openmes_api",
            }
            for r in rows
        ]

    async def get_work_order_batches(self, work_order_id: str) -> list[dict[str, Any]]:
        """获取工单生产批次（批次/SN 追溯维度）。

        接口真实存在；无批次时返回空列表。连接失败抛出异常。
        """
        rows = await self._client.list_work_order_batches(work_order_id)
        return [
            {
                "batch_id": str(r.get("id", "")),
                "lot_number": r.get("lot_number", ""),
                "target_qty": str(r.get("target_qty", "")),
                "produced_qty": str(r.get("produced_qty", "")),
                "status": r.get("status", ""),
                "started_at": r.get("started_at", ""),
                "completed_at": r.get("completed_at", ""),
                "steps": [
                    {
                        "step_id": str(step.get("id", "")),
                        "step_number": step.get("step_number"),
                        "name": step.get("name", ""),
                        "status": step.get("status", ""),
                        "passed_qty": str(step.get("passed_qty", "")),
                        "started_at": step.get("started_at", ""),
                        "completed_at": step.get("completed_at", ""),
                        "actual_elapsed_minutes": step.get("actual_elapsed_minutes"),
                        "actual_run_minutes": step.get("actual_run_minutes"),
                    }
                    for step in (r.get("steps") or [])
                    if isinstance(step, dict)
                ],
                "authority": self.authority,
                "data_source": "openmes_api",
            }
            for r in rows
        ]

    async def resolve_quality_issue(self, issue_id: str, resolution_notes: str) -> dict[str, Any]:
        return await self._client.resolve_issue(issue_id, resolution_notes)

    async def close_quality_issue(self, issue_id: str) -> dict[str, Any]:
        return await self._client.close_issue(issue_id)

    async def set_quality_issue_disposition(self, issue_id: str, **kwargs: Any) -> dict[str, Any]:
        return await self._client.set_issue_disposition(issue_id, **kwargs)

    async def get_quality_issue_actions(self, issue_id: str) -> list[dict[str, Any]]:
        rows = await self._client.list_issue_actions(issue_id)
        return [
            {
                "action_id": str(row.get("id", "")),
                "type": row.get("type", ""),
                "title": row.get("title", ""),
                "description": row.get("description") or "",
                "status": row.get("status", ""),
                "assigned_to_id": row.get("assigned_to_id"),
                "due_date": row.get("due_date") or "",
                "completed_at": row.get("completed_at") or "",
                "verified_at": row.get("verified_at") or "",
                "authority": self.authority,
                "data_source": "openmes_api",
            }
            for row in rows
        ]

    async def get_production_documents(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        """获取生产文档（SOP、Control Plan 等）。

        OpenMES 当前无独立文档管理端点。检查事件日志是否有关联文档。
        如无文档记录，返回空列表——不虚构占位文档。
        """
        work_order_id = scope.get("work_order_id", scope.get("order_id", ""))
        if not work_order_id:
            return []
        try:
            result = await self._client.list_event_logs("work_order", work_order_id)
            events = result.get("data", [])
            docs = []
            for evt in events:
                doc_types = ["SOP", "Control Plan", "PFMEA", "Drawing", "Inspection Plan"]
                for dt in doc_types:
                    if dt.lower() in str(evt.get("description", "")).lower():
                        docs.append({
                            "doc_id": str(evt.get("id", "")),
                            "doc_type": dt,
                            "doc_name": evt.get("title", f"{dt} 文档"),
                            "work_order_id": work_order_id,
                            "version": evt.get("version", "v1"),
                            "status": "released",
                            "source": "event_log",
                            "authority": self.authority,
                            "data_source": "openmes_api",
                        })
                        break
            return docs
        except Exception as e:
            logger.error("OpenMES get_production_documents failed (wo=%s): %s", work_order_id, e)
            return []

    async def read_authoritative_events(
        self, scope: dict[str, Any], cursor: str | None = None
    ) -> list[dict[str, Any]]:
        """读取权威事件（生产完工、质量问题等）。"""
        events = []
        try:
            result = await self._client.list_erp_production_completions(
                since=scope.get("since"),
                cursor=cursor,
            )
            for item in result.get("data", []):
                events.append({
                    "event_id": f"completion-{item.get('id', '')}",
                    "event_type": "PRODUCTION_COMPLETED",
                    "entity_type": "work_order",
                    "entity_id": str(item.get("work_order_id", "")),
                    "payload": item,
                    "occurred_at": item.get("completed_at", item.get("created_at", "")),
                    "is_authoritative": True,
                    "authority": self.authority,
                    "source": "erp_production_completions",
                })
        except Exception as e:
            logger.error("OpenMES read_authoritative_events (completions) failed: %s", e)

        try:
            result = await self._client.list_erp_quality_issues(
                since=scope.get("since"),
            )
            for item in result.get("data", []):
                events.append({
                    "event_id": f"quality-{item.get('id', '')}",
                    "event_type": "QUALITY_ISSUE",
                    "entity_type": "work_order",
                    "entity_id": str(item.get("work_order_id", "")),
                    "payload": item,
                    "occurred_at": item.get("reported_at", item.get("created_at", "")),
                    "is_authoritative": True,
                    "authority": self.authority,
                    "source": "erp_quality_issues",
                })
        except Exception as e:
            logger.error("OpenMES read_authoritative_events (quality) failed: %s", e)

        return events

    async def create_non_authoritative_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """创建非权威请求（如返工、补料等）。默认禁用。"""
        try:
            await self._client.write_request(request)
            return {
                "request_id": "",
                "status": "submitted",
                "authority": self.authority,
            }
        except Exception as e:
            return {
                "request_id": "",
                "status": "rejected",
                "authority": self.authority,
                "error": str(e),
            }
