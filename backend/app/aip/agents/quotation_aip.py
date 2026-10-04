"""
报价智能体 AIP 服务。
将报价智能体的工具能力通过标准 AIP 协议暴露。
技能分两类：
- *.real_*：调用真实业务链（real_order，数据来自真实 ERPNext/OpenMES）
- 其余：Mock 场景模拟技能（tool_registry 合成数据，仅用于 Mock 模拟）
"""
from __future__ import annotations

import logging

from app.aip.aip_agent_service import AipAgentService
from app.services import real_order
from app.tools.registry import tool_registry
from app.domain.models import AgentType

logger = logging.getLogger(__name__)


def create_quotation_aip_service(aic: str = "local-quotation-001") -> AipAgentService:
    """
    创建报价智能体 AIP 服务实例。
    注册所有报价相关的技能处理函数。
    """
    service = AipAgentService(
        agent_name="汽车零部件报价智能体",
        agent_type=AgentType.QUOTATION.value,
        aic=aic,
    )

    # ===== 真实业务技能（数据来自真实 ERPNext/OpenMES，evidence 可追溯） =====

    async def real_analyze(inputs: dict) -> dict:
        """真实报价分析：读取真实 ERP 客户/物料/价格/BOM/库存与 MES 排程。"""
        return await real_order.analyze_quotation(
            customer_id=str(inputs["customer_id"]),
            item_code=str(inputs["item_code"]),
            quantity=int(inputs["quantity"]),
            delivery_date=inputs.get("delivery_date"),
            source_erp_order_id=inputs.get("erp_order_id"),
        )

    service.register_skill(
        skill_id="quotation.analyze_real",
        skill_name="真实报价分析（ERP/MES）",
        handler=real_analyze,
    )

    async def real_get(inputs: dict) -> dict:
        """查询已保存的真实报价（含审批状态）。"""
        quotation = real_order.get_quotation(str(inputs["quotation_id"]))
        if quotation is None:
            return {"found": False, "quotation_id": str(inputs["quotation_id"])}
        return quotation

    service.register_skill(
        skill_id="quotation.get_real",
        skill_name="查询真实报价（含审批状态）",
        handler=real_get,
    )

    async def real_find_by_erp_order(inputs: dict) -> dict:
        """按 ERP 销售订单号反查关联报价（问答链：工单/订单 → 报价 → 采购方案）。"""
        return await real_order.find_quotation_by_erp_order(str(inputs["erp_order_id"]))

    service.register_skill(
        skill_id="quotation.find_real_by_erp_order",
        skill_name="按 ERP 订单号查找报价",
        handler=real_find_by_erp_order,
    )

    async def real_assess_cost(inputs: dict) -> dict:
        """真实成本影响评估：缺料换供应商方案对订单收入/毛利的影响（材料口径）。"""
        return await real_order.assess_cost_impact(
            plan_id=str(inputs["plan_id"]),
            option_id=str(inputs["option_id"]),
        )

    service.register_skill(
        skill_id="quotation.assess_cost_impact",
        skill_name="成本影响评估（缺料方案）",
        handler=real_assess_cost,
    )

    # ===== Mock 场景模拟技能（合成数据，不用于真实业务结果） =====

    # 注册技能：成本计算
    async def calculate_cost(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.QUOTATION, "quotation.calculate_cost", inputs
        )

    service.register_skill(
        skill_id="quotation.calculate_cost",
        skill_name="成本计算",
        handler=calculate_cost,
    )

    # 注册技能：报价草稿生成
    async def create_draft(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.QUOTATION, "quotation.create_draft", inputs
        )

    service.register_skill(
        skill_id="quotation.create_draft",
        skill_name="报价草稿生成",
        handler=create_draft,
    )

    # 注册技能：成本评估报告
    async def create_cost_assessment(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.QUOTATION, "quotation.create_cost_assessment", inputs
        )

    service.register_skill(
        skill_id="quotation.create_cost_assessment",
        skill_name="成本评估报告",
        handler=create_cost_assessment,
    )

    # 注册技能：询价信息抽取（简化版，直接透传结构化输入）
    async def extract_rfq(inputs: dict) -> dict:
        """
        询价信息抽取。
        当前为确定性实现：如果输入已经是结构化RFQ，直接返回。
        后续可接入 LLM 做自然语言抽取。
        """
        # 如果已经有结构化字段，直接返回
        if "rfq_id" in inputs or "items" in inputs:
            return {
                "rfq_id": inputs.get("rfq_id", "RFQ-EXTRACTED"),
                "customer_name": inputs.get("customer_name", ""),
                "items": inputs.get("items", []),
                "delivery_date": inputs.get("delivery_date", ""),
                "quality_requirements": inputs.get("quality_requirements", ""),
                "extracted": True,
                "method": "structured_passthrough",
            }
        # 如果只有文本，做简单的关键字提取（占位，后续接LLM）
        text = inputs.get("text", "")
        return {
            "rfq_id": "RFQ-EXTRACTED",
            "customer_name": "",
            "items": [],
            "delivery_date": "",
            "quality_requirements": "",
            "extracted": False,
            "method": "text_placeholder",
            "note": "自然语言抽取需要 LLM 支持，当前仅返回占位结果",
        }

    service.register_skill(
        skill_id="quotation.extract_rfq",
        skill_name="询价信息抽取",
        handler=extract_rfq,
    )

    logger.info(f"报价智能体 AIP 服务创建完成，共 {len(service.list_skills())} 个技能")
    return service
