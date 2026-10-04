"""
质量文档智能体 AIP 服务。
技能分两类：
- *.real_*：调用真实业务链（real_order，数据来自真实 OpenMES）
- 其余：Mock 场景模拟技能（合成数据，仅用于 Mock 模拟）
"""
from __future__ import annotations

import logging

from app.aip.aip_agent_service import AipAgentService
from app.services import real_order
from app.tools.registry import tool_registry
from app.domain.models import AgentType

logger = logging.getLogger(__name__)


def create_quality_document_aip_service(aic: str = "local-quality-doc-001") -> AipAgentService:
    """创建质量文档智能体 AIP 服务实例。"""
    service = AipAgentService(
        agent_name="汽车零部件质量文档智能体",
        agent_type=AgentType.QUALITY_DOCUMENT.value,
        aic=aic,
    )

    # ===== 真实业务技能 =====

    async def real_quality_package(inputs: dict) -> dict:
        """真实质量资料包：质量问题/工程文档/检验记录与质量门禁（真实 OpenMES）。"""
        return await real_order.quality_package(str(inputs["work_order_id"]))

    service.register_skill(
        skill_id="quality.get_real_package",
        skill_name="真实质量资料包与门禁（OpenMES）",
        handler=real_quality_package,
    )

    async def real_assess_impact(inputs: dict) -> dict:
        """质量异常影响分析：问题/批次/门禁/生产进度交叉结论（讨论稿例子三）。"""
        return await real_order.assess_quality_impact(str(inputs["work_order_id"]))

    service.register_skill(
        skill_id="quality.assess_quality_impact",
        skill_name="质量异常影响分析",
        handler=real_assess_impact,
    )

    async def real_check_issue_closure(inputs: dict) -> dict:
        """只读校验 NCR disposition、纠正措施与关闭前置条件。"""
        return await real_order.assess_quality_issue_closure(
            str(inputs["work_order_id"]), str(inputs["issue_id"])
        )

    service.register_skill(
        skill_id="quality.check_issue_closure",
        skill_name="NCR 关闭前置校验",
        handler=real_check_issue_closure,
    )

    async def real_list_open_issues(inputs: dict) -> dict:
        """全厂未关闭质量问题队列（跨工单，真实 OpenMES，只读）。"""
        return await real_order.quality_todo_list()

    service.register_skill(
        skill_id="quality.list_open_issues",
        skill_name="全厂未关闭质量问题队列",
        handler=real_list_open_issues,
    )

    # ===== Mock 场景模拟技能 =====

    # 资料清单生成
    async def build_checklist(inputs: dict) -> dict:
        """
        根据产品类型和客户要求生成质量资料清单。
        """
        product_type = inputs.get("product_type", "standard")
        customer_requirements = inputs.get("customer_requirements", [])

        # 基础资料清单
        base_items = [
            {"item_id": "QC-001", "name": "来料检验报告", "required": True, "source": "IQC"},
            {"item_id": "QC-002", "name": "首件检验报告", "required": True, "source": "IPQC"},
            {"item_id": "QC-003", "name": "过程检验记录", "required": True, "source": "IPQC"},
            {"item_id": "QC-004", "name": "成品检验报告", "required": True, "source": "FQC"},
            {"item_id": "QC-005", "name": "材质证明", "required": True, "source": "Supplier"},
            {"item_id": "QC-006", "name": "尺寸检测报告", "required": True, "source": "QC"},
        ]

        # 根据产品类型添加特殊要求
        if product_type == "safety_critical":
            base_items.extend([
                {"item_id": "QC-007", "name": "PPAP文件包", "required": True, "source": "Quality"},
                {"item_id": "QC-008", "name": "MSA报告", "required": True, "source": "Quality"},
                {"item_id": "QC-009", "name": "SPC过程能力报告", "required": True, "source": "Quality"},
            ])

        # 根据客户要求追加
        for req in customer_requirements:
            if req == "rohs":
                base_items.append({
                    "item_id": "QC-010", "name": "RoHS检测报告", "required": True, "source": "Lab"
                })
            elif req == "reach":
                base_items.append({
                    "item_id": "QC-011", "name": "REACH符合性声明", "required": True, "source": "Quality"
                })

        return {
            "checklist_id": inputs.get("checklist_id", "CL-NEW"),
            "project_id": inputs.get("project_id", ""),
            "product_type": product_type,
            "total_items": len(base_items),
            "required_items": [i for i in base_items if i["required"]],
            "all_items": base_items,
        }

    service.register_skill(
        skill_id="quality_document.build_checklist",
        skill_name="资料清单生成",
        handler=build_checklist,
    )

    # 质量证据收集
    async def collect_evidence(inputs: dict) -> dict:
        """
        收集质量证据并关联到资料包条目。
        当前为确定性实现，模拟从MES/检验系统收集。
        """
        checklist = inputs.get("checklist_items", [])
        collected = inputs.get("available_evidence", [])

        evidence_map = {}
        for item in checklist:
            item_id = item.get("item_id", "")
            # 模拟：假设有一半的条目能找到证据
            if item_id in collected or len(evidence_map) < len(checklist) // 2:
                evidence_map[item_id] = {
                    "evidence_id": f"EVID-{item_id}",
                    "collected": True,
                    "source": item.get("source", "unknown"),
                    "collected_at": inputs.get("collected_at", ""),
                }
            else:
                evidence_map[item_id] = {
                    "evidence_id": None,
                    "collected": False,
                    "source": item.get("source", "unknown"),
                }

        return {
            "package_id": inputs.get("package_id", "PKG-NEW"),
            "total_items": len(checklist),
            "collected_count": sum(1 for v in evidence_map.values() if v["collected"]),
            "evidence_map": evidence_map,
        }

    service.register_skill(
        skill_id="quality_document.collect_evidence",
        skill_name="质量证据收集",
        handler=collect_evidence,
    )

    # 资料包草稿生成
    async def build_package_draft(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.QUALITY_DOCUMENT, "quality.build_package_draft", inputs
        )

    service.register_skill(
        skill_id="quality_document.build_package_draft",
        skill_name="资料包草稿生成",
        handler=build_package_draft,
    )

    # 完整性检查
    async def check_completeness(inputs: dict) -> dict:
        return tool_registry.invoke(
            AgentType.QUALITY_DOCUMENT, "quality.check_completeness", inputs
        )

    service.register_skill(
        skill_id="quality_document.check_completeness",
        skill_name="完整性检查",
        handler=check_completeness,
    )

    logger.info(f"质量文档智能体 AIP 服务创建完成，共 {len(service.list_skills())} 个技能")
    return service
