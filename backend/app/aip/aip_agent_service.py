"""
AIP 智能体服务基类。
将现有的 Agent + Tools 架构包装为标准 AIP RPC 服务。
遵循 ACPs-spec-AIP-v02.02 规范。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from acps_sdk.aip.aip_base_model import (
    Product,
    StructuredDataItem,
    TaskCommand,
    TaskResult,
    TaskState,
    TaskStatus,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    TaskManager,
)
from acps_sdk.aip.aip_rpc_model import RpcRequest, RpcResponse
from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


class AipAgentService(CommandHandlers):
    """
    AIP 智能体服务基类。
    每个具体智能体服务继承此类，注册自己的技能处理函数。

    任务状态流转：
    start → accepted → working → awaiting-completion → complete → completed
                          ↘ failed / canceled
    """

    def __init__(
        self,
        agent_name: str,
        agent_type: str,
        aic: str = "local-dev",
    ):
        super().__init__(
            on_start=self._handle_start,
            on_get=self._handle_get,
            on_cancel=self._handle_cancel,
            on_complete=self._handle_complete,
        )
        self.agent_name = agent_name
        self.agent_type = agent_type
        self.aic = aic
        # 技能ID → 处理函数映射
        # 处理函数签名: async def handler(inputs: dict) -> dict
        self._skill_handlers: Dict[str, Callable[[dict], Awaitable[dict]]] = {}
        # 技能ID → 技能名称映射
        self._skill_names: Dict[str, str] = {}

    def register_skill(
        self,
        skill_id: str,
        skill_name: str,
        handler: Callable[[dict], Awaitable[dict]],
    ) -> None:
        """注册一个技能及其处理函数。"""
        self._skill_handlers[skill_id] = handler
        self._skill_names[skill_id] = skill_name
        logger.info(f"[{self.agent_name}] 注册技能: {skill_id} ({skill_name})")

    def list_skills(self) -> list[dict]:
        """列出所有已注册的技能。"""
        return [
            {"id": sid, "name": self._skill_names.get(sid, sid)}
            for sid in self._skill_handlers
        ]

    def registered_skill_ids(self) -> list[str]:
        """运行时已注册的技能 ID（能力目录交叉验证的运行时事实来源）。"""
        return list(self._skill_handlers.keys())

    def restrict_skills(self, allowed_ids: set[str]) -> None:
        """在真实运行表面移除 Mock 技能，防止 AIP 路由越过业务边界。"""
        for skill_id in list(self._skill_handlers):
            if skill_id not in allowed_ids:
                self._skill_handlers.pop(skill_id, None)
                self._skill_names.pop(skill_id, None)

    async def _handle_start(
        self, command: TaskCommand, task: Optional[TaskResult]
    ) -> TaskResult:
        """
        处理 start 命令：解析技能ID和输入，异步执行任务。
        """
        # 幂等：如果任务已存在，直接返回
        if task is not None:
            return task

        # 创建任务（初始状态 accepted）
        task_result = TaskManager.create_task(command)
        task_id = task_result.taskId

        # 解析技能ID和输入
        skill_id, inputs = self._parse_skill_input(command)

        # 验证技能是否存在
        if skill_id not in self._skill_handlers:
            error_item = TextDataItem(
                text=f"未知技能: {skill_id}。可用技能: {', '.join(self._skill_handlers.keys())}"
            )
            return TaskManager.update_task_status(
                task_id, TaskState.Rejected, data_items=[error_item]
            )

        # 标记为 working，异步执行
        TaskManager.update_task_status(task_id, TaskState.Working)

        # 后台执行任务（不阻塞 start 响应）
        asyncio.create_task(self._execute_skill(task_id, skill_id, inputs))

        # 更新发送者信息
        task_result = TaskManager.get_task(task_id)
        task_result.senderId = self.aic
        task_result.senderRole = "partner"
        return task_result

    async def _handle_get(
        self, command: TaskCommand, task: TaskResult
    ) -> TaskResult:
        """处理 get 命令：返回任务当前状态。"""
        task.senderId = self.aic
        return task

    async def _handle_cancel(
        self, command: TaskCommand, task: TaskResult
    ) -> TaskResult:
        """处理 cancel 命令：取消任务。"""
        if task.status.state in {
            TaskState.Completed,
            TaskState.Failed,
            TaskState.Rejected,
            TaskState.Canceled,
        }:
            return task
        TaskManager.add_command_to_history(task.taskId, command)
        result = TaskManager.update_task_status(task.taskId, TaskState.Canceled)
        result.senderId = self.aic
        return result

    async def _handle_complete(
        self, command: TaskCommand, task: TaskResult
    ) -> TaskResult:
        """处理 complete 命令：Leader 确认任务完成。"""
        if task.status.state == TaskState.AwaitingCompletion:
            TaskManager.add_command_to_history(task.taskId, command)
            result = TaskManager.update_task_status(task.taskId, TaskState.Completed)
            result.senderId = self.aic
            return result
        TaskManager.add_command_to_history(task.taskId, command)
        task.senderId = self.aic
        return task

    def _parse_skill_input(self, command: TaskCommand) -> tuple[str, dict]:
        """
        从 TaskCommand 中解析技能ID和输入参数。
        支持两种格式：
        1. dataItems 中的 StructuredDataItem（推荐）
        2. dataItems 中的 TextDataItem 文本格式: "skill_id|json_inputs"
        """
        skill_id = ""
        inputs: dict[str, Any] = {}

        if not command.dataItems:
            return skill_id, inputs

        # 尝试从结构化数据项中解析
        for item in command.dataItems:
            if isinstance(item, StructuredDataItem) and getattr(item, "kind", "") == "skill":
                skill_id = getattr(item, "name", "") or ""
                if hasattr(item, "data") and item.data:
                    inputs = dict(item.data)
                break

        # 如果没有结构化数据，尝试从文本解析
        if not skill_id:
            text_content = ""
            for item in command.dataItems:
                if isinstance(item, TextDataItem):
                    text_content = item.text or ""
                    break

            if text_content and "|" in text_content:
                parts = text_content.split("|", 1)
                skill_id = parts[0].strip()
                try:
                    inputs = json.loads(parts[1].strip())
                except json.JSONDecodeError:
                    inputs = {"raw_text": parts[1].strip()}
            elif text_content:
                skill_id = text_content.strip()

        return skill_id, inputs

    async def _execute_skill(self, task_id: str, skill_id: str, inputs: dict):
        """
        后台执行技能，更新任务状态和产出物。
        """
        try:
            logger.info(f"[{self.agent_name}] 开始执行技能: {skill_id}, task_id={task_id}")

            handler = self._skill_handlers[skill_id]
            result_data = await handler(inputs)

            # 构造产出物
            products = [
                Product(
                    id=f"prod-{task_id}",
                    name=self._skill_names.get(skill_id, skill_id),
                    description=f"技能 {skill_id} 的执行结果",
                    dataItems=[
                        StructuredDataItem(
                            kind="result",
                            name=skill_id,
                            data=result_data,
                            mimeType="application/json",
                        )
                    ],
                )
            ]

            # 设置产出物
            TaskManager.set_products(task_id, products)

            # 状态变为 awaiting-completion，等待 Leader 确认
            TaskManager.update_task_status(task_id, TaskState.AwaitingCompletion)

            logger.info(
                f"[{self.agent_name}] 技能执行完成: {skill_id}, task_id={task_id}"
            )

        except Exception as e:
            logger.exception(
                f"[{self.agent_name}] 技能执行失败: {skill_id}, task_id={task_id}, error={e}"
            )
            error_item = TextDataItem(text=f"执行失败: {str(e)}")
            TaskManager.update_task_status(
                task_id, TaskState.Failed, data_items=[error_item]
            )


def register_aip_agent_router(
    app: FastAPI,
    service: AipAgentService,
    endpoint: str,
    *,
    identity_binding_enabled: bool = False,
) -> None:
    """
    将一个 AIP 智能体服务注册到 FastAPI 应用。

    Args:
        app: FastAPI 应用实例
        service: AIP 智能体服务实例
        endpoint: RPC 端点路径，如 "/aip/quotation/rpc"
        identity_binding_enabled: 是否启用身份绑定（本地开发关闭）
    """
    from acps_sdk.aip.aip_rpc_server import handle_rpc_request

    @app.post(endpoint, response_model=RpcResponse, tags=["AIP"])
    async def aip_rpc_endpoint(request: Request):
        return await handle_rpc_request(
            request,
            service,
            local_aic=service.aic,
            identity_binding_enabled=identity_binding_enabled,
        )

    # 也注册一个健康检查端点
    health_endpoint = endpoint.replace("/rpc", "/health")

    @app.get(health_endpoint, tags=["AIP"])
    async def aip_health_check():
        return {
            "status": "healthy",
            "agent": service.agent_name,
            "agent_type": service.agent_type,
            "aic": service.aic,
            "skills": service.list_skills(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    logger.info(f"已注册 AIP 服务: {service.agent_name} @ {endpoint}")
