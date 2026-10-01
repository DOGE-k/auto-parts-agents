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

    async def test_retrigger_after_manual_takeover_creates_new_event(self):
        """缺口③回归：人工接管后同一业务事实再次触发 → 新事件，不撞唯一约束。"""
        patches = _patch_happy_path()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            first = await collaboration.trigger_quality_issue_event("11", "9006", title="TEST 接管后重触发")
        collaboration.takeover_event(first["event_id"], "admin", note="人工处置中")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            second = await collaboration.trigger_quality_issue_event("11", "9006", title="TEST 接管后重触发")
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(second["status"], "COMPLETED")
        self.assertFalse(second.get("deduplicated"))
        # 重触发事件使用 #N 后缀键；原事件保留 MANUAL_HANDLED 留痕
        detail = collaboration.get_event(second["event_id"])
        self.assertTrue(detail["dedup_key"].endswith("#2"))
        self.assertEqual(collaboration.get_event(first["event_id"])["status"], "MANUAL_HANDLED")


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


class ShortageEventTests(unittest.IsolatedAsyncioTestCase):
    """缺料事件：采购分析发现真实缺料时触发（事实触发，无阈值）。"""

    def _shortage_plan(self, *, recommended="OPT-1", quotation_id="QUO-COSTTEST0001"):
        # quotation_id 参与去重键：各测试独立报价号，避免 unittest 字母序执行时互相去重
        return {
            "plan_id": "PROC-SHORTTEST01",
            "quotation_id": quotation_id,
            "quotation_status": "APPROVED",
            "recommended_option_id": recommended,
            "net_requirement": {
                "has_shortage": True,
                "shortage_count": 2,
                "shortage_items": [
                    {"item_id": "CI-RAW", "net_requirement": "1200", "warehouse_qty": "800", "purchase_qty": "1200", "price_status": "real_buying_price"},
                    {"item_id": "M10-BOLT", "net_requirement": "3000", "warehouse_qty": "5000", "purchase_qty": "3000", "price_status": "real_buying_price"},
                ],
            },
            "supplier_options": [
                {"option_id": "OPT-1", "supplier_name": "上海铸锻厂", "lead_time_days": 15, "coverage": "2/2", "total_cost": 39080, "currency": "CNY"},
            ],
        }

    async def test_shortage_event_with_cost_and_delivery(self):
        # 每个测试独立报价号：去重键 = 报价 + 缺料签名，unittest 按方法名字母序
        # 执行，共用报价号会触发去重互相污染（d < g < w）。
        with patch.object(collaboration.real_order, "get_quotation", return_value={"erp_draft_id": "SAL-ORD-2026-00023"}), \
             patch("app.services.order_linkage.get_order_mes_link", AsyncMock(return_value={
                 "status": "LINKED",
                 "association": {"links": [{"mes_record": {"record_id": "9"}}]},
             })), \
             patch.object(collaboration.real_order, "assess_cost_impact", AsyncMock(return_value={
                 "plan_id": "PROC-SHORTTEST01", "option_id": "OPT-1", "supplier_name": "上海铸锻厂",
                 "material_cost_delta": "2220.00", "currency": "CNY",
                 "calculation_basis": "supplier_specific_price_vs_standard",
             })), \
             patch.object(collaboration.real_order, "assess_delivery_impact", AsyncMock(return_value={
                 "work_order_id": "9", "work_order_no": "TEST_WO", "due_date": "2026-10-31",
                 "material_ready_date": "2026-10-16", "buffer_days": 15, "verdict": "arrival_in_time",
                 "conclusion": "物料 2026-10-16 到位，早于交期 15 天，不延期",
                 "completion_rate": 90.0,
             })):
            event = await collaboration.trigger_shortage_event(self._shortage_plan(quotation_id="QUO-COSTTEST0001"))
        self.assertTrue(event["event_id"].startswith("EVT-"))
        self.assertEqual(event["status"], "COMPLETED")
        detail = collaboration.get_event(event["event_id"])
        result = detail["result"]
        self.assertEqual(result["cost_assessment"]["material_cost_delta"], "2220.00")
        self.assertEqual(result["delivery_assessment"]["verdict"], "arrival_in_time")
        conclusions = " ".join(result["conclusions"])
        self.assertIn("缺料 2 项", conclusions)
        self.assertIn("成本维度", conclusions)
        self.assertIn("交期维度", conclusions)
        self.assertIn("审批门禁", conclusions)

    async def test_shortage_event_dedup_on_same_signature(self):
        with patch.object(collaboration.real_order, "get_quotation", return_value={}), \
             patch("app.services.order_linkage.get_order_mes_link", AsyncMock(return_value={"status": "NOT_LINKED"})), \
             patch.object(collaboration.real_order, "assess_cost_impact", AsyncMock(return_value={"status": "DATA_MISSING"})):
            first = await collaboration.trigger_shortage_event(self._shortage_plan(quotation_id="QUO-DEDUPTEST001"))
            second = await collaboration.trigger_shortage_event(self._shortage_plan(quotation_id="QUO-DEDUPTEST001"))
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["event_id"], second["event_id"])
        # 数量变化 → 新签名 → 新事件
        changed = self._shortage_plan(quotation_id="QUO-DEDUPTEST001")
        changed["net_requirement"]["shortage_items"][0]["net_requirement"] = "1500"
        third = await collaboration.trigger_shortage_event(changed)
        self.assertNotEqual(first["event_id"], third["event_id"])

    async def test_shortage_event_gaps_when_no_recommendation_and_no_link(self):
        plan = self._shortage_plan(recommended="", quotation_id="QUO-GAPTEST00001")
        with patch.object(collaboration.real_order, "get_quotation", return_value={}), \
             patch("app.services.order_linkage.get_order_mes_link", AsyncMock(return_value={"status": "NOT_LINKED"})):
            event = await collaboration.trigger_shortage_event(plan)
        detail = collaboration.get_event(event["event_id"])
        self.assertEqual(event["status"], "COMPLETED")
        gaps = detail["result"]["data_gaps"]
        self.assertTrue(any("未产出可推荐供应商" in g["detail"] for g in gaps))
        # 报价无 erp_draft_id → 交期维度如实记缺口（无法反查关联工单）
        self.assertTrue(any("尚未创建 ERP 销售订单草稿" in g["detail"] for g in gaps))
        conclusions = " ".join(detail["result"]["conclusions"])
        self.assertIn("推荐 无", conclusions)

    async def test_no_shortage_plan_skipped(self):
        plan = self._shortage_plan()
        plan["net_requirement"]["has_shortage"] = False
        result = await collaboration.trigger_shortage_event(plan)
        self.assertIsNone(result)


