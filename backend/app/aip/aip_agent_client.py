"""
AIP 智能体客户端封装。
用于调用其他智能体的 AIP 服务，遵循 AIP 协议标准。
"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4
from typing import Any, Optional

from acps_sdk.aip import AipRpcClient, TaskState

logger = logging.getLogger(__name__)


class AipAgentClient:
    """
    AIP 智能体客户端，封装对另一个智能体的调用流程。

    典型调用流程：
    1. start_task: 启动任务，得到 task_id
    2. 轮询 get_task_status: 等待任务完成
    3. complete_task: 确认任务完成（可选）
    4. 获取结果
    """

    def __init__(
        self,
        partner_url: str,
        leader_id: str = "local-leader-001",
        ssl_context=None,
        identity_binding_enabled: bool = False,
        expected_partner_aic: str | None = None,
        transport=None,
    ):
        self.partner_url = partner_url
        self.leader_id = leader_id
        self.ssl_context = ssl_context
        self.identity_binding_enabled = identity_binding_enabled
        self.expected_partner_aic = expected_partner_aic
        self.transport = transport
        self._client: Optional[AipRpcClient] = None

    async def _get_client(self) -> AipRpcClient:
        if self._client is None:
            self._client = AipRpcClient(
                partner_url=self.partner_url,
                leader_id=self.leader_id,
                ssl_context=self.ssl_context,
                identity_binding_enabled=self.identity_binding_enabled,
                expected_partner_aic=self.expected_partner_aic,
                transport=self.transport,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None

    async def invoke_skill(
        self,
        skill_id: str,
        inputs: dict[str, Any],
        session_id: str = "default-session",
        timeout: float = 30.0,
        poll_interval: float = 0.2,
    ) -> dict[str, Any]:
        """
        调用一个技能，等待执行完成并返回结果。
        这是一个便捷方法，封装了 start → poll → get result 的完整流程。

        Args:
            skill_id: 技能ID，如 "quotation.calculate_cost"
            inputs: 技能输入参数
            session_id: 会话ID
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）

        Returns:
            技能执行结果字典

        Raises:
            TimeoutError: 任务超时
            RuntimeError: 任务执行失败或被取消
        """
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout 和 poll_interval 必须大于零")
        if not isinstance(inputs, dict):
            raise ValueError("技能输入必须是 JSON 对象")
        client = await self._get_client()
        # Allocate before start: even a lost/late start response can be cancelled.
        task_id = f"task-{uuid4()}"

        async def invoke() -> dict[str, Any]:
            result = await client.start_task(
                session_id=session_id,
                user_input=f"{skill_id}|{json.dumps(inputs, ensure_ascii=False)}",
                task_id=task_id,
            )
            if result.taskId != task_id:
                raise RuntimeError("AIP 响应任务编号与请求不一致")
            if result.status.state not in self._terminal_states():
                result = await self._wait_for_completion(
                    task_id, session_id, timeout, poll_interval
                )
            state = result.status.state
            if state in {TaskState.Failed, TaskState.Rejected, TaskState.Canceled}:
                message = next(
                    (item.text for item in (result.status.dataItems or []) if getattr(item, "text", None)),
                    str(state),
                )
                raise RuntimeError(f"技能 {skill_id} 未成功: {message}")
            data = self._extract_result(result)
            if state == TaskState.AwaitingCompletion:
                completed = await client.complete_task(task_id, session_id)
                if completed.status.state != TaskState.Completed:
                    raise RuntimeError("AIP 任务完成确认未被接受")
            return data

        try:
            # Bound the whole exchange, including network calls and acknowledgement.
            return await asyncio.wait_for(invoke(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await self._cancel_remote(client, task_id, session_id)
            raise

    @staticmethod
    def _terminal_states() -> set[TaskState]:
        return {
            TaskState.Completed, TaskState.Failed, TaskState.Rejected,
            TaskState.Canceled, TaskState.AwaitingCompletion,
        }

    @staticmethod
    async def _cancel_remote(client, task_id: str, session_id: str) -> None:
        try:
            await asyncio.shield(asyncio.wait_for(client.cancel_task(task_id, session_id), timeout=2.0))
        except (Exception, asyncio.CancelledError):
            logger.warning("未能确认远端 AIP 任务已取消: %s", task_id)

    async def _wait_for_completion(
        self,
        task_id: str,
        session_id: str,
        timeout: float = 30.0,
        poll_interval: float = 0.2,
    ):
        """轮询等待任务到达终态或 awaiting-completion。"""
        client = await self._get_client()
        deadline = asyncio.get_running_loop().time() + timeout
        result = None
        while asyncio.get_running_loop().time() < deadline:
            result = await client.get_task(task_id, session_id)
            if result.taskId != task_id:
                raise RuntimeError("AIP 响应任务编号与请求不一致")
            state = result.status.state
            if state in self._terminal_states():
                return result
            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"任务 {task_id} 超时（{timeout}s）"
        )

    def _extract_result(self, task_result) -> dict[str, Any]:
        """从 TaskResult 中提取技能执行结果。"""
        from acps_sdk.aip.aip_base_model import StructuredDataItem, TextDataItem

        for product in task_result.products or []:
            if product.dataItems:
                for item in product.dataItems:
                    if isinstance(item, StructuredDataItem):
                        return dict(item.data)
                    if isinstance(item, TextDataItem) and item.text:
                        return {"text": item.text}
        raise RuntimeError("AIP 成功任务没有可读取的结果产物")


# ========== 客户端工厂 ==========

class AipClientFactory:
    """
    AIP 客户端工厂。
    管理所有智能体客户端的创建和复用。
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self._clients: dict[str, AipAgentClient] = {}

    def get_client(self, agent_type: str) -> AipAgentClient:
        """
        获取指定类型智能体的 AIP 客户端。

        Args:
            agent_type: 智能体类型，如 "quotation", "procurement", "tracking", "quality_document"
        """
        if agent_type not in self._clients:
            endpoint_map = {
                "quotation": "/aip/quotation/rpc",
                "procurement": "/aip/procurement/rpc",
                "tracking": "/aip/tracking/rpc",
                "quality_document": "/aip/quality-document/rpc",
            }
            endpoint = endpoint_map.get(agent_type, f"/aip/{agent_type}/rpc")
            url = f"{self.base_url}{endpoint}"
            self._clients[agent_type] = AipAgentClient(
                partner_url=url,
                leader_id="local-coordinator-001",
            )
            logger.info(f"创建 AIP 客户端: {agent_type} @ {url}")
        return self._clients[agent_type]

    async def close_all(self):
        """关闭所有客户端连接。"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()


# 全局单例
_aip_client_factory: Optional[AipClientFactory] = None


def get_aip_client_factory() -> AipClientFactory:
    """获取全局 AIP 客户端工厂。"""
    global _aip_client_factory
    if _aip_client_factory is None:
        _aip_client_factory = AipClientFactory()
    return _aip_client_factory


def set_aip_client_factory(factory: AipClientFactory):
    """设置全局 AIP 客户端工厂（用于测试或自定义配置）。"""
    global _aip_client_factory
    _aip_client_factory = factory
