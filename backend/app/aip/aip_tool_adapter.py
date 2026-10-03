"""
AIP 工具适配器。

将 AIP 协议调用封装为与 tool_registry.invoke 相同的同步接口，
使得现有的 scenarios.py 等同步代码可以无缝切换到 AIP 协议调用。

使用方式：
    # 使用本地直接调用（默认）
    from app.tools.registry import tool_registry
    result = tool_registry.invoke(AgentType.QUOTATION, "quotation.calculate_cost", inputs)

    # 使用 AIP 协议调用
    from app.aip.aip_tool_adapter import aip_tool_adapter
    result = aip_tool_adapter.invoke(AgentType.QUOTATION, "quotation.calculate_cost", inputs)

环境变量：
    AIP_BASE_URL: AIP 服务的基础 URL（默认 http://127.0.0.1:8000）
    AIP_SESSION_ID: AIP 会话 ID（默认 aip-tool-adapter）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.domain.models import AgentType
from app.aip.aip_agent_client import AipAgentClient

logger = logging.getLogger(__name__)


class AipToolAdapter:
    """
    AIP 工具适配器。

    提供与 tool_registry 相同的 invoke 接口，但内部通过 AIP 协议
    调用远程智能体服务。用于将现有同步代码平滑迁移到 AIP 协议。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        session_id: str = "aip-tool-adapter",
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self._clients: dict[str, AipAgentClient] = {}
        # 技能ID到智能体类型的映射
        self._skill_agent_map: dict[str, str] = {
            # 报价智能体
            "quotation.calculate_cost": "quotation",
            "quotation.create_draft": "quotation",
            "quotation.create_cost_assessment": "quotation",
            "quotation.extract_rfq": "quotation",
            # 采购智能体
            "procurement.calculate_net_requirement": "procurement",
            "procurement.compare_supply_plans": "procurement",
            "procurement.create_po_draft": "procurement",
            # 跟单智能体
            "tracking.calculate_eta": "tracking",
            "tracking.detect_risk": "tracking",
            "tracking.check_ship_gate": "tracking",
            # 质量文档智能体
            "quality_document.build_checklist": "quality_document",
            "quality_document.collect_evidence": "quality_document",
            "quality_document.build_package_draft": "quality_document",
            "quality_document.check_completeness": "quality_document",
            # 兼容旧名称（quality.xxx -> quality_document）
            "quality.check_completeness": "quality_document",
            "quality.build_package_draft": "quality_document",
        }
        # 端点路径映射
        self._endpoint_map = {
            "quotation": "/aip/quotation/rpc",
            "procurement": "/aip/procurement/rpc",
            "tracking": "/aip/tracking/rpc",
            "quality_document": "/aip/quality-document/rpc",
        }

    def _create_client(self, agent_type: str) -> AipAgentClient:
        """创建一个新的 AIP 客户端（每次调用都创建新实例，避免事件循环问题）。"""
        endpoint = self._endpoint_map.get(agent_type)
        if endpoint is None:
            raise ValueError(f"未知智能体类型: {agent_type}")
        url = f"{self.base_url}{endpoint}"
        return AipAgentClient(
            partner_url=url,
            leader_id="aip-tool-adapter",
            identity_binding_enabled=False,
        )

    def _resolve_agent_type(self, agent_type_hint: AgentType, skill_id: str) -> str:
        """
        根据技能ID解析目标智能体类型。
        优先使用技能映射表，其次使用传入的 agent_type 提示。
        """
        # 先从技能映射表查找
        if skill_id in self._skill_agent_map:
            return self._skill_agent_map[skill_id]
        # 降级使用 agent_type 参数
        agent_type_str = agent_type_hint.value
        if agent_type_str in self._endpoint_map:
            return agent_type_str
        raise ValueError(f"无法确定技能 {skill_id} 对应的智能体类型")

    def invoke(
        self,
        agent_type: AgentType,
        skill_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        调用一个技能，通过 AIP 协议。

        接口与 tool_registry.invoke 完全兼容。

        每次调用都会创建新的 AIP 客户端和事件循环，
        以避免在同步上下文中复用导致的事件循环关闭问题。

        Args:
            agent_type: 智能体类型（用于提示，实际以 skill_id 映射为准）
            skill_id: 技能ID
            inputs: 输入参数

        Returns:
            技能执行结果
        """
        target_agent = self._resolve_agent_type(agent_type, skill_id)

        async def _do_invoke():
            client = self._create_client(target_agent)
            try:
                return await client.invoke_skill(
                    skill_id=skill_id,
                    inputs=inputs,
                    session_id=self.session_id,
                    timeout=30.0,
                )
            finally:
                await client.close()

        # 检查是否在已运行的事件循环中（如 FastAPI 的 async 端点）
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # 在新线程中运行，避免阻塞当前事件循环
                import threading
                result_container: dict[str, Any] = {}
                exc_container: list[BaseException] = []

                def _run_in_thread():
                    try:
                        result_container["result"] = asyncio.run(_do_invoke())
                    except BaseException as e:
                        exc_container.append(e)

                thread = threading.Thread(target=_run_in_thread)
                thread.start()
                thread.join()
                if exc_container:
                    raise exc_container[0]
                return result_container["result"]
        except RuntimeError:
            # 没有运行中的事件循环，直接使用 asyncio.run
            pass

        return asyncio.run(_do_invoke())

    async def invoke_async(
        self,
        agent_type: AgentType,
        skill_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        异步版本的 invoke。
        在异步上下文中使用此方法以获得更好的性能。
        """
        target_agent = self._resolve_agent_type(agent_type, skill_id)
        client = self._get_client(target_agent)
        return await client.invoke_skill(
            skill_id=skill_id,
            inputs=inputs,
            session_id=self.session_id,
            timeout=30.0,
        )

    def close(self):
        """关闭所有客户端连接。"""
        for client in self._clients.values():
            try:
                asyncio.run(client.close())
            except Exception:
                pass
        self._clients.clear()


# 全局单例
_aip_tool_adapter: AipToolAdapter | None = None


def get_aip_tool_adapter() -> AipToolAdapter:
    """获取全局 AIP 工具适配器实例。"""
    global _aip_tool_adapter
    if _aip_tool_adapter is None:
        base_url = os.environ.get("AIP_BASE_URL", "http://127.0.0.1:8000")
        session_id = os.environ.get("AIP_SESSION_ID", "aip-tool-adapter")
        _aip_tool_adapter = AipToolAdapter(base_url=base_url, session_id=session_id)
    return _aip_tool_adapter


def set_aip_tool_adapter(adapter: AipToolAdapter) -> None:
    """设置全局 AIP 工具适配器实例（用于测试或自定义配置）。"""
    global _aip_tool_adapter
    _aip_tool_adapter = adapter


# 便捷引用
aip_tool_adapter = get_aip_tool_adapter
