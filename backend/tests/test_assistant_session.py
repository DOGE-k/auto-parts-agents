"""任务型协同问答：会话上下文、槽位追问与确定性指令测试（P0）。

覆盖用户确认的五条规则（2026-10-01）：
- 上下文沿用与回显；新值覆盖旧值；歧义先追问不猜测；
- "数量改成 3000" → 只读重新报价（不写 ERP、不审批），下游标记需重新确认；
- "选第二个方案" → supplier_options 展示顺序（1 开始）+ 稳定 option_id 快照，
  方案缺失/过期/顺序变化时要求重新选择；
- 缺必填槽位先追问（缺什么/为什么/补充后调用哪个智能体），不调用 LLM；
- 保留策略：消息与协调者运行记录按保留期过期，审批/审计记录永久。

全部使用隔离测试库与假协调者，不触真实系统。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.persistence.database import SessionLocal
from app.persistence.models import (
    AssistantMessageRow,
    AssistantSessionRow,
    BusinessTaskRow,
    RealAgentRunRow,
)
from app.services import assistant_session as session_module
from app.services import real_order
from app.services.assistant_context import (
    extract_signals,
    format_context_echo,
    merge_context,
    missing_slots,
    validate_ordinal_selection,
)


class _FakeCoordinator:
    """记录调用参数并返回固定结果的假协调者。"""

    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self.result = result or {}
        self.error = error
        self.closed = False

    async def ask(self, question, context=None, on_step=None, run_meta=None):
        self.calls.append({"question": question, "context": context, "run_meta": run_meta})
        if self.error is not None:
            raise self.error
        return dict(self.result)

    async def aclose(self):
        self.closed = True


def _seed_task(entity_context: dict) -> str:
    """直接播种一个带实体上下文的会话+任务，返回 session_id。"""
    from uuid import uuid4

    session_id = f"ASST-{uuid4().hex[:12].upper()}"
    task_id = f"TASK-{uuid4().hex[:12].upper()}"
    with SessionLocal() as db:
        db.add(AssistantSessionRow(session_id=session_id, title="seed", business_task_id=task_id))
        db.add(BusinessTaskRow(
            task_id=task_id, title="seed", entity_context=entity_context,
            stale_downstream=[],
        ))
        db.commit()
    return session_id


def _fail_if_called(name):
    async def _raise(*args, **kwargs):
        raise AssertionError(f"确定性路径不得调用 {name}")

    return _raise


class SignalExtractionTests(unittest.TestCase):
    def test_quantity_change_phrases(self):
        for text in ("数量改成 3000", "数量改为3000", "把数量变更为 3,000 件", "改成 3000 件"):
            signals = extract_signals(text)
            self.assertEqual(signals.quantity_change, 3000, text)

    def test_ordinal_selection_cn_and_digit(self):
        self.assertEqual(extract_signals("选第二个方案").ordinal_selection, 2)
        self.assertEqual(extract_signals("选第 3 个采购方案").ordinal_selection, 3)

    def test_entity_codes_extracted(self):
        signals = extract_signals("SAL-ORD-2026-00023 什么时候能做完？WO-2026-001 呢？")
        self.assertEqual(signals.erp_order_id, "SAL-ORD-2026-00023")
        # 同一条消息两个编号时过滤掉歧义工单号（不猜测）
        self.assertEqual(signals.work_order_no, "")

    def test_intent_and_missing_slots(self):
        signals = extract_signals("BD-2401 500 件多少钱？")
        merged, _ = merge_context({}, signals)
        missing = missing_slots("quotation", merged)
        slots = [m["slot"] for m in missing]
        self.assertIn("customer_id", slots)
        self.assertNotIn("item_code", slots)
        self.assertNotIn("quantity", slots)


class ContextEchoTests(unittest.TestCase):
    def test_echo_lists_carried_fields(self):
        echo = format_context_echo({"customer_id": "上汽集团", "item_code": "BD-2401", "quantity": 2000})
        self.assertIn("客户=上汽集团", echo)
        self.assertIn("物料=BD-2401", echo)
        self.assertIn("数量=2000", echo)
        self.assertIn("如果需要修改请直接说明", echo)

    def test_merge_new_value_overrides_old(self):
        merged, updates = merge_context({"quantity": 500}, extract_signals("数量改成 3000"))
        self.assertEqual(merged["quantity"], 3000)
        self.assertIn("quantity", updates)

    def test_validate_ordinal_selection_order_changed(self):
        active = {"plan_id": "PROC-1", "supplier_options": [
            {"option_id": "OPT-1", "supplier_name": "A", "total_cost": 100},
            {"option_id": "OPT-2", "supplier_name": "B", "total_cost": 200},
        ]}
        current = {"plan_id": "PROC-1", "supplier_options": [
            {"option_id": "OPT-2", "supplier_name": "B", "total_cost": 200},
            {"option_id": "OPT-1", "supplier_name": "A", "total_cost": 100},
        ]}
        chosen, error = validate_ordinal_selection(1, active, current)
        self.assertIsNone(chosen)
        self.assertIn("不一致", error)

    def test_validate_ordinal_selection_missing_plan(self):
        active = {"plan_id": "PROC-1", "supplier_options": [{"option_id": "OPT-1"}]}
        chosen, error = validate_ordinal_selection(1, active, None)
        self.assertIsNone(chosen)
        self.assertIn("已不存在", error)

    def test_validate_ordinal_selection_lead_time_changed_requires_reselect(self):
        """缺口②回归：交期/覆盖变化时快照校验必须要求重新选择。"""
        base = {"option_id": "OPT-1", "supplier_name": "上海铸锻厂", "total_cost": 100,
                "currency": "CNY", "lead_time_days": 15, "lead_time_source": "item_lead_time_days",
                "coverage": "3/3"}
        active = {"plan_id": "PROC-1", "supplier_options": [dict(base)]}
        current_changed_lead = {"plan_id": "PROC-1", "supplier_options": [{**base, "lead_time_days": 7}]}
        chosen, error = validate_ordinal_selection(1, active, current_changed_lead)
        self.assertIsNone(chosen)
        self.assertIn("不一致", error)
        # 覆盖变化同样触发
        current_changed_cov = {"plan_id": "PROC-1", "supplier_options": [{**base, "coverage": "2/3"}]}
        chosen2, error2 = validate_ordinal_selection(1, active, current_changed_cov)
        self.assertIsNone(chosen2)
        self.assertIn("不一致", error2)
        # 完全一致时通过
        chosen3, error3 = validate_ordinal_selection(1, active, {"plan_id": "PROC-1", "supplier_options": [dict(base)]})
        self.assertIsNotNone(chosen3)
        self.assertIsNone(error3)

    def test_validate_ordinal_selection_out_of_range(self):
        active = {"plan_id": "PROC-1", "supplier_options": [{"option_id": "OPT-1"}]}
        chosen, error = validate_ordinal_selection(2, active, active)
        self.assertIsNone(chosen)
        self.assertIn("超出范围", error)


class QuantityChangeFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_quantity_change_reuses_context_marks_stale_and_never_writes(self):
        from unittest.mock import AsyncMock

        session_id = _seed_task({
            "customer_id": "上汽集团",
            "item_code": "BD-2401",
            "delivery_date": "2026-11-20",
            "quantity": 500,
            "quotation_id": "QUO-OLD0000001",
            "plan_id": "PROC-OLD000001",
            "erp_draft_id": "SAL-ORD-2026-00025",
            "work_order_no": "WO-SO-2026-00025",
        })
        analysis = {
            "quotation_id": "QUO-NEW0000001",
            "status": "ok",
            "unit_price": 85.0,
            "total_price": 255000,
            "currency": "CNY",
            "delivery_estimate": {"conclusion": "17 天"},
        }
        with patch.object(real_order, "analyze_quotation", AsyncMock(return_value=analysis)) as mock_analyze, \
             patch.object(real_order, "approve_quotation", _fail_if_called("approve_quotation")), \
             patch.object(real_order, "create_erp_sales_order_from_quotation", _fail_if_called("create_so_draft")), \
             patch.object(real_order, "dispatch_work_order_to_openmes", _fail_if_called("dispatch")):
            result = await session_module.handle_ask(
                "数量改成 3000", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=session_id,
            )

        self.assertEqual(result["handled_by"], "deterministic_quantity_change")
        mock_analyze.assert_awaited_once()
        kwargs = mock_analyze.await_args.kwargs
        self.assertEqual(mock_analyze.await_args.args, ("上汽集团", "BD-2401", 3000))
        self.assertEqual(kwargs.get("delivery_date"), "2026-11-20")
        self.assertIn("没有", result["answer"])
        self.assertIn("需要重新确认", result["answer"])
        stale_types = {s["type"] for s in result["stale_downstream"]}
        self.assertEqual(stale_types, {"quotation", "procurement_plan", "erp_so_draft", "mes_work_order"})
        # 新报价进入任务上下文，状态进入待重新确认
        with SessionLocal() as db:
            row = db.get(BusinessTaskRow, result["business_task_id"])
            self.assertEqual(row.entity_context["quantity"], 3000)
            self.assertEqual(row.entity_context["quotation_id"], "QUO-NEW0000001")
            self.assertEqual(row.status, "RECONFIRMATION_REQUIRED")

    async def test_quantity_change_resolves_carried_quotation_without_llm(self):
        """客户/物料缺失时可从任务沿用的已保存报价记录确定性解析（真实数据）。"""
        from unittest.mock import AsyncMock

        session_id = _seed_task({
            "quantity": 500,
            "quotation_id": "QUO-CARRIED0001",
            "erp_draft_id": "SAL-ORD-2026-00030",
        })
        carried = {
            "customer": {"customer_id": "上汽集团"},
            "item": {"item_id": "BD-2401"},
            "delivery_date": "2026-10-31",
            "quantity": 500,
        }
        analysis = {
            "quotation_id": "QUO-NEWFROMCARR",
            "status": "ok",
            "unit_price": 85.0,
            "total_price": 255000,
            "currency": "CNY",
        }
        with patch.object(real_order, "get_quotation", return_value=carried), \
             patch.object(real_order, "analyze_quotation", AsyncMock(return_value=analysis)) as mock_analyze:
            result = await session_module.handle_ask(
                "数量改成 3000", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=session_id,
            )
        self.assertEqual(result["handled_by"], "deterministic_quantity_change")
        self.assertEqual(mock_analyze.await_args.args, ("上汽集团", "BD-2401", 3000))
        self.assertEqual(mock_analyze.await_args.kwargs.get("delivery_date"), "2026-10-31")
        # 沿用报价被标记为需重新确认
        self.assertIn("quotation", {s["type"] for s in result["stale_downstream"]})

    async def test_quantity_change_without_customer_asks_back(self):
        result = await session_module.handle_ask(
            "BD-2401 数量改成 3000", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
        )
        self.assertTrue(result["needs_input"])
        self.assertEqual(result["handled_by"], "clarify")

    async def test_quantity_change_data_missing_reported_honestly(self):
        from unittest.mock import AsyncMock

        session_id = _seed_task({"customer_id": "上汽集团", "item_code": "BD-2401"})
        analysis = {"status": "DATA_MISSING", "missing_data": [{"detail": "缺少 Selling 价格"}]}
        with patch.object(real_order, "analyze_quotation", AsyncMock(return_value=analysis)):
            result = await session_module.handle_ask(
                "数量改成 3000", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=session_id,
            )
        self.assertIn("数据缺失", result["answer"])
        self.assertIn("缺少 Selling 价格", result["answer"])


class SelectionFlowTests(unittest.IsolatedAsyncioTestCase):
    def _seed_task_with_plan(self):
        """先跑一次带方案结果的协调问答，让任务持有 active_plan。"""
        coordinator = _FakeCoordinator({
            "answer": "方案如下",
            # 真实协调者链会经报价分析参数把客户/物料/数量带入任务上下文；
            # 缺了它们，后续"数量改成 N"会（正确地）走追问而不是 stale 标记。
            "call_chain": [
                {"seq": 1, "skill_id": "quotation.analyze_real", "status": "ok",
                 "arguments": {"customer_id": "上汽集团", "item_code": "BD-2401",
                               "quantity": 2000, "delivery_date": "2026-10-31"}},
            ],
            "rounds": 1,
            "tool_count": 1,
            "coordination_run_id": "RUN-COORD-TESTPLAN",
            "proposal_options": {
                "plan_id": "PROC-TESTPLAN01",
                "quotation_id": "QUO-TESTPLAN001",
                "supplier_options": [
                    {"option_id": "OPT-1", "supplier_name": "上海铸锻厂", "total_cost": 39080, "currency": "CNY", "lead_time_days": 15, "lead_time_source": "item_lead_time_days"},
                    {"option_id": "OPT-2", "supplier_name": "宁波紧固件", "total_cost": 41000, "currency": "CNY", "lead_time_days": 7, "lead_time_source": "item_lead_time_days"},
                ],
                "shortage": {"shortage_count": 2},
                "recommendation": "OPT-1",
            },
        })

        async def _run():
            return await session_module.handle_ask(
                "SAL-ORD-2026-00023 缺料了怎么办", lambda: coordinator,
            ), coordinator

        return _run

    async def test_select_second_option_by_display_order(self):
        first, _ = await self._seed_task_with_plan()()
        plan_id = first["business_task_id"]
        plan_row = None
        with SessionLocal() as db:
            plan_row = db.get(BusinessTaskRow, plan_id)
            stored_plan = dict(plan_row.active_plan or {})
        with patch.object(real_order, "get_procurement_plan", return_value=stored_plan):
            result = await session_module.handle_ask(
                "选第二个方案", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=first["session_id"],
            )
        self.assertEqual(result["handled_by"], "deterministic_selection")
        self.assertEqual(result["pending_selection"]["option_id"], "OPT-2")
        self.assertEqual(result["pending_selection"]["option_snapshot"]["supplier_name"], "宁波紧固件")
        self.assertIn("人工审批", result["answer"])
        # 同一会话/任务：第二次问答沿用了第一次的任务
        self.assertEqual(result["business_task_id"], plan_id)

    async def test_select_without_plan_asks_to_regenerate(self):
        # 全新会话没有任何方案快照：如实提示先问一次缺料方案
        result = await session_module.handle_ask(
            "选第二个方案", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
        )
        self.assertTrue(result["needs_input"])
        self.assertIn("没有可选择的供应商方案", result["answer"])

    async def test_select_with_expired_plan_asks_to_rechoose(self):
        first, _ = await self._seed_task_with_plan()()
        with patch.object(real_order, "get_procurement_plan", return_value=None):
            result = await session_module.handle_ask(
                "选第二个方案", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=first["session_id"],
            )
        self.assertTrue(result["needs_input"])
        self.assertIn("已不存在", result["answer"])

    async def test_select_refused_after_quantity_change_marks_plan_stale(self):
        """缺口①连续回归：缺料方案 → 数量变更 → 选第二个方案必须被拒绝。"""
        first, _ = await self._seed_task_with_plan()()
        # 数量变更（真实走 stale 标记路径）：任务上下文已带 plan_id=PROC-TESTPLAN01
        from unittest.mock import AsyncMock as _AM

        with patch.object(real_order, "analyze_quotation", _AM(return_value={
            "quotation_id": "QUO-NEWQTY00001", "status": "ok",
            "unit_price": 85.0, "total_price": 255000, "currency": "CNY",
        })):
            qty_result = await session_module.handle_ask(
                "数量改成 3000", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=first["session_id"],
            )
        stale_types = {s["type"] for s in qty_result["stale_downstream"]}
        self.assertIn("procurement_plan", stale_types)
        # 旧方案仍存在且选项一致——但必须因"基于旧数量"被拒绝
        with SessionLocal() as db:
            row = db.get(BusinessTaskRow, first["business_task_id"])
            stored_plan = dict(row.active_plan or {})
        with patch.object(real_order, "get_procurement_plan", return_value=stored_plan):
            result = await session_module.handle_ask(
                "选第二个方案", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
                session_id=first["session_id"],
            )
        self.assertTrue(result["needs_input"])
        self.assertIn("旧数量", result["answer"])
        self.assertNotIn("pending_selection", result)
        self.assertIn("重新发起", result["answer"])


class ClarifyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_quotation_without_customer_asks_without_llm(self):
        def _boom():
            raise AssertionError("槽位追问不得调用 LLM")

        result = await session_module.handle_ask("BD-2401 500 件多少钱？", _boom)
        self.assertTrue(result["needs_input"])
        self.assertEqual(result["handled_by"], "clarify")
        slots = [m["slot"] for m in result["missing_slots"]]
        self.assertIn("customer_id", slots)
        self.assertIn("报价智能体", result["answer"])

    async def test_entity_context_updated_from_call_chain(self):
        coordinator = _FakeCoordinator({
            "answer": "完成率 90%",
            "call_chain": [
                {"seq": 1, "skill_id": "tracking.lookup_order_link", "status": "ok",
                 "arguments": {"erp_order_id": "SAL-ORD-2026-00023"}},
                {"seq": 2, "skill_id": "tracking.track_real", "status": "ok",
                 "arguments": {"work_order_id": "9"}},
            ],
            "rounds": 2,
            "tool_count": 2,
            "coordination_run_id": "RUN-COORD-CHAIN",
        })
        result = await session_module.handle_ask(
            "SAL-ORD-2026-00023 什么时候能做完？", lambda: coordinator,
        )
        with SessionLocal() as db:
            row = db.get(BusinessTaskRow, result["business_task_id"])
            self.assertEqual(row.entity_context.get("erp_order_id"), "SAL-ORD-2026-00023")
            self.assertEqual(row.entity_context.get("work_order_id"), "9")
        # 协调者运行记录带会话关联与保留期
        self.assertEqual(coordinator.calls[0]["run_meta"]["business_task_id"], result["business_task_id"])


class RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_messages_expire_and_purge_keeps_audit(self):
        result = await session_module.handle_ask(
            "BD-2401 500 件多少钱？", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
        )
        session_id = result["session_id"]
        with SessionLocal() as db:
            msgs = db.query(AssistantMessageRow).filter_by(session_id=session_id).all()
            self.assertEqual([m.role for m in msgs], ["user", "assistant"])
            self.assertTrue(all(m.expires_at is not None for m in msgs))

        # 构造一条已过期的旧消息 + 一条永久审计记录
        past = datetime.now(timezone.utc) - timedelta(days=120)
        with SessionLocal() as db:
            db.add(AssistantMessageRow(
                session_id="ASST-EXPIRED", role="user", content="旧消息", expires_at=past,
            ))
            db.add(RealAgentRunRow(
                run_id="RUN-EXPIRED-TEST", agent_type="coordinator", operation="ask",
                result_status="ok", result_summary="", input_json={}, started_at=past,
                expires_at=past,
            ))
            db.add(RealAgentRunRow(
                run_id="RUN-PERMANENT-TEST", agent_type="approval", operation="approve_quotation",
                result_status="ok", result_summary="", input_json={}, started_at=past,
                expires_at=None,
            ))
            db.commit()

        removed = session_module.purge_expired_assistant_data()
        self.assertGreaterEqual(removed, 2)
        with SessionLocal() as db:
            self.assertIsNone(db.query(AssistantMessageRow).filter_by(session_id="ASST-EXPIRED").first())
            self.assertIsNone(db.get(RealAgentRunRow, "RUN-EXPIRED-TEST"))
            self.assertIsNotNone(db.get(RealAgentRunRow, "RUN-PERMANENT-TEST"))
            # 业务任务与当次会话消息不受影响
            self.assertIsNotNone(db.get(BusinessTaskRow, result["business_task_id"]))

    async def test_retention_days_configurable_forever(self):
        with patch.dict("os.environ", {"ASSISTANT_RETENTION_DAYS": "0"}):
            self.assertIsNone(__import__("app.services.assistant_context", fromlist=["assistant_retention_days"]).assistant_retention_days())
        with patch.dict("os.environ", {"ASSISTANT_RETENTION_DAYS": "7"}):
            self.assertEqual(__import__("app.services.assistant_context", fromlist=["assistant_retention_days"]).assistant_retention_days(), 7)


class EndToEndSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_question_reuses_task_and_echoes_context(self):
        fake = _FakeCoordinator({"answer": "回答", "call_chain": [], "rounds": 1, "tool_count": 0, "coordination_run_id": "RUN-COORD-E2E"})
        first = await session_module.handle_ask(
            "SAL-ORD-2026-00023 什么时候能做完？", lambda: fake,
        )
        second = await session_module.handle_ask(
            "数量改成 3000",
            lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")),
            session_id=first["session_id"],
        )
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["business_task_id"], first["business_task_id"])
        # 上一轮的订单号被沿用（调用链参数写入实体上下文），回显中可见
        self.assertIn("SAL-ORD-2026-00023", second["applied_context"])
        with SessionLocal() as db:
            msgs = db.query(AssistantMessageRow).filter_by(session_id=first["session_id"]).order_by(AssistantMessageRow.id).all()
            self.assertEqual(len(msgs), 4)  # 2 问 2 答
            contents = " ".join(m.content for m in msgs)
            self.assertNotIn("Bearer", contents)  # 凭据从不进入会话持久化内容


if __name__ == "__main__":
    unittest.main()
