"""
生成四个智能体的 ACS（Agent Capability Specification）能力描述文件。
符合 ACPs-spec-ACS-v02.02 规范。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from acps_sdk.acs import (
    AgentCapabilitySpec,
    AgentProvider,
    AgentCapabilities,
    AgentEndPoint,
    AgentSkill,
    MutualTLSSecurityScheme,
    APIKeySecurityScheme,
)


def _base_provider() -> AgentProvider:
    return AgentProvider(
        country_code="CN",
        organization="AutoParts Intelligence",
        department="AI Agent Team",
        url="https://autoparts-agent.example.com",
        name="Dev Team",
        email="dev@autoparts-agent.example.com",
    )


def _base_capabilities() -> AgentCapabilities:
    return AgentCapabilities(
        streaming=False,
        notification=False,
        message_queue=[],
    )


def _base_security_schemes() -> dict:
    return {
        "mtls": MutualTLSSecurityScheme(
            type="mutualTLS",
            description="双向TLS认证，智能体间高安全级别通信",
        ),
        "apiKey": APIKeySecurityScheme(
            type="apiKey",
            description="API Key 认证，用于本地开发和测试",
            name="X-API-Key",
            in_location="header",
        ),
    }


def create_quotation_acs() -> AgentCapabilitySpec:
    """报价智能体 ACS"""
    return AgentCapabilitySpec(
        aic="TBD-QUOTATION-AGENT-001",  # 注册后由平台分配
        active=True,
        last_modified_time=datetime.now(timezone.utc).isoformat(),
        protocol_version="02.02",
        name="汽车零部件报价智能体",
        description="负责汽车零部件订单的报价处理，包括询价信息抽取、成本计算、报价草稿生成和成本评估。只负责报价建议和草稿生成，不做最终定价批准。",
        version="1.0.0",
        icon_url=None,
        documentation_url=None,
        web_app_url=None,
        provider=_base_provider(),
        security_schemes=_base_security_schemes(),
        end_points=[
            AgentEndPoint(
                url="http://localhost:8001/aip/quotation/rpc",
                transport="JSONRPC",
                security=[{"apiKey": []}],
            )
        ],
        capabilities=_base_capabilities(),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json", "text/markdown"],
        skills=[
            AgentSkill(
                id="quotation.extract_rfq",
                name="询价信息抽取",
                description="从客户询价单（文本/JSON）中抽取关键信息，包括客户名称、物料、数量、交期、质量要求等。配置 DeepSeek API Key 时启用 LLM 智能抽取，否则使用确定性规则回退。",
                version="1.1.0",
                tags=["报价", "信息抽取", "RFQ", "LLM"],
                examples=[
                    "提取这份询价单的关键信息",
                    "客户说要1000个齿轮，下月底交货，帮我整理成RFQ",
                ],
                input_modes=["text/plain", "application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quotation.analyze_rfq",
                name="RFQ 智能分析",
                description="深度分析 RFQ 描述，提取结构化信息并给出报价建议（复杂度评估、数量折扣、风险提示、价格调整因子）。支持 DeepSeek LLM 增强抽取。",
                version="1.0.0",
                tags=["报价", "RFQ分析", "定价建议", "LLM"],
                examples=[
                    "分析这个询价单的报价策略",
                    "给我报价建议",
                ],
                input_modes=["text/plain", "application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quotation.calculate_cost",
                name="成本计算",
                description="基于BOM结构、材料价格、加工工时和管理费率，计算产品的详细成本构成和报价建议。",
                version="1.0.0",
                tags=["报价", "成本计算", "BOM"],
                examples=[
                    "计算这个产品的成本",
                    "给我一个报价方案",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quotation.create_draft",
                name="报价草稿生成",
                description="根据成本计算结果和报价策略，生成正式报价单草稿，等待人工审批后生效。",
                version="1.0.0",
                tags=["报价", "草稿", "报价单"],
                examples=["生成报价单草稿"],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quotation.create_cost_assessment",
                name="成本评估报告",
                description="对采购方案进行成本影响评估，包括额外采购成本、交期影响、库存成本等。",
                version="1.0.0",
                tags=["报价", "成本评估", "采购影响"],
                examples=["评估这个采购方案的成本影响"],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
        ],
    )


def create_procurement_acs() -> AgentCapabilitySpec:
    """采购智能体 ACS"""
    return AgentCapabilitySpec(
        aic="TBD-PROCUREMENT-AGENT-001",
        active=True,
        last_modified_time=datetime.now(timezone.utc).isoformat(),
        protocol_version="02.02",
        name="汽车零部件采购智能体",
        description="负责物料需求计算、供应商方案对比和采购订单草稿生成。只提供采购建议和方案对比，供应商定标和PO发布需人工审批。",
        version="1.0.0",
        provider=_base_provider(),
        security_schemes=_base_security_schemes(),
        end_points=[
            AgentEndPoint(
                url="http://localhost:8001/aip/procurement/rpc",
                transport="JSONRPC",
                security=[{"apiKey": []}],
            )
        ],
        capabilities=_base_capabilities(),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json", "text/markdown"],
        skills=[
            AgentSkill(
                id="procurement.calculate_net_requirement",
                name="净需求计算",
                description="根据销售订单需求、现有库存、在途采购和安全库存，计算物料净需求量和建议采购量。",
                version="1.0.0",
                tags=["采购", "净需求", "MRP"],
                examples=[
                    "计算这批订单的物料缺口",
                    "还需要采购多少原材料",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="procurement.compare_supply_plans",
                name="供应方案对比",
                description="从多个供应商中筛选可选方案，按价格、交期、质量等级等维度对比，给出推荐方案。",
                version="1.0.0",
                tags=["采购", "供应商", "方案对比"],
                examples=[
                    "给我几个供应商的采购方案对比",
                    "推荐一个性价比最高的供应商",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="procurement.create_po_draft",
                name="采购订单草稿生成",
                description="根据选定的供应方案生成采购订单草稿，包含物料、数量、价格、交期等信息，等待人工审批。",
                version="1.0.0",
                tags=["采购", "PO草稿", "采购订单"],
                examples=["生成采购订单草稿"],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
        ],
    )


def create_tracking_acs() -> AgentCapabilitySpec:
    """跟单智能体 ACS"""
    return AgentCapabilitySpec(
        aic="TBD-TRACKING-AGENT-001",
        active=True,
        last_modified_time=datetime.now(timezone.utc).isoformat(),
        protocol_version="02.02",
        name="汽车零部件跟单智能体",
        description="负责订单交期跟踪、生产进度监控、风险识别和ETA计算。只提供风险预警和进度分析，不做生产调度和质量放行决策。",
        version="1.0.0",
        provider=_base_provider(),
        security_schemes=_base_security_schemes(),
        end_points=[
            AgentEndPoint(
                url="http://localhost:8001/aip/tracking/rpc",
                transport="JSONRPC",
                security=[{"apiKey": []}],
            )
        ],
        capabilities=_base_capabilities(),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json", "text/markdown"],
        skills=[
            AgentSkill(
                id="tracking.calculate_eta",
                name="交期预估",
                description="基于生产工单进度、工序节拍、异常历史和物料到货情况，计算订单预计交付时间。",
                version="1.0.0",
                tags=["跟单", "ETA", "交期"],
                examples=[
                    "这个订单什么时候能交",
                    "预计完工时间是多少",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="tracking.detect_risk",
                name="风险识别",
                description="实时监控订单执行状态，识别物料短缺、产能瓶颈、质量异常、供应商延期等风险并预警。",
                version="1.0.0",
                tags=["跟单", "风险预警", "异常检测"],
                examples=[
                    "这个订单有什么风险",
                    "检查一下订单状态",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="tracking.check_ship_gate",
                name="发运门禁检查",
                description="在发运前检查所有前置条件是否满足：生产完工、质量放行、资料包完整、客户确认等。",
                version="1.0.0",
                tags=["跟单", "发运", "门禁检查"],
                examples=[
                    "检查是否可以发运",
                    "发运条件都满足了吗",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
        ],
    )


def create_quality_document_acs() -> AgentCapabilitySpec:
    """质量文档智能体 ACS"""
    return AgentCapabilitySpec(
        aic="TBD-QUALITY-DOC-AGENT-001",
        active=True,
        last_modified_time=datetime.now(timezone.utc).isoformat(),
        protocol_version="02.02",
        name="汽车零部件质量文档智能体",
        description="负责质量证据收集、资料清单管理、完整性校验、资料包组卷和版本追溯。只负责文档管理和证据关联，不执行检验、不做质量放行、不确认根因。",
        version="1.0.0",
        provider=_base_provider(),
        security_schemes=_base_security_schemes(),
        end_points=[
            AgentEndPoint(
                url="http://localhost:8001/aip/quality-document/rpc",
                transport="JSONRPC",
                security=[{"apiKey": []}],
            )
        ],
        capabilities=_base_capabilities(),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json", "text/markdown"],
        skills=[
            AgentSkill(
                id="quality_document.build_checklist",
                name="资料清单生成",
                description="根据产品类型、客户要求和标准规范，生成质量资料包清单，明确需要哪些文档和证据。",
                version="1.0.0",
                tags=["质量文档", "资料清单", "Checklist"],
                examples=[
                    "这个产品需要哪些质量资料",
                    "生成资料清单",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quality_document.collect_evidence",
                name="质量证据收集",
                description="从MES、检验系统等来源收集与订单相关的质量证据，关联到资料包对应条目。",
                version="1.0.0",
                tags=["质量文档", "证据收集", "关联"],
                examples=[
                    "收集这个批次的质量证据",
                    "把检验报告关联到资料包",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quality_document.check_completeness",
                name="完整性校验",
                description="检查资料包的完整性，对比清单要求和已收集证据，给出缺失项和完成度百分比。",
                version="1.0.0",
                tags=["质量文档", "完整性", "校验"],
                examples=[
                    "检查资料是否齐全",
                    "资料包完整性如何",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
            AgentSkill(
                id="quality_document.build_package_draft",
                name="资料包草稿生成",
                description="根据资料清单和已收集的证据，生成质量资料包草稿，进行完整性校验，等待审批后正式发布。",
                version="1.0.0",
                tags=["质量文档", "资料包", "组卷"],
                examples=[
                    "生成质量资料包",
                    "检查资料完整性",
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            ),
        ],
    )


def generate_all(output_dir: str = "acs"):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    agents = {
        "quotation": create_quotation_acs(),
        "procurement": create_procurement_acs(),
        "tracking": create_tracking_acs(),
        "quality_document": create_quality_document_acs(),
    }

    for name, spec in agents.items():
        file_path = output_path / f"{name}_acs.json"
        file_path.write_text(spec.to_json(indent=2), encoding="utf-8")
        # 验证能正常加载
        loaded = AgentCapabilitySpec.from_file(str(file_path))
        print(f"✅ {name}: {loaded.name} ({len(loaded.skills)} 个技能)")
        for skill in loaded.skills:
            print(f"   - {skill.id}: {skill.name}")

    print(f"\n所有 ACS 文件已生成到: {output_path.absolute()}")


if __name__ == "__main__":
    generate_all()
