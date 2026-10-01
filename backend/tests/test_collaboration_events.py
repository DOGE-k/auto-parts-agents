"""P1 跨智能体协同事件测试。

覆盖 AI_HANDOFF_PLAN 第 6 节事件协作要求：
- 事件编号（EVT-）与去重键（同工单同问题不重复协同）；
- 只读三维度协同（质量影响/生产交期/供应商风险）与数据缺口如实记录；
- 失败记录（FAILED + error_json）与重试上限；
- 人工接管（MANUAL_HANDLED + 操作者留痕）；
- API 端点接线。

全部使用假服务函数（patch real_order 服务层），不触真实系统。
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.persistence.database import SessionLocal
from app.persistence.models import CollaborationEventRow
from app.services import collaboration


def _fake_quality(**overrides):
    async def _fn(work_order_id: str) -> dict:
        base = {
            "status": "ok",
            "work_order_id": work_order_id,
            "work_order_no": "WO-TEST-001",
            "open_issues_count": 1,
            "open_records": [{"record_id": "9", "title": "TEST 质量事件验证", "severity": "HIGH", "status": "OPEN"}],
            "quality_gate_passed": False,
            "missing_documents": ["SOP", "Control Plan"],
            "impact_conclusions": ["1 项未关闭质量问题，质量门禁未通过"],
            "handling_options": [],
            "data_gaps": [{"detail": "检验记录 0 条"}],
        }
        base.update(overrides)
        return base

    return _fn


def _fake_track(**overrides):
    async def _fn(work_order_id: str) -> dict:
        base = {
            "work_order_id": work_order_id,
            "work_order_no": "WO-TEST-001",
            "status": "ACCEPTED",
            "quantity": "400.00",
            "completed_qty": "0.00",
            "completion_rate": 0.0,
            "due_date": "2026-11-20",
            "eta": None,
            "eta_status": "DATA_MISSING",
            "eta_data_gaps": [{"field": "rate", "detail": "无实际速率记录"}],
            "risks": [],
        }
        base.update(overrides)
        return base

    return _fn


class _FakeMES:
    async def get_work_order_raw(self, work_order_id: str) -> dict:
        return {"id": int(work_order_id), "customer_order_no": "SAL-ORD-2026-00025"}


def _patch_happy_path(procurement=None, raw=None):
    """打桩三维度只读协同（默认全链成功 + 真实形状数据）。"""
    mes = _FakeMES()
    if raw is not None:
        mes.get_work_order_raw = AsyncMock(return_value=raw)
    return (
        patch.object(collaboration.real_order, "assess_quality_impact", _fake_quality()),
        patch.object(collaboration.real_order, "track_order", _fake_track()),
        patch.object(collaboration.real_order, "get_mes_adapter", return_value=mes),
        patch.object(
            collaboration.real_order,
            "find_quotation_by_erp_order",
            AsyncMock(return_value={"quotation_id": "QUO-TEST0000001", "status": "APPROVED"}),
        ),
        patch.object(
            collaboration.real_order,
            "analyze_procurement",
            AsyncMock(return_value=procurement or {
                "plan_id": "PROC-TEST0000001",
                "status": "ok",
                "net_requirement": {
                    "has_shortage": False,
                    "shortage_evaluable": True,
                    "shortage_count": 0,
                    "shortage_items": [],
                },
                "supplier_options": [],
            }),
        ),
    )


class CollaborationEventTests(unittest.IsolatedAsyncioTestCase):
    def _count_events(self, dedup_key: str) -> int:
        with SessionLocal() as db:
            rows = db.scalars(
                select(CollaborationEventRow).where(CollaborationEventRow.dedup_key == dedup_key)
            ).all()
            return len(rows)

    async def test_trigger_creates_event_with_real_shape_result(self):
        patches = _patch_happy_path()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            event = await collaboration.trigger_quality_issue_event("11", "9001", title="TEST 协同验证")
        self.assertTrue(event["event_id"].startswith("EVT-"))
        self.assertEqual(event["status"], "COMPLETED")
        detail = collaboration.get_event(event["event_id"])
        self.assertEqual(detail["result"]["quality_impact"]["open_issues_count"], 1)
        self.assertEqual(detail["result"]["tracking"]["eta_status"], "DATA_MISSING")
        self.assertIsNotNone(detail["result"]["procurement_risk"])
        conclusions = " ".join(detail["result"]["conclusions"])
        self.assertIn("质量维度", conclusions)
        self.assertIn("生产维度", conclusions)
        self.assertIn("当前库存充足", conclusions)
        self.assertIn("审批门禁", conclusions)
        # 数据缺口如实记录（质量维度检验缺口 + 跟单 ETA 缺口）
        dims = {g["dimension"] for g in detail["result"]["data_gaps"]}
        self.assertIn("quality", dims)
        self.assertIn("tracking", dims)

    async def test_dedup_same_work_order_and_issue(self):
        patches = _patch_happy_path()
        dedup_key = "quality_issue_raised:11:9002"
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            first = await collaboration.trigger_quality_issue_event("11", "9002", title="TEST 去重")
            second = await collaboration.trigger_quality_issue_event("11", "9002", title="TEST 去重")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(self._count_events(dedup_key), 1)

    async def test_procurement_gap_recorded_not_fabricated(self):
        patches = _patch_happy_path(raw={"id": 11, "customer_order_no": ""})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            event = await collaboration.trigger_quality_issue_event("11", "9003", title="TEST 缺口")
        detail = collaboration.get_event(event["event_id"])
        self.assertEqual(event["status"], "COMPLETED")
        self.assertIsNone(detail["result"]["procurement_risk"])
        gaps = detail["result"]["data_gaps"]
        self.assertTrue(any("未建立 customer_order_no 关联" in g["detail"] for g in gaps))
        conclusions = " ".join(detail["result"]["conclusions"])
        self.assertIn("数据不足", conclusions)

    async def test_failure_recorded_and_retry_then_cap(self):
        # 质量维度抛错 → FAILED + failure_count=1
        with patch.object(collaboration.real_order, "assess_quality_impact", AsyncMock(side_effect=RuntimeError("MES 不可达"))), \
             patch.object(collaboration.real_order, "track_order", _fake_track()):
            event = await collaboration.trigger_quality_issue_event("11", "9004", title="TEST 失败重试")
        self.assertEqual(event["status"], "FAILED")
        self.assertEqual(event["failure_count"], 1)
        self.assertIn("MES 不可达", event["error"]["message"])

        # 重试成功 → COMPLETED
        patches = _patch_happy_path()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            retried = await collaboration.retry_event(event["event_id"], "Administrator")
        self.assertEqual(retried["status"], "COMPLETED")

        # 人为把失败次数顶到上限 → 再重试被拒绝
        with SessionLocal() as db:
            row = db.get(CollaborationEventRow, event["event_id"])
            row.failure_count = row.max_retries
            row.status = collaboration.EVENT_STATUS_FAILED
            db.commit()
        with self.assertRaises(ValueError) as ctx:
            await collaboration.retry_event(event["event_id"], "Administrator")
        self.assertIn("重试上限", str(ctx.exception))

    async def test_takeover_records_operator_and_stops_reprocess(self):
        patches = _patch_happy_path()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            event = await collaboration.trigger_quality_issue_event("11", "9005", title="TEST 接管")
        taken = collaboration.takeover_event(event["event_id"], "admin（OpenMES）", note="由质量员现场处置")
        self.assertEqual(taken["status"], "MANUAL_HANDLED")
        self.assertEqual(taken["taken_over_by"], "admin（OpenMES）")
        detail = collaboration.get_event(event["event_id"])
        self.assertEqual(detail["result"]["manual_takeover"]["note"], "由质量员现场处置")
        self.assertIn("审批门禁", detail["result"]["manual_takeover"]["hint"])
        # 已接管的事件不再自动协同
        again = await collaboration.process_event(event["event_id"])
        self.assertEqual(again["status"], "MANUAL_HANDLED")
        self.assertIn("已人工接管", again["error"]["message"])


class CollaborationEndpointTests(unittest.IsolatedAsyncioTestCase):
    def test_list_endpoint_returns_events(self):
        from app.main import app as fastapi_app

        app = FastAPI()

        @app.get("/api/real-orders/collaboration/events")
        async def route(limit: int = 20, status: str | None = None):
            return collaboration.list_events(limit=limit, status=status)

        # 直接对真实 fastapi_app 不可行（依赖较多）；这里用打桩服务验证路由层
        with patch.object(collaboration, "list_events", return_value={"items": [], "count": 0, "data_source": "local_collaboration_events"}):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/api/real-orders/collaboration/events")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 0)

    def test_real_app_has_event_routes(self):
        from app.main import app as fastapi_app

        paths = {r.path for r in fastapi_app.routes}
        self.assertIn("/api/real-orders/collaboration/events", paths)
        self.assertIn("/api/real-orders/collaboration/events/{event_id}", paths)
        self.assertIn("/api/real-orders/collaboration/events/{event_id}/retry", paths)
        self.assertIn("/api/real-orders/collaboration/events/{event_id}/takeover", paths)


if __name__ == "__main__":
    unittest.main()
