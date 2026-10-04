"""
跟单智能体 AIP 服务。
技能分两类：
- *.real_*：调用真实业务链（real_order/order_linkage，数据来自真实 ERPNext/OpenMES）
- 其余：Mock 场景模拟技能（tool_registry 合成数据，仅用于 Mock 模拟）
"""
from __future__ import annotations

import logging

from app.aip.aip_agent_service import AipAgentService
from app.services import order_linkage, real_order
from app.tools.registry import tool_registry
from app.domain.models import AgentType

logger = logging.getLogger(__name__)


def create_tracking_aip_service(aic: str = "local-tracking-001") -> AipAgentService:
    """创建跟单智能体 AIP 服务实例。"""
    service = AipAgentService(
        agent_name="汽车零部件跟单智能体",
        agent_type=AgentType.TRACKING.value,
        aic=aic,
    )

    # ===== 真实业务技能 =====

    async def real_track(inputs: dict) -> dict:
        """真实生产跟单：读取真实 MES 工单进度与风险。"""
        return await real_order.track_order(str(inputs["work_order_id"]))

    service.register_skill(
        skill_id="tracking.track_real",
        skill_name="真实生产跟单（MES 工单进度）",
        handler=real_track,
    )

    async def real_find_by_no(inputs: dict) -> dict:
        """按用户可见的 MES 工单编号查找数字工单 ID。"""
        return await real_order.find_work_order_by_no(str(inputs["work_order_no"]))

    service.register_skill(
        skill_id="tracking.find_real_by_no",
        skill_name="按工单编号查找真实工单",
        handler=real_find_by_no,
    )

    async def real_lookup_link(inputs: dict) -> dict:
        """查询 ERP 销售订单与 MES 工单的正式关联（customer_order_no 精确匹配）。"""
        return await order_linkage.get_order_mes_link(str(inputs["erp_order_id"]))

    service.register_skill(
        skill_id="tracking.lookup_order_link",
        skill_name="查询 ERP↔MES 订单关联",
        handler=real_lookup_link,
    )

    async def real_ship_gate(inputs: dict) -> dict:
        """真实发运门禁：报价审批 + 质量门禁 + 生产完成率综合判断。"""
        return await real_order.ship_gate_check(
            str(inputs["work_order_id"]),
            quotation_approved=bool(inputs.get("quotation_approved", False)),
        )

    service.register_skill(
        skill_id="tracking.check_real_ship_gate",
        skill_name="真实发运门禁判断",
        handler=real_ship_gate,
    )

    async def real_delivery_impact(inputs: dict) -> dict:
        """真实交期影响评估：物料到货时间（由采购方案 lead_time 推算）vs 工单交期。"""
        return await real_order.assess_delivery_impact(
            work_order_id=str(inputs["work_order_id"]),
            material_ready_date=str(inputs["material_ready_date"]),
        )

    service.register_skill(
        skill_id="tracking.assess_delivery_impact",
        skill_name="交期影响评估（物料到货 vs 工单交期）",
        handler=real_delivery_impact,
    )

    # ===== Mock 场景模拟技能 =====

    # 交期预估
    async def calculate_eta(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.TRACKING, "tracking.calculate_eta", inputs
        )

    service.register_skill(
        skill_id="tracking.calculate_eta",
        skill_name="交期预估",
        handler=calculate_eta,
    )

    # 风险识别
    async def detect_risk(inputs: dict) -> dict:
        """
        风险识别。
        根据订单状态、物料状态、质量状态等综合判断风险。
        """
        risks = []
        # 物料短缺风险
        if inputs.get("material_shortage", False):
            risks.append({
                "risk_type": "material_shortage",
                "severity": "high",
                "description": "关键物料短缺，可能影响生产进度",
            })
        # 质量异常风险
        if inputs.get("quality_issue", False):
            risks.append({
                "risk_type": "quality_issue",
                "severity": "high",
                "description": "存在质量异常，可能导致返工或延期",
            })
        # 进度滞后风险
        progress_pct = inputs.get("progress_pct", 0)
        expected_pct = inputs.get("expected_pct", 0)
        if progress_pct < expected_pct * 0.9:
            risks.append({
                "risk_type": "schedule_delay",
                "severity": "medium",
                "description": f"生产进度滞后，当前{progress_pct}%，计划{expected_pct}%",
            })

        return {
            "project_id": inputs.get("project_id", ""),
            "risk_count": len(risks),
            "overall_severity": "high" if any(r["severity"] == "high" for r in risks) else (
                "medium" if risks else "low"
            ),
            "risks": risks,
        }

    service.register_skill(
        skill_id="tracking.detect_risk",
        skill_name="风险识别",
        handler=detect_risk,
    )

    # 发运门禁检查
    async def check_ship_gate(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.TRACKING, "tracking.check_ship_gate", inputs
        )

    service.register_skill(
        skill_id="tracking.check_ship_gate",
        skill_name="发运门禁检查",
        handler=check_ship_gate,
    )

    logger.info(f"跟单智能体 AIP 服务创建完成，共 {len(service.list_skills())} 个技能")
    return service