class OverdueEventTests(unittest.IsolatedAsyncioTestCase):
    """延期事件：事实型触发（已过交期且未完成），无业务阈值。"""

    def _overdue_track(self, *, due="2026-09-01T00:00:00.000000Z", completion=30.0, status="IN_PROGRESS"):
        return {
            "work_order_id": "2", "work_order_no": "WO-2026-001", "status": status,
            "quantity": "500.00", "completed_qty": "150.00", "completion_rate": completion,
            "due_date": due, "eta_status": "DATA_MISSING",
        }

    async def test_overdue_triggers_event_with_facts(self):
        event = await collaboration.trigger_overdue_event_if_needed(self._overdue_track())
        self.assertIsNotNone(event)
        self.assertEqual(event["status"], "COMPLETED")
        detail = collaboration.get_event(event["event_id"])
        self.assertGreater(detail["result"]["overdue_days"], 0)
        conclusions = " ".join(detail["result"]["conclusions"])
        self.assertIn("已过交期", conclusions)
        self.assertIn("30.0%", conclusions)
        self.assertIn("不代承诺客户", conclusions)
        # 同日去重
        again = await collaboration.trigger_overdue_event_if_needed(self._overdue_track())
        self.assertIsNone(again)

    async def test_not_overdue_or_done_does_not_trigger(self):
        self.assertIsNone(await collaboration.trigger_overdue_event_if_needed(
            self._overdue_track(due="2099-01-01T00:00:00.000000Z")))  # 未到期
        self.assertIsNone(await collaboration.trigger_overdue_event_if_needed(
            self._overdue_track(status="DONE")))  # 已完成
        self.assertIsNone(await collaboration.trigger_overdue_event_if_needed(
            self._overdue_track(completion=100.0)))  # 完成率 100%
        self.assertIsNone(await collaboration.trigger_overdue_event_if_needed(
            self._overdue_track(due="")))  # 无交期数据如实跳过
