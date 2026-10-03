"""
AIP 智能体互联模块。

将四个智能体（报价、采购、跟单、质量文档）以标准 AIP 协议暴露，
实现智能体间的标准化互联。

模块结构：
- aip_agent_service.py: AIP 服务基类
- aip_agent_client.py: AIP 客户端封装
- agents/: 四个智能体的具体 AIP 服务实现
- acs/: 四个智能体的 ACS 能力描述文件
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.aip.aip_agent_service import register_aip_agent_router
from app.aip.agents.quotation_aip import create_quotation_aip_service
from app.aip.agents.procurement_aip import create_procurement_aip_service
from app.aip.agents.tracking_aip import create_tracking_aip_service
from app.aip.agents.quality_document_aip import create_quality_document_aip_service

logger = logging.getLogger(__name__)

# 全局服务实例
_quotation_service = None
_procurement_service = None
_tracking_service = None
_quality_document_service = None


def init_aip_agents(app: FastAPI) -> None:
    """
    初始化所有 AIP 智能体服务并注册到 FastAPI。

    每个智能体都有独立的 RPC 端点和健康检查端点：
    - 报价智能体: /aip/quotation/rpc, /aip/quotation/health
    - 采购智能体: /aip/procurement/rpc, /aip/procurement/health
    - 跟单智能体: /aip/tracking/rpc, /aip/tracking/health
    - 质量文档智能体: /aip/quality-document/rpc, /aip/quality-document/health
    """
    global _quotation_service, _procurement_service
    global _tracking_service, _quality_document_service

    # 创建四个智能体服务
    _quotation_service = create_quotation_aip_service()
    _procurement_service = create_procurement_aip_service()
    _tracking_service = create_tracking_aip_service()
    _quality_document_service = create_quality_document_aip_service()

    # 统一能力目录：声明元数据（REAL_SKILL_TOOLS）∩ AIP 运行时注册，
    # 带字段校验与双向差异报告；协调者选择、真实表面白名单、ACS 生成
    # 共用这一份目录（P0-5 收口）。
    from app.services.capability_catalog import (
        allowed_skill_ids_by_agent,
        build_capability_catalog,
        store_runtime_catalog,
    )
    from app.services.coordinator import REAL_SKILL_TOOLS

    registered_by_agent = {
        "quotation": set(_quotation_service.registered_skill_ids()),
        "procurement": set(_procurement_service.registered_skill_ids()),
        "tracking": set(_tracking_service.registered_skill_ids()),
        "quality-document": set(_quality_document_service.registered_skill_ids()),
    }
    capability_catalog = build_capability_catalog(REAL_SKILL_TOOLS, registered_by_agent)
    for rejection in capability_catalog["rejections"]:
        logger.warning("能力目录拒绝条目: [%s] %s", rejection["kind"], rejection["reason"])
    store_runtime_catalog(capability_catalog)

    # 真实表面只公开目录中的真实只读技能；Mock 技能仍可在开发
    # 表面（MOCK_DEMO_ENABLED=true 或非 real 适配器）使用。
    from app.runtime.surface import mock_demo_enabled
    if not mock_demo_enabled():
        allowed = allowed_skill_ids_by_agent(capability_catalog)
        _quotation_service.restrict_skills(allowed["quotation"])
        _procurement_service.restrict_skills(allowed["procurement"])
        _tracking_service.restrict_skills(allowed["tracking"])
        _quality_document_service.restrict_skills(allowed["quality-document"])

    # 注册到 FastAPI
    register_aip_agent_router(
        app, _quotation_service, "/aip/quotation/rpc"
    )
    register_aip_agent_router(
        app, _procurement_service, "/aip/procurement/rpc"
    )
    register_aip_agent_router(
        app, _tracking_service, "/aip/tracking/rpc"
    )
    register_aip_agent_router(
        app, _quality_document_service, "/aip/quality-document/rpc"
    )

    # 注册 AIP 总览端点
    @app.get("/aip/overview", tags=["AIP"])
    async def aip_overview():
        return {
            "protocol_version": "02.02",
            "agents": [
                {
                    "name": _quotation_service.agent_name,
                    "type": _quotation_service.agent_type,
                    "aic": _quotation_service.aic,
                    "skills": _quotation_service.list_skills(),
                    "rpc_endpoint": "/aip/quotation/rpc",
                    "health_endpoint": "/aip/quotation/health",
                },
                {
                    "name": _procurement_service.agent_name,
                    "type": _procurement_service.agent_type,
                    "aic": _procurement_service.aic,
                    "skills": _procurement_service.list_skills(),
                    "rpc_endpoint": "/aip/procurement/rpc",
                    "health_endpoint": "/aip/procurement/health",
                },
                {
                    "name": _tracking_service.agent_name,
                    "type": _tracking_service.agent_type,
                    "aic": _tracking_service.aic,
                    "skills": _tracking_service.list_skills(),
                    "rpc_endpoint": "/aip/tracking/rpc",
                    "health_endpoint": "/aip/tracking/health",
                },
                {
                    "name": _quality_document_service.agent_name,
                    "type": _quality_document_service.agent_type,
                    "aic": _quality_document_service.aic,
                    "skills": _quality_document_service.list_skills(),
                    "rpc_endpoint": "/aip/quality-document/rpc",
                    "health_endpoint": "/aip/quality-document/health",
                },
            ],
            "note": "本地开发模式，身份绑定已禁用。生产环境应启用 mTLS 和身份绑定。",
        }

    # 注册统一能力目录端点（目录来源、构建时间与拒绝报告可追溯）
    @app.get("/aip/capability-catalog", tags=["AIP"])
    async def aip_capability_catalog():
        from app.services.capability_catalog import get_runtime_catalog

        current = get_runtime_catalog()
        if current is None:
            from app.services.capability_catalog import declared_only_catalog

            return declared_only_catalog()
        return current

    logger.info("✅ 所有 AIP 智能体服务已初始化并注册")


def get_agent_service(agent_type: str):
    """获取指定类型的 AIP 服务实例（主要用于测试）。"""
    services = {
        "quotation": _quotation_service,
        "procurement": _procurement_service,
        "tracking": _tracking_service,
        "quality_document": _quality_document_service,
    }
    return services.get(agent_type)
