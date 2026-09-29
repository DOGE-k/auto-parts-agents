"""阶段六方案化协同测试：成本/交期影响评估、订单反查报价、方案数据汇集。

全部使用隔离测试库与假适配器（patch），不触真实 ERP/MES。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.persistence.database import SessionLocal
from app.persistence.models import RealQuotationRow, RealProcurementPlanRow
from app.services import real_order
from app.services.coordinator import BusinessCoordinator, REAL_SKILL_TOOLS


def _persist_quotation(quotation_id: str, erp_draft_id: str, total_price: str, quantity: int) -> None:
    with SessionLocal() as session:
        session.merge(RealQuotationRow(
            quotation_id=quotation_id,
            data_json={
                "quotation_id": quotation_id,
                "status": "APPROVED",
                "quantity": quantity,
                "unit_price": str(float(total_price) / quantity),
                "total_price": total_price,
                "currency": "CNY",
                "item": {"item_id": "BD-2401"},
                "erp_draft_id": erp_draft_id,
                "created_at": "2026-09-29T10:00:00+00:00",
            },
            status="APPROVED",
            adapter_mode="real",
            customer_id="上汽集团",
            item_code="BD-2401",
            quantity=quantity,
        ))
        session.commit()


def _persist_plan(plan_id: str, quotation_id: str, *, baseline: str, options: list[dict],
                  shortage_items: list[dict] | None = None) -> None:
    with SessionLocal() as session:
        session.merge(RealProcurementPlanRow(
            plan_id=plan_id,
            quotation_id=quotation_id,
            status="PENDING_APPROVAL",
            adapter_mode="real",
            data_json={
                "plan_id": plan_id,
                "quotation_id": quotation_id,
                "status": "PENDING_APPROVAL",
                "net_requirement": {
                    "has_shortage": True,
                    "shortage_count": len(shortage_items) if shortage_items else 1,
                    "total_estimated_cost": baseline,
                    "shortage_items": shortage_items or [{"item_id": "CI-RAW", "net_requirement": "1200"}],
                },
                "supplier_options": options,
                "recommended_option_id": options[0]["option_id"] if options else None,
                "created_at": "2026-09-29T10:00:00+00:00",
            },
        ))
        session.commit()


def _option(option_id: str, total_cost: str, complete: bool = True) -> dict:
    return {
        "option_id": option_id,
        "supplier_id": "SUP-B",
        "supplier_name": "B 供应商",
        "lead_time_days": 2,
        "covers_all_shortage_items": True,
        "coverage": "1/1",
        "total_cost": total_cost,
        "total_cost_complete": complete,
        "currency": "CNY",
        "is_recommended": False,
        "recommendation_reason": "",
        "items": [{"item_id": "CI-RAW", "unit_price": "12.5", "price_record": "rec-1", "line_total": total_cost}],
    }


class FindWorkOrderByNoTests(unittest.IsolatedAsyncioTestCase):
    """工单可见编号到数字 ID 的桥接必须精确匹配且不猜 ID。"""

    async def test_exact_number_returns_numeric_id(self):
        class _FakeMES:
            async def get_work_orders_strict(self, scope):
                return [
                    {"work_order_id": "2", "work_order_no": "WO-2026-001", "status": "ACCEPTED", "authority": "OpenMES"},
                    {"work_order_id": "3", "work_order_no": "WO-2026-002", "status": "BLOCKED", "authority": "OpenMES"},
                ]

        with patch.object(real_order, "get_mes_adapter", return_value=_FakeMES()):
            result = await real_order.find_work_order_by_no(" WO-2026-001 ")

        self.assertTrue(result["found"])
        self.assertEqual(result["work_order_id"], "2")
        self.assertEqual(result["work_order_no"], "WO-2026-001")

    async def test_unknown_number_returns_suggestions_without_id_guess(self):
        class _FakeMES:
            async def get_work_orders_strict(self, scope):
                return [{"work_order_id": "2", "work_order_no": "WO-2026-001", "customer_order_no": "SAL-1"}]

        with patch.object(real_order, "get_mes_adapter", return_value=_FakeMES()):
            result = await real_order.find_work_order_by_no("WO-2099-999")

        self.assertFalse(result["found"])
        self.assertEqual(result["work_order_no"], "WO-2099-999")
        self.assertEqual(result["existing_work_orders"][0]["work_order_no"], "WO-2026-001")
        self.assertNotIn("work_order_id", result)


class ApprovalIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_quotation_approval_reuses_original_record(self):
        _persist_quotation("QUO-IDEMP", "SAL-IDEMP", "850.00", 10)

        first = await real_order.approve_quotation("QUO-IDEMP", True, "sales_manager")
        second = await real_order.approve_quotation("QUO-IDEMP", True, "sales_manager")

        self.assertEqual(first["approval_id"], second["approval_id"])
        self.assertTrue(second["idempotent_replay"])

    async def test_repeated_procurement_approval_and_po_draft_reuse_records(self):
        _persist_plan(
            "PROC-IDEMP",
            "QUO-IDEMP-2",
            baseline="100.00",
            options=[_option("OPT-1", "120.00")],
        )

        first = await real_order.approve_procurement_plan("PROC-IDEMP", "OPT-1", True, "purchase_manager")
        second = await real_order.approve_procurement_plan("PROC-IDEMP", "OPT-1", True, "purchase_manager")
        self.assertEqual(first["approval_id"], second["approval_id"])
        self.assertTrue(second["idempotent_replay"])

        plan = real_order.get_procurement_plan("PROC-IDEMP")
        assert plan is not None
        plan["status"] = "PO_DRAFT_CREATED"
        plan["po_draft_id"] = "PUR-ORD-IDEMP"
        plan["po_draft"] = {"status": "DRAFT", "draft_id": "PUR-ORD-IDEMP", "authority": "ERPNext", "read_back_verified": True}
        real_order.save_procurement_plan(plan)

        with patch.object(real_order, "get_erp_adapter", side_effect=AssertionError("idempotent replay must not call ERP")):
            replay = await real_order.create_erp_purchase_order_from_plan(
                "PROC-IDEMP", first["approval_id"], "purchase_manager"
            )
        self.assertTrue(replay["success"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["draft"]["draft_id"], "PUR-ORD-IDEMP")


class AssessCostImpactTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _persist_quotation("QUO-CI", "SAL-ORD-2026-00023", "17000.00", 2000)
        _persist_plan("PROC-CI", "QUO-CI", baseline="1000.00", options=[
            _option("OPT-1", "1060.00"),
        ])

    async def test_cost_delta_and_margin_from_real_records(self):
        result = await real_order.assess_cost_impact("PROC-CI", "OPT-1")
        self.assertEqual(result["status"], "ok")
        # 方案比基准贵 60：1060 - 1000
        self.assertEqual(result["material_cost_delta"], "60.00")
        # 单件加价：60 / 2000 = 0.03
        self.assertEqual(result["per_unit_surcharge"], "0.03")
        # 材料毛利：17000-1000=16000 → 17000-1060=15940
        self.assertEqual(result["material_margin_before"], "16000.00")
        self.assertEqual(result["material_margin_after"], "15940.00")
        # 证据链包含真实价格记录与报价编号
        record_ids = [e["record_id"] for e in result["evidence"]]
        self.assertIn("rec-1", record_ids)
        self.assertIn("QUO-CI", record_ids)
        self.assertIn("材料成本口径", result["calculation_basis"])

    async def test_incomplete_price_returns_data_missing(self):
        _persist_plan("PROC-CI2", "QUO-CI", baseline="1000.00", options=[
            _option("OPT-1", "", complete=False),
        ])
        result = await real_order.assess_cost_impact("PROC-CI2", "OPT-1")
        self.assertEqual(result["status"], "DATA_MISSING")
        self.assertTrue(any(f["field"] == "option_material_cost" for f in result["missing_fields"]))

    async def test_missing_plan_or_option_reports_error(self):
        result = await real_order.assess_cost_impact("PROC-NOPE", "OPT-1")
        self.assertEqual(result["status"], "ERROR")
        result2 = await real_order.assess_cost_impact("PROC-CI", "OPT-99")
        self.assertEqual(result2["status"], "ERROR")
        self.assertEqual(result2["available_options"], ["OPT-1"])


class AssessDeliveryImpactTests(unittest.IsolatedAsyncioTestCase):
    async def _assess(self, material_ready_date: str) -> dict:
        async def fake_track(work_order_id: str):
            return {
                "work_order_no": "TEST_WO_PAGE_00023",
                "status": "ACCEPTED",
                "quantity": "2000",
                "completed_qty": "1800",
                "completion_rate": 90.0,
                "due_date": "2026-10-31",
                "risks": [],
                "data_source": "openmes_api",
                "authority": "OpenMES",
            }

        with patch.object(real_order, "track_order", side_effect=fake_track):
            return await real_order.assess_delivery_impact("9", material_ready_date)

    async def test_material_after_due_reports_delay(self):
        result = await self._assess("2026-11-05")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verdict"], "arrival_after_due")
        self.assertEqual(result["buffer_days"], -5)
        self.assertIn("延期 5 天", result["conclusion"])
        # 剩余产量来自真实工单数据
        self.assertEqual(result["remaining_qty"], "200")

    async def test_material_before_due_is_in_time(self):
        result = await self._assess("2026-10-20")
        self.assertEqual(result["verdict"], "arrival_in_time")
        self.assertEqual(result["buffer_days"], 11)

    async def test_invalid_date_returns_data_missing(self):
        result = await self._assess("下周三")
        self.assertEqual(result["status"], "DATA_MISSING")
        self.assertTrue(any(f["field"] == "material_ready_date" for f in result["missing_fields"]))

    async def test_assessment_scope_is_honest(self):
        result = await self._assess("2026-10-20")
        self.assertIn("不伪造", result["assessment_scope"])


class FindQuotationByErpOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_finds_latest_quotation_by_draft_id(self):
        _persist_quotation("QUO-F1", "SAL-ORD-2099-00023", "17000.00", 2000)
        result = await real_order.find_quotation_by_erp_order("SAL-ORD-2099-00023")
        self.assertTrue(result["found"])
        self.assertEqual(result["quotation_id"], "QUO-F1")
        self.assertEqual(result["total_price"], "17000.00")
        self.assertEqual(result["matched_count"], 1)

    async def test_no_match_reports_clearly(self):
        result = await real_order.find_quotation_by_erp_order("SAL-ORD-2099-99999")
        self.assertFalse(result["found"])
        self.assertIn("报价分析", result["note"])


class ProposalCollectionTests(unittest.TestCase):
    """协调者把真实工具结果原样汇集为 proposal_options（不二次计算）。"""

    def test_collects_shortage_options_and_assessments(self):
        collected = [
            ("procurement.analyze_real", {
                "plan_id": "PROC-CI",
                "quotation_id": "QUO-CI",
                "quotation_status": "APPROVED",
                "net_requirement": {
                    "has_shortage": True,
                    "shortage_count": 1,
                    "finished_item": "BD-2401",
                    "shortage_items": [{"item_id": "CI-RAW", "net_requirement": "1200"}],
                },
                "supplier_options": [_option("OPT-1", "1060.00")],
                "recommendation": "按规则推荐 OPT-1",
            }),
            ("quotation.assess_cost_impact", {
                "status": "ok",
                "option_id": "OPT-1",
                "supplier_name": "B 供应商",
                "material_cost_delta": "60.00",
                "per_unit_surcharge": "0.03",
                "currency": "CNY",
            }),
            ("tracking.assess_delivery_impact", {
                "verdict": "arrival_in_time",
                "conclusion": "物料于交期前 11 天到位",
                "work_order_no": "TEST_WO_PAGE_00023",
                "due_date": "2026-10-31",
                "material_ready_date": "2026-10-20",
                "buffer_days": 11,
            }),
        ]
        proposal = BusinessCoordinator._collect_proposal(collected)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["plan_id"], "PROC-CI")
        self.assertEqual(proposal["shortage"]["shortage_count"], 1)
        self.assertEqual(len(proposal["supplier_options"]), 1)
        self.assertEqual(proposal["cost_assessments"][0]["material_cost_delta"], "60.00")
        self.assertEqual(proposal["delivery_assessments"][0]["verdict"], "arrival_in_time")
        self.assertEqual(proposal["quotation_status"], "APPROVED")
        self.assertNotIn("data_missing", proposal)

    def test_collects_data_missing_entries(self):
        collected = [
            ("quotation.assess_cost_impact", {
                "status": "DATA_MISSING",
                "missing_fields": [{"field": "option_material_cost", "detail": "价格记录缺失"}],
                "need": "补录真实价格",
            }),
        ]
        proposal = BusinessCoordinator._collect_proposal(collected)
        self.assertEqual(len(proposal["data_missing"]), 1)
        self.assertEqual(proposal["data_missing"][0]["source_skill"], "quotation.assess_cost_impact")

    def test_current_analysis_is_not_overwritten_by_old_plan_status_readback(self):
        """状态回读可能命中旧方案，但方案卡片必须保留本轮分析生成的方案。"""
        current = {
            "plan_id": "PROC-CURRENT",
            "quotation_id": "QUO-CI",
            "quotation_status": "DRAFT",
            "status": "PENDING_APPROVAL",
            "net_requirement": {
                "has_shortage": True,
                "shortage_count": 1,
                "finished_item": "BD-2401",
                "shortage_items": [{"item_id": "CI-RAW", "net_requirement": "1200"}],
            },
            "supplier_options": [_option("OPT-1", "1060.00")],
            "recommendation": "当前方案推荐 OPT-1",
        }
        old_plan = {
            "plan_id": "PROC-OLD",
            "quotation_id": "QUO-CI",
            "quotation_status": "APPROVED",
            "status": "PO_DRAFT_CREATED",
            "selected_option_id": "OPT-9",
            "approval_id": "APPR-OLD",
            "po_draft_id": "PUR-OLD",
            "net_requirement": {
                "has_shortage": True,
                "shortage_count": 9,
                "finished_item": "OLD-ITEM",
                "shortage_items": [{"item_id": "OLD-RAW", "net_requirement": "9"}],
            },
            "supplier_options": [_option("OPT-9", "9999.00")],
            "recommendation": "旧方案",
        }

        proposal = BusinessCoordinator._collect_proposal([
            ("procurement.analyze_real", current),
            ("procurement.get_real_plan", old_plan),
        ])

        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["plan_id"], "PROC-CURRENT")
        self.assertEqual(proposal["quotation_status"], "DRAFT")
        self.assertEqual(proposal["shortage"]["finished_item"], "BD-2401")
        self.assertEqual(proposal["supplier_options"][0]["option_id"], "OPT-1")
        self.assertNotIn("plan_status", proposal)

    def test_matching_plan_status_readback_only_supplements_status(self):
        current = {
            "plan_id": "PROC-CURRENT-STATUS",
            "quotation_id": "QUO-CI",
            "quotation_status": "DRAFT",
            "net_requirement": {
                "has_shortage": True,
                "shortage_count": 1,
                "shortage_items": [{"item_id": "CI-RAW", "net_requirement": "1200"}],
            },
            "supplier_options": [_option("OPT-1", "1060.00")],
        }
        status_readback = {
            "plan_id": "PROC-CURRENT-STATUS",
            "quotation_status": "DRAFT",
            "status": "PO_DRAFT_CREATED",
            "selected_option_id": "OPT-1",
            "approval_id": "APPR-CURRENT",
            "po_draft_id": "PUR-CURRENT",
            "net_requirement": {
                "has_shortage": True,
                "shortage_count": 1,
                "shortage_items": [{"item_id": "CI-RAW", "net_requirement": "1200"}],
            },
            "supplier_options": [_option("OPT-1", "1060.00")],
        }

        proposal = BusinessCoordinator._collect_proposal([
            ("procurement.analyze_real", current),
            ("procurement.get_real_plan", status_readback),
        ])

        self.assertEqual(proposal["plan_id"], "PROC-CURRENT-STATUS")
        self.assertEqual(proposal["plan_status"], "PO_DRAFT_CREATED")
        self.assertEqual(proposal["selected_option_id"], "OPT-1")
        self.assertEqual(proposal["approval_id"], "APPR-CURRENT")
        self.assertEqual(proposal["po_draft_id"], "PUR-CURRENT")

    def test_assessments_from_old_plan_are_not_attached_to_current_card(self):
        current = {
            "plan_id": "PROC-CURRENT-ASSESS",
            "quotation_id": "QUO-CI",
            "quotation_status": "DRAFT",
            "net_requirement": {
                "has_shortage": True,
                "shortage_count": 1,
                "shortage_items": [{"item_id": "CI-RAW", "net_requirement": "1200"}],
            },
            "supplier_options": [_option("OPT-1", "1060.00")],
        }
        old_cost = {
            "status": "ok",
            "plan_id": "PROC-OLD-ASSESS",
            "option_id": "OPT-1",
            "material_cost_delta": "-999.00",
        }
        current_cost = {
            "status": "ok",
            "plan_id": "PROC-CURRENT-ASSESS",
            "option_id": "OPT-1",
            "material_cost_delta": "60.00",
        }

        proposal = BusinessCoordinator._collect_proposal([
            ("procurement.analyze_real", current),
            ("quotation.assess_cost_impact", old_cost),
            ("quotation.assess_cost_impact", current_cost),
        ])

        self.assertEqual(len(proposal["cost_assessments"]), 1)
        self.assertEqual(proposal["cost_assessments"][0]["material_cost_delta"], "60.00")

    def test_new_skills_registered_on_aip_services(self):
        from app.aip.agents.quotation_aip import create_quotation_aip_service
        from app.aip.agents.tracking_aip import create_tracking_aip_service

        quotation_skills = {s["id"] for s in create_quotation_aip_service().list_skills()}
        tracking_skills = {s["id"] for s in create_tracking_aip_service().list_skills()}
        for tool in REAL_SKILL_TOOLS:
            if tool["aip_agent"] == "quotation":
                self.assertIn(tool["skill_id"], quotation_skills)
            elif tool["aip_agent"] == "tracking":
                self.assertIn(tool["skill_id"], tracking_skills)


if __name__ == "__main__":
    unittest.main()


class AssessCombinationTests(unittest.IsolatedAsyncioTestCase):
    """分单采购组合的确定性计算（阶段七）。"""

    def _persist_split_plan(self, plan_id: str) -> None:
        opt_a = _option("OPT-A", "600.00")
        opt_a["items"] = [{"item_id": "CI-RAW", "unit_price": "12.5", "price_record": "rec-A", "line_total": "600.00"}]
        opt_b = _option("OPT-B", "1500.00")
        opt_b["items"] = [
            {"item_id": "CI-RAW", "unit_price": "13.0", "price_record": "rec-B1", "line_total": "600.00"},
            {"item_id": "M10-BOLT", "unit_price": "0.9", "price_record": "rec-B2", "line_total": "900.00"},
        ]
        _persist_plan(
            plan_id, "QUO-CI", baseline="1000.00",
            options=[opt_a, opt_b],
            shortage_items=[
                {"item_id": "CI-RAW", "net_requirement": "1200"},
                {"item_id": "M10-BOLT", "net_requirement": "3000"},
            ],
        )

    async def test_combination_math_coverage_and_overlap(self):
        self._persist_split_plan("PROC-COMB1")
        result = await real_order.assess_combination("PROC-COMB1", ["OPT-A", "OPT-B"])
        self.assertEqual(result["status"], "ok")
        # 组合成本 = 600 + 1500 = 2100（真实价格记录合计，无系数）
        self.assertEqual(result["combined_cost"], "2100.00")
        # 覆盖并集完整
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["covered_items"], ["CI-RAW", "M10-BOLT"])
        # CI-RAW 被两个选项同时覆盖 → 重复采购告警
        self.assertEqual(result["overlapping_items"], ["CI-RAW"])
        self.assertTrue(any("重复覆盖" in w for w in result["warnings"]))
        # 证据链含两个选项的真实价格记录
        record_ids = [e["record_id"] for e in result["evidence"]]
        self.assertIn("rec-A", record_ids)
        self.assertIn("rec-B2", record_ids)
        self.assertIn("确定性计算", result["calculation_basis"])

    async def test_single_option_leaves_shortage_uncovered(self):
        self._persist_split_plan("PROC-COMB2")
        result = await real_order.assess_combination("PROC-COMB2", ["OPT-A"])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["uncovered_items"], ["M10-BOLT"])
        self.assertTrue(any("不构成完整采购方案" in w for w in result["warnings"]))

    async def test_incomplete_pricing_blocks_decision(self):
        bad = _option("OPT-BAD", "", complete=False)
        _persist_plan("PROC-COMB3", "QUO-CI", baseline="1000.00", options=[bad])
        result = await real_order.assess_combination("PROC-COMB3", ["OPT-BAD"])
        self.assertEqual(result["status"], "DATA_MISSING")
        self.assertTrue(any(f["field"] == "option_total_cost" for f in result["missing_fields"]))

    async def test_unknown_option_reports_available(self):
        self._persist_split_plan("PROC-COMB4")
        result = await real_order.assess_combination("PROC-COMB4", ["OPT-NOPE"])
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["available_options"], ["OPT-A", "OPT-B"])

    async def test_registered_on_aip_service(self):
        from app.aip.agents.procurement_aip import create_procurement_aip_service
        skills = {s["id"] for s in create_procurement_aip_service().list_skills()}
        self.assertIn("procurement.assess_combination", skills)


class AssessQualityImpactTests(unittest.IsolatedAsyncioTestCase):
    """质量异常影响分析（阶段八，讨论稿例子三）。"""

    @staticmethod
    def _fake_quality(*, records, open_issues, gate_passed, missing=None, inspections=None):
        async def fake_quality(work_order_id: str):
            return {
                "work_order_id": work_order_id,
                "quality_records": records,
                "open_issues_count": open_issues,
                "missing_documents": missing or [],
                "inspections": inspections if inspections is not None else [],
                "quality_gate_passed": gate_passed,
                "gate_details": {"missing_documents": missing or []},
                "authority": "OpenMES",
                "data_source": "openmes_api",
            }
        return fake_quality

    @staticmethod
    def _fake_track():
        async def fake_track(work_order_id: str):
            return {
                "work_order_no": "WO-2026-001",
                "status": "ACCEPTED",
                "quantity": "500",
                "completed_qty": "0",
                "completion_rate": 0.0,
                "due_date": "2026-10-20",
                "risks": [],
                "data_source": "openmes_api",
                "authority": "OpenMES",
            }
        return fake_track

    async def test_open_issues_block_shipping_with_conclusions(self):
        records = [
            {"record_id": "2", "title": "铸铁毛坯库存不足", "severity": "CRITICAL", "status": "OPEN", "record_type": "Material Shortage"},
            {"record_id": "1", "title": "制动盘外径尺寸超差", "severity": "MEDIUM", "status": "RESOLVED", "record_type": "Measurement / Dimension Error"},
        ]
        with patch.object(real_order, "quality_package", side_effect=self._fake_quality(records=records, open_issues=1, gate_passed=False, missing=["SOP"])), \
             patch.object(real_order, "track_order", side_effect=self._fake_track()):
            result = await real_order.assess_quality_impact("2")

        self.assertEqual(result["status"], "ok")
        # 未关闭问题排在最前（严重度排序只作用于未关闭记录）
        self.assertEqual(result["open_records"][0]["record_id"], "2")
        # 已解决问题不计入未关闭
        self.assertEqual(len(result["open_records"]), 1)
        conclusions = " ".join(result["impact_conclusions"])
        self.assertIn("1 项未关闭质量问题", conclusions)
        self.assertIn("SOP", conclusions)
        self.assertIn("0%", conclusions)
        # 数据缺口如实标注（检验 0 条 + SN 无 API + NCR NOT_SUPPORTED）
        gap_fields = [g["field"] for g in result["data_gaps"]]
        self.assertIn("inspections", gap_fields)
        self.assertIn("sn_traceability", gap_fields)
        self.assertIn("ncr_full_disposition", gap_fields)
        # 处理选项均标注需人工确认
        self.assertTrue(all(o["requires_human_confirmation"] for o in result["handling_options"]))

    async def test_clean_work_order_reports_no_block(self):
        async def fake_track_done(work_order_id: str):
            return {
                "work_order_no": "WO-2026-001",
                "status": "DONE",
                "quantity": "500",
                "completed_qty": "500",
                "completion_rate": 100.0,
                "due_date": "2026-10-20",
                "risks": [],
                "data_source": "openmes_api",
                "authority": "OpenMES",
            }

        with patch.object(real_order, "quality_package", side_effect=self._fake_quality(records=[], open_issues=0, gate_passed=True)), \
             patch.object(real_order, "track_order", side_effect=fake_track_done):
            result = await real_order.assess_quality_impact("7")
        self.assertTrue(result["quality_gate_passed"])
        self.assertIn("不构成发运阻断", " ".join(result["impact_conclusions"]))

    async def test_batches_loaded_via_adapter(self):
        class _FakeMES:
            authority = "OpenMES"

            async def get_work_order_batches(self, work_order_id: str):
                return [
                    {"batch_id": "3", "lot_number": "TEST_LOT_PAGE_9", "target_qty": "2000", "status": "PENDING",
                     "authority": "OpenMES", "data_source": "openmes_api"},
                ]

        with patch.object(real_order, "quality_package", side_effect=self._fake_quality(records=[], open_issues=0, gate_passed=True)), \
             patch.object(real_order, "track_order", side_effect=self._fake_track()), \
             patch.object(real_order, "get_mes_adapter", return_value=_FakeMES()):
            result = await real_order.assess_quality_impact("9")

        self.assertEqual(result["batches"][0]["lot_number"], "TEST_LOT_PAGE_9")
        self.assertFalse(any(g["field"] == "batches" for g in result["data_gaps"]))

    async def test_batch_failure_is_a_data_gap_not_empty(self):
        class _FailingMES:
            authority = "OpenMES"

            async def get_work_order_batches(self, work_order_id: str):
                raise RuntimeError("MES 连接失败")

        with patch.object(real_order, "quality_package", side_effect=self._fake_quality(records=[], open_issues=0, gate_passed=True)), \
             patch.object(real_order, "track_order", side_effect=self._fake_track()), \
             patch.object(real_order, "get_mes_adapter", return_value=_FailingMES()):
            result = await real_order.assess_quality_impact("9")

        self.assertEqual(result["batches"], [])
        batch_gaps = [g for g in result["data_gaps"] if g["field"] == "batches"]
        self.assertEqual(len(batch_gaps), 1)
        self.assertIn("MES 连接失败", batch_gaps[0]["detail"])

    def test_collector_gathers_quality_impact(self):
        collected = [
            ("quality.assess_quality_impact", {
                "status": "ok",
                "work_order_id": "2",
                "work_order_no": "WO-2026-001",
                "quality_records": [{"record_id": "2", "title": "铸铁毛坯库存不足", "severity": "CRITICAL", "status": "OPEN"}],
                "open_issues_count": 1,
                "open_records": [{"record_id": "2"}],
                "batches": [],
                "quality_gate_passed": False,
                "missing_documents": ["SOP"],
                "production": {"completion_rate": 0.0, "status": "ACCEPTED", "due_date": "2026-10-20"},
                "impact_conclusions": ["存在 1 项未关闭质量问题"],
                "handling_options": [{"option": "解决质量问题", "how": "resolve 审批通道", "requires_human_confirmation": True}],
                "data_gaps": [{"field": "inspections", "detail": "无检验记录"}],
            }),
        ]
        proposal = BusinessCoordinator._collect_proposal(collected)
        qi = proposal["quality_impacts"][0]
        self.assertEqual(qi["work_order_no"], "WO-2026-001")
        self.assertEqual(qi["open_issues_count"], 1)
        self.assertEqual(len(qi["data_gaps"]), 1)
