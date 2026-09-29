"""
AIP 智能体协作演示模块。

展示四个智能体（报价、采购、跟单、质量文档）如何通过标准 AIP 协议
（ACPs-spec-AIP-v02.02）互相发现、调用和协作。

这是一个独立的演示模块，不依赖原有业务流程，
专注于展示 AIP 协议级别的智能体互联能力。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.aip.aip_agent_client import AipAgentClient

logger = logging.getLogger(__name__)


class AipCollaborationDemo:
    """
    AIP 智能体协作演示编排器。

    模拟一个汽车零部件订单的端到端协作流程，
    所有智能体间的调用都通过标准 AIP 协议完成。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        session_id: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id or f"collab-demo-{int(datetime.now().timestamp())}"
        self.clients: dict[str, AipAgentClient] = {}
        self.collaboration_log: list[dict[str, Any]] = []
        self.project_id = f"DEMO-AIP-{int(datetime.now().timestamp())}"

    def _get_client(self, agent_type: str) -> AipAgentClient:
        """获取指定智能体的 AIP 客户端。"""
        if agent_type not in self.clients:
            endpoint_map = {
                "quotation": "/aip/quotation/rpc",
                "procurement": "/aip/procurement/rpc",
                "tracking": "/aip/tracking/rpc",
                "quality_document": "/aip/quality-document/rpc",
            }
            endpoint = endpoint_map[agent_type]
            self.clients[agent_type] = AipAgentClient(
                partner_url=f"{self.base_url}{endpoint}",
                leader_id=f"coordinator-{self.session_id}",
            )
        return self.clients[agent_type]

    def _log_collaboration(
        self,
        leader: str,
        partner: str,
        skill: str,
        status: str,
        detail: str = "",
    ):
        """记录协作事件。"""
        self.collaboration_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "leader": leader,
            "partner": partner,
            "skill": skill,
            "status": status,
            "detail": detail,
        })

    async def run_normal_order_demo(self) -> dict[str, Any]:
        """
        运行正常订单的 AIP 协作演示。

        协作流程：
        1. 报价智能体计算成本 + 生成报价草稿
        2. 跟单智能体计算 ETA（报价调用跟单）
        3. 采购智能体计算净需求
        4. 质量文档智能体生成资料清单 + 检查完整性
        5. 跟单智能体检查发运门禁
        """
        print(f"\n{'='*70}")
        print(f"🚀 AIP 智能体协作演示 - 正常订单场景")
        print(f"   会话ID: {self.session_id}")
        print(f"   项目ID: {self.project_id}")
        print(f"{'='*70}\n")

        # 场景数据（模拟一个汽车齿轮订单）
        order_data = {
            "project_id": self.project_id,
            "customer_name": "比亚迪汽车",
            "product_name": "变速箱驱动齿轮",
            "product_id": "PROD-GEAR-001",
            "order_quantity": 5000,
            "required_delivery_date": "2026-11-15",
            "currency": "CNY",
        }

        # ===== 第 1 步：报价智能体计算成本 =====
        print("📋 [步骤 1/5] 报价智能体: 成本计算")
        print("   └─ Leader: 协调器 → Partner: 报价智能体")
        print("   └─ 技能: quotation.calculate_cost")

        quote_inputs = {
            "order_quantity": order_data["order_quantity"],
            "currency": order_data["currency"],
            "bom": [
                {"qty_per_product": 2.5, "unit_price": "18.50", "material_id": "MAT-STEEL-20CrMnTi"},
                {"qty_per_product": 0.3, "unit_price": "45.00", "material_id": "MAT-COPPER-Alloy"},
            ],
            "cost_components": {
                "processing": "12.80",
                "outsourcing": "5.00",
                "inspection": "3.20",
                "packaging": "1.50",
                "logistics": "2.80",
            },
            "risk_buffer_rate": "0.05",
            "target_gross_margin": "0.25",
            "minimum_gross_margin": "0.18",
        }

        try:
            quote_result = await self._invoke_skill(
                "coordinator", "quotation", "quotation.calculate_cost", quote_inputs
            )
            gross_margin = quote_result.get('actual_gross_margin', 'N/A')
            if gross_margin != 'N/A':
                gross_margin_pct = f"{float(gross_margin)*100:.1f}%"
            else:
                gross_margin_pct = 'N/A'
            print(f"   ✅ 成功! 单价: ¥{quote_result.get('suggested_unit_price', 'N/A')} "
                  f"({gross_margin_pct} 毛利率)")
            print(f"   ✅ 总价: ¥{quote_result.get('total_price', 'N/A')}")
        except Exception as e:
            print(f"   ❌ 失败: {e}")
            quote_result = {"error": str(e)}

        print()

        # ===== 第 2 步：报价智能体调用跟单智能体计算 ETA =====
        print("📋 [步骤 2/5] 报价智能体 → 跟单智能体: 交期预估")
        print("   └─ Leader: 报价智能体 → Partner: 跟单智能体")
        print("   └─ 技能: tracking.calculate_eta")

        eta_inputs = {
            "material_available_date": "2026-10-05",
            "capacity_completion_date": "2026-10-28",
            "quality_wait_days": 2,
            "buffer_days": 3,
        }

        try:
            eta_result = await self._invoke_skill(
                "quotation", "tracking", "tracking.calculate_eta", eta_inputs
            )
            print(f"   ✅ 成功! 预计交付日期: {eta_result.get('eta_date', 'N/A')}")
            print(f"   ✅ 关键路径: {eta_result.get('critical_path', 'N/A')}")
        except Exception as e:
            print(f"   ❌ 失败: {e}")
            eta_result = {"error": str(e)}

        print()

        # ===== 第 3 步：采购智能体计算净需求 =====
        print("📋 [步骤 3/5] 采购智能体: 净需求计算")
        print("   └─ Leader: 协调器 → Partner: 采购智能体")
        print("   └─ 技能: procurement.calculate_net_requirement")

        material_inputs = {
            "material_id": "MAT-STEEL-20CrMnTi",
            "required_quantity": 12500,  # 5000 * 2.5 kg
            "inventory": [
                {"quantity": 3000, "status": "available", "location": "WH-A1"},
                {"quantity": 500, "status": "inspection", "location": "QA-AREA"},
                {"quantity": 200, "status": "frozen", "location": "FROZEN-ZONE"},
            ],
            "inbound": [
                {"quantity": 5000, "confirmed": True, "eta": "2026-10-08", "po_id": "PO-2026-1001"},
                {"quantity": 3000, "confirmed": False, "eta": "2026-10-15", "po_id": "PO-2026-1002"},
            ],
            "safety_stock": 2000,
            "lot_size": 1000,
        }

        try:
            net_result = await self._invoke_skill(
                "coordinator", "procurement", "procurement.calculate_net_requirement", material_inputs
            )
            print(f"   ✅ 成功! 净需求: {net_result.get('net_requirement', 'N/A')} kg")
            print(f"   ✅ 可用库存: {net_result.get('usable_inventory', 'N/A')} kg")
            print(f"   ✅ 确认在途: {net_result.get('confirmed_inbound', 'N/A')} kg")
        except Exception as e:
            print(f"   ❌ 失败: {e}")
            net_result = {"error": str(e)}

        print()

        # ===== 第 4 步：质量文档智能体生成资料清单 =====
        print("📋 [步骤 4/5] 质量文档智能体: 资料清单生成")
        print("   └─ Leader: 协调器 → Partner: 质量文档智能体")
        print("   └─ 技能: quality_document.build_checklist")

        checklist_inputs = {
            "project_id": self.project_id,
            "product_type": "safety_critical",  # 安全件
            "customer_requirements": ["rohs", "reach"],
        }

        try:
            checklist_result = await self._invoke_skill(
                "coordinator", "quality_document", "quality_document.build_checklist", checklist_inputs
            )
            print(f"   ✅ 成功! 共 {checklist_result.get('total_items', 'N/A')} 项资料")
            print(f"   ✅ 其中必选项: {len(checklist_result.get('required_items', []))} 项")
        except Exception as e:
            print(f"   ❌ 失败: {e}")
            checklist_result = {"error": str(e)}

        print()

        # ===== 第 5 步：跟单智能体检查发运门禁 =====
        print("📋 [步骤 5/5] 跟单智能体 → 质量/资料: 发运门禁检查")
        print("   └─ Leader: 跟单智能体 → Partner: (模拟双门禁)")
        print("   └─ 技能: tracking.check_ship_gate")

        gate_inputs = {
            "quality_released": True,
            "document_package_approved": True,
        }

        try:
            gate_result = await self._invoke_skill(
                "tracking", "tracking", "tracking.check_ship_gate", gate_inputs
            )
            gate_status = "通过 ✅" if gate_result.get("ready_to_request_shipment_approval") else "未通过 ❌"
            print(f"   ✅ 门禁检查结果: {gate_status}")
            if gate_result.get("blocking_gates"):
                for gate in gate_result["blocking_gates"]:
                    print(f"      - {gate}: 未满足")
        except Exception as e:
            print(f"   ❌ 失败: {e}")
            gate_result = {"error": str(e)}

        print()

        # ===== 汇总 =====
        print("=" * 70)
        print("📊 协作统计")
        print("=" * 70)
        print(f"   协作轮次: {len(self.collaboration_log)}")
        print(f"   参与智能体: 报价、采购、跟单、质量文档")
        print(f"   协议: ACPs AIP v02.02 (JSON-RPC)")
        print(f"   传输: HTTP/JSON")
        print()
        print("📋 协作明细:")
        for i, log in enumerate(self.collaboration_log, 1):
            status_icon = "✅" if log["status"] == "success" else "❌"
            print(f"   {i}. {status_icon} {log['leader']} → {log['partner']}: {log['skill']}")

        print()
        print("🎉 AIP 智能体协作演示完成!")
        print("=" * 70)

        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "scenario": "normal_order",
            "collaboration_rounds": len(self.collaboration_log),
            "collaboration_log": self.collaboration_log,
            "results": {
                "quotation": quote_result,
                "eta": eta_result,
                "net_requirement": net_result,
                "quality_checklist": checklist_result,
                "ship_gate": gate_result,
            },
        }

    async def run_shortage_demo(self) -> dict[str, Any]:
        """
        运行物料短缺场景的 AIP 协作演示。

        展示异常场景下智能体的动态协作：
        1. 跟单智能体检测到物料短缺风险
        2. 采购智能体计算净需求 + 对比供应方案
        3. 报价智能体做成本评估
        4. 形成多方案对比供人工选择
        """
        print(f"\n{'='*70}")
        print(f"⚠️  AIP 智能体协作演示 - 物料短缺异常场景")
        print(f"   会话ID: {self.session_id}")
        print(f"   项目ID: {self.project_id}")
        print(f"{'='*70}\n")

        # ===== 第 1 步：跟单检测风险 =====
        print("🔍 [步骤 1/4] 跟单智能体: 风险识别")
        print("   └─ 发现物料短缺 + 进度滞后风险")

        risk_inputs = {
            "project_id": self.project_id,
            "material_shortage": True,
            "quality_issue": False,
            "progress_pct": 45,
            "expected_pct": 60,
        }

        risk_result = await self._invoke_skill(
            "coordinator", "tracking", "tracking.detect_risk", risk_inputs
        )
        print(f"   ✅ 检测到 {risk_result['risk_count']} 个风险，级别: {risk_result['overall_severity']}")
        for r in risk_result["risks"]:
            icon = "🔴" if r["severity"] == "high" else "🟡"
            print(f"   {icon} {r['risk_type']}: {r['description']}")

        print()

        # ===== 第 2 步：采购计算净需求 =====
        print("📦 [步骤 2/4] 采购智能体: 净需求计算")
        print("   └─ Leader: 跟单智能体 → Partner: 采购智能体")

        net_inputs = {
            "material_id": "MAT-STEEL-20CrMnTi",
            "required_quantity": 8000,
            "inventory": [
                {"quantity": 1200, "status": "available", "location": "WH-A1"},
                {"quantity": 300, "status": "inspection", "location": "QA-AREA"},
            ],
            "inbound": [
                {"quantity": 800, "confirmed": True, "eta": "2026-10-20", "po_id": "PO-2026-1003"},
            ],
            "safety_stock": 500,
            "lot_size": 500,
        }

        net_result = await self._invoke_skill(
            "tracking", "procurement", "procurement.calculate_net_requirement", net_inputs
        )
        print(f"   ✅ 净需求: {net_result['net_requirement']} kg")
        print(f"   ✅ 可用库存: {net_result['usable_inventory']} kg")
        print(f"   ✅ 确认在途: {net_result['confirmed_inbound']} kg")

        print()

        # ===== 第 3 步：采购对比供应方案 =====
        print("🏭 [步骤 3/4] 采购智能体: 供应方案对比")
        print("   └─ 从 3 家供应商中筛选最优方案")

        compare_inputs = {
            "net_requirement": net_result,
            "options": [
                {
                    "option_id": "OPT-A",
                    "supplier_id": "SUP-BAOSTEEL",
                    "supplier_name": "宝钢特钢",
                    "unit_price": "22.50",
                    "delivery_date": "2026-10-15",
                    "quality_grade": "A",
                },
                {
                    "option_id": "OPT-B",
                    "supplier_id": "SUP-XINGDA",
                    "supplier_name": "兴达特钢",
                    "unit_price": "19.80",
                    "delivery_date": "2026-10-25",
                    "quality_grade": "B",
                },
                {
                    "option_id": "OPT-C",
                    "supplier_id": "SUP-DONGGANG",
                    "supplier_name": "东港精密",
                    "unit_price": "24.00",
                    "delivery_date": "2026-10-08",
                    "quality_grade": "A+",
                },
            ],
            "required_date": "2026-10-20",
        }

        compare_result = await self._invoke_skill(
            "tracking", "procurement", "procurement.compare_supply_plans", compare_inputs
        )
        print(f"   ✅ 方案对比完成，共 {len(compare_result.get('options', compare_result.get('supplier_options', [])))} 个可选方案")

        print()

        # ===== 第 4 步：报价做成本评估 =====
        print("💰 [步骤 4/4] 报价智能体: 成本影响评估")
        print("   └─ Leader: 采购智能体 → Partner: 报价智能体")
        print("   └─ 评估缺料对订单成本的影响")

        cost_inputs = {
            "cost_assessment_id": f"CA-{self.project_id}",
            "parent_order_id": f"SO-{self.project_id}",
            "accepted_quote_id": "Q-2026-0891",
            "accepted_quote_status": "ACCEPTED",
            "baseline_unit_price": "18.50",
            "shortage_quantity": str(net_result["net_requirement"]),
            "options": compare_inputs["options"],
        }

        cost_result = await self._invoke_skill(
            "procurement", "quotation", "quotation.create_cost_assessment", cost_inputs
        )
        print(f"   ✅ 成本评估完成")
        print(f"   📊 各方案额外成本:")
        for delta in cost_result.get("option_deltas", []):
            print(f"      - {delta['supplier_id']}: ¥{delta['delta_for_shortage']}")

        print()
        print("=" * 70)
        print("📊 协作统计")
        print("=" * 70)
        print(f"   协作轮次: {len(self.collaboration_log)}")
        print(f"   参与智能体: 跟单、采购、报价")
        print(f"   场景: 物料短缺异常 → 动态协作")
        print()
        print("📋 协作明细:")
        for i, log in enumerate(self.collaboration_log, 1):
            status_icon = "✅" if log["status"] == "success" else "❌"
            print(f"   {i}. {status_icon} {log['leader']} → {log['partner']}: {log['skill']}")

        print()
        print("🎉 AIP 异常场景协作演示完成!")
        print("=" * 70)

        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "scenario": "material_shortage",
            "collaboration_rounds": len(self.collaboration_log),
            "collaboration_log": self.collaboration_log,
            "results": {
                "risk_detection": risk_result,
                "net_requirement": net_result,
                "supply_comparison": compare_result,
                "cost_assessment": cost_result,
            },
        }

    async def _invoke_skill(
        self,
        leader_name: str,
        partner_type: str,
        skill_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        调用一个智能体的技能，通过标准 AIP 协议。

        Args:
            leader_name: 调用方名称（用于日志）
            partner_type: 被调用方智能体类型
            skill_id: 技能ID
            inputs: 输入参数

        Returns:
            技能执行结果
        """
        client = self._get_client(partner_type)
        try:
            result = await client.invoke_skill(
                skill_id=skill_id,
                inputs=inputs,
                session_id=self.session_id,
                timeout=10.0,
            )
            self._log_collaboration(leader_name, partner_type, skill_id, "success")
            return result
        except Exception as e:
            self._log_collaboration(leader_name, partner_type, skill_id, "failed", str(e))
            raise

    async def close(self):
        """关闭所有客户端连接。"""
        for client in self.clients.values():
            await client.close()
        self.clients.clear()


async def run_demo(scenario: str = "normal_order") -> dict[str, Any]:
    """便捷函数：运行指定场景的 AIP 协作演示。"""
    demo = AipCollaborationDemo()
    try:
        if scenario == "normal_order":
            return await demo.run_normal_order_demo()
        elif scenario == "material_shortage":
            return await demo.run_shortage_demo()
        else:
            raise ValueError(f"未知场景: {scenario}")
    finally:
        await demo.close()


if __name__ == "__main__":
    asyncio.run(run_demo())
