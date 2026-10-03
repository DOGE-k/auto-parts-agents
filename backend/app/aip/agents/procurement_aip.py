"""
采购智能体 AIP 服务。
技能分两类：
- *.real_*：调用真实业务链（real_order，数据来自真实 ERPNext/OpenMES）
- 其余：Mock 场景演示技能（tool_registry 合成数据，仅用于 Mock 演示）
"""
from __future__ import annotations

import logging

from app.aip.aip_agent_service import AipAgentService
from app.services import real_order
from app.tools.registry import tool_registry
from app.domain.models import AgentType

logger = logging.getLogger(__name__)


def create_procurement_aip_service(aic: str = "local-procurement-001") -> AipAgentService:
    """创建采购智能体 AIP 服务实例。"""
    service = AipAgentService(
        agent_name="汽车零部件采购智能体",
        agent_type=AgentType.PROCUREMENT.value,
        aic=aic,
    )

    # ===== 真实业务技能 =====

    async def real_analyze(inputs: dict) -> dict:
        """真实采购分析：基于真实报价 BOM 展开计算缺料与供应商方案。"""
        return await real_order.analyze_procurement(quotation_id=str(inputs["quotation_id"]))

    service.register_skill(
        skill_id="procurement.analyze_real",
        skill_name="真实采购分析（缺料/供应商方案）",
        handler=real_analyze,
    )

    async def real_get_plan(inputs: dict) -> dict:
        """查询已保存的真实采购方案（含审批与 PO 草稿状态）。"""
        plan = real_order.get_procurement_plan(str(inputs["plan_id"]))
        if plan is None:
            return {"found": False, "plan_id": str(inputs["plan_id"])}
        return plan

    service.register_skill(
        skill_id="procurement.get_real_plan",
        skill_name="查询真实采购方案",
        handler=real_get_plan,
    )

    async def real_assess_combination(inputs: dict) -> dict:
        """分单采购组合的确定性评估（覆盖并集/组合成本/最长交期/重复覆盖告警）。"""
        return await real_order.assess_combination(
            plan_id=str(inputs["plan_id"]),
            option_ids=list(inputs.get("option_ids") or []),
        )

    service.register_skill(
        skill_id="procurement.assess_combination",
        skill_name="分单采购组合评估（确定性计算）",
        handler=real_assess_combination,
    )

    async def real_find_plan_by_quotation(inputs: dict) -> dict:
        """按报价编号查找最新采购方案（审批状态/PO 草稿状态联动）。"""
        return await real_order.find_latest_plan_by_quotation(str(inputs["quotation_id"]))

    service.register_skill(
        skill_id="procurement.find_real_plan_by_quotation",
        skill_name="按报价查找最新采购方案状态",
        handler=real_find_plan_by_quotation,
    )

    # 全厂库存总览（2026-10-02 用户需求）
    async def real_inventory_overview(inputs: dict) -> dict:
        """全厂库存总览：ERPNext Bin 实时余量（只读）。"""
        limit = inputs.get("limit") or 50
        return await real_order.inventory_overview(int(limit))

    service.register_skill(
        skill_id="procurement.inventory_overview",
        skill_name="全厂库存总览（ERPNext Bin 实时余量）",
        handler=real_inventory_overview,
    )

    # ===== Mock 场景演示技能 =====

    # 净需求计算
    async def calculate_net_requirement(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.PROCUREMENT, "procurement.calculate_net_requirement", inputs
        )

    service.register_skill(
        skill_id="procurement.calculate_net_requirement",
        skill_name="净需求计算",
        handler=calculate_net_requirement,
    )

    # 供应方案对比
    async def compare_supply_plans(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.PROCUREMENT, "procurement.compare_supply_plans", inputs
        )

    service.register_skill(
        skill_id="procurement.compare_supply_plans",
        skill_name="供应方案对比",
        handler=compare_supply_plans,
    )

    # 采购订单草稿生成
    async def create_po_draft(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.PROCUREMENT, "procurement.create_po_draft", inputs
        )

    service.register_skill(
        skill_id="procurement.create_po_draft",
        skill_name="采购订单草稿生成",
        handler=create_po_draft,
    )

    logger.info(f"采购智能体 AIP 服务创建完成，共 {len(service.list_skills())} 个技能")
    return service
