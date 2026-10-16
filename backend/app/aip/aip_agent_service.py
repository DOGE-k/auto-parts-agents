"""AIP 2.2 服务：独立任务存储、任务归属和 SDK 数据项适配。"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from acps_sdk.aip.aip_base_model import (
    Product, StructuredDataItem, TaskCommand, TaskCommandType, TaskResult,
    TaskState, TaskStatus, TextDataItem,
)
from acps_sdk.aip.aip_rpc_model import JSONRPCError, RpcRequest, RpcResponse
from acps_sdk.aip.aip_rpc_server import CommandHandlers
from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)
_NATURAL_LANGUAGE_SKILL = "__natural_language__"
_TERMINAL_STATES = {
    TaskState.Completed, TaskState.Failed, TaskState.Rejected, TaskState.Canceled,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class _TaskOwner:
    # senderId is authenticated only when the router's SDK identity binding is on.
    # session/group/user are caller-declared partitions, never login identities.
    sender_id: str
    sender_role: str
    session_id: Optional[str]
    group_id: Optional[str]
    user_id: Optional[str]

    @classmethod
    def from_command(cls, command: TaskCommand) -> "_TaskOwner":
        user_id = (command.commandParams or {}).get("userId")
        if user_id is not None and (not isinstance(user_id, str) or not user_id.strip()):
            raise ValueError("commandParams.userId 必须为非空字符串")
        return cls(command.senderId, command.senderRole, command.sessionId,
                   command.groupId, user_id)


class AipAgentService(CommandHandlers):
    """每个服务实例持有自己的任务；统一 dispatch 在回调之前检查归属。"""

    def __init__(self, agent_name: str, agent_type: str, aic: str = "local-dev"):
        super().__init__(on_start=self._handle_start, on_get=self._handle_get,
                         on_cancel=self._handle_cancel, on_complete=self._handle_complete)
        self.agent_name = agent_name
        self.agent_type = agent_type
        self.aic = aic
        self._skill_handlers: dict[str, Callable[[dict], Awaitable[dict]]] = {}
        self._skill_names: dict[str, str] = {}
        self._tasks: dict[str, TaskResult] = {}
        self._owners: dict[str, _TaskOwner] = {}
        self._executions: dict[str, asyncio.Task] = {}
        self._dispatch_lock = asyncio.Lock()
        self._natural_language_handler: Optional[
            Callable[[str, TaskCommand], Awaitable[dict]]
        ] = None
        self._natural_language_skill_id = _NATURAL_LANGUAGE_SKILL
        self._natural_language_skill_name = "智能问答"

    def register_skill(self, skill_id: str, skill_name: str,
                       handler: Callable[[dict], Awaitable[dict]]) -> None:
        if skill_id == _NATURAL_LANGUAGE_SKILL:
            raise ValueError("该技能 ID 为内部保留值")
        self._skill_handlers[skill_id] = handler
        self._skill_names[skill_id] = skill_name

    def set_natural_language_handler(
        self, handler: Callable[[str, TaskCommand], Awaitable[dict]], *,
        skill_id: str = _NATURAL_LANGUAGE_SKILL, skill_name: str = "智能问答",
    ) -> None:
        """接收自然语言，并向处理器传服务保存的原始命令上下文。

        默认的内部技能只接受普通文本。显式指定公开 skill_id 后也接受
        {skill_id, inputs: {question: ...}}，但身份始终来自命令而非 inputs。
        """
        if not skill_id.strip() or skill_id in self._skill_handlers:
            raise ValueError("自然语言技能 ID 为空或与已注册技能冲突")
        self._natural_language_handler = handler
        self._natural_language_skill_id = skill_id
        self._natural_language_skill_name = skill_name

    def list_skills(self) -> list[dict]:
        skills = [{"id": sid, "name": self._skill_names.get(sid, sid)}
                  for sid in self._skill_handlers]
        if self._natural_language_handler and self._natural_language_skill_id != _NATURAL_LANGUAGE_SKILL:
            skills.append({"id": self._natural_language_skill_id,
                           "name": self._natural_language_skill_name})
        return skills

    def registered_skill_ids(self) -> list[str]:
        return [item["id"] for item in self.list_skills()]

    def restrict_skills(self, allowed_ids: set[str]) -> None:
        for skill_id in list(self._skill_handlers):
            if skill_id not in allowed_ids:
                self._skill_handlers.pop(skill_id, None)
                self._skill_names.pop(skill_id, None)
        if (self._natural_language_skill_id != _NATURAL_LANGUAGE_SKILL
                and self._natural_language_skill_id not in allowed_ids):
            self._natural_language_handler = None

    async def dispatch_rpc(self, rpc_request: RpcRequest) -> RpcResponse:
        """SDK dispatch_request 回调；所有命令先经过同一归属检查。

        不使用 SDK 全局 TaskManager，避免相同 taskId 跨智能体共享状态。
        TLS 对端验证由 SDK handle_rpc_request 在进入本方法前完成。
        """
        command = rpc_request.params.command

        def error(code: int, message: str, data: Any = None) -> RpcResponse:
            return RpcResponse(id=rpc_request.id, error=JSONRPCError(
                code=code, message=message, data=data))

        try:
            owner = _TaskOwner.from_command(command)
        except ValueError as exc:
            return error(-32602, "Invalid params", str(exc))

        async with self._dispatch_lock:
            task_id = command.taskId
            task = self._tasks.get(task_id) if task_id else None
            if task is not None and self._owners.get(task_id) != owner:
                return error(-32003, "Task access denied")
            if task is not None and command.command == TaskCommandType.Start:
                original = task.commandHistory[0]
                if original.dataItems != command.dataItems or original.commandParams != command.commandParams:
                    return error(-32602, "Task ID already belongs to a different start request")
            if command.command != TaskCommandType.Start and task is None:
                return error(-32001, "Task not found")
            try:
                if command.command == TaskCommandType.Start:
                    if task is None:
                        # Permit protocol Start without taskId; never mutate the caller's model.
                        command = command.model_copy(deep=True)
                        command.taskId = task_id or str(uuid.uuid4())
                    result = await self._handle_start(command, task)
                elif command.command == TaskCommandType.Get:
                    result = await self._handle_get(command, task)
                elif command.command == TaskCommandType.Cancel:
                    result = await self._handle_cancel(command, task)
                elif command.command == TaskCommandType.Complete:
                    result = await self._handle_complete(command, task)
                else:
                    return error(-32602, "Invalid params", "本服务不支持此任务命令")
            except ValueError as exc:
                return error(-32602, "Invalid params", str(exc))
            # A response is a snapshot: background work must not alter an earlier reply.
            return RpcResponse(id=rpc_request.id, result=result.model_copy(deep=True))

    def _update_status(self, task_id: str, state: TaskState,
                       data_items: Optional[list] = None) -> TaskResult:
        task = self._tasks[task_id]
        status = TaskStatus(state=state, stateChangedAt=_now(), dataItems=data_items or [])
        task.status = status
        task.statusHistory = [*(task.statusHistory or []), status]
        task.sentAt = _now()
        return task

    async def _handle_start(self, command: TaskCommand,
                            task: Optional[TaskResult]) -> TaskResult:
        if task is not None:
            return task
        skill_id, inputs = self._parse_skill_input(command)
        task_id = command.taskId
        if not task_id:
            raise ValueError("缺少任务 ID")
        status = TaskStatus(state=TaskState.Accepted, stateChangedAt=_now(), dataItems=[])
        result = TaskResult(id=f"result-{uuid.uuid4()}", sentAt=_now(),
                            senderRole="partner", senderId=self.aic, taskId=task_id,
                            sessionId=command.sessionId, groupId=command.groupId,
                            status=status, commandHistory=[command.model_copy(deep=True)],
                            statusHistory=[status])
        self._tasks[task_id] = result
        self._owners[task_id] = _TaskOwner.from_command(command)
        natural_language = (self._natural_language_handler is not None
                            and skill_id == self._natural_language_skill_id)
        if skill_id not in self._skill_handlers and not natural_language:
            return self._update_status(task_id, TaskState.Rejected,
                [TextDataItem(text=f"未知技能: {skill_id}")])
        self._update_status(task_id, TaskState.Working)
        self._executions[task_id] = asyncio.create_task(
            self._execute_skill(task_id, skill_id, inputs, command.model_copy(deep=True)))
        return result

    async def _handle_get(self, command: TaskCommand, task: TaskResult) -> TaskResult:
        return task

    async def _handle_cancel(self, command: TaskCommand, task: TaskResult) -> TaskResult:
        if task.status.state not in _TERMINAL_STATES:
            task.commandHistory = [*(task.commandHistory or []), command.model_copy(deep=True)]
            # Set terminal state before cancellation: even a handler swallowing cancellation
            # cannot publish a late result and revive the task.
            self._update_status(task.taskId, TaskState.Canceled)
            execution = self._executions.get(task.taskId)
            if execution is not None:
                execution.cancel()
        return task

    async def _handle_complete(self, command: TaskCommand, task: TaskResult) -> TaskResult:
        task.commandHistory = [*(task.commandHistory or []), command.model_copy(deep=True)]
        if task.status.state == TaskState.AwaitingCompletion:
            self._update_status(task.taskId, TaskState.Completed)
        return task

    def _parse_skill_input(self, command: TaskCommand) -> tuple[str, dict]:
        """支持 SDK 2.2 data={skill_id,inputs} 及旧版 skill|JSON 文本。"""
        items = command.dataItems or []
        if len(items) != 1:
            raise ValueError("start 必须提供一个技能调用数据项")
        item = items[0]
        if isinstance(item, StructuredDataItem):
            skill_id = item.data.get("skill_id")
            inputs = item.data.get("inputs", {})
        elif isinstance(item, TextDataItem):
            text = item.text.strip()
            if "|" in text:
                skill_id, raw = text.split("|", 1)
                try:
                    inputs = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError("技能参数不是有效 JSON") from exc
            elif text in self._skill_handlers:
                skill_id, inputs = text, {}
            elif self._natural_language_handler is not None and text:
                return self._natural_language_skill_id, {"question": text}
            else:
                skill_id, inputs = text, {}
        else:
            raise ValueError("仅支持结构化数据或文本技能调用")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skill_id 必须为非空字符串")
        skill_id = skill_id.strip()
        if skill_id == _NATURAL_LANGUAGE_SKILL:
            raise ValueError("该技能 ID 为内部保留值，请直接提交问题文本")
        if not isinstance(inputs, dict):
            raise ValueError("inputs 必须为 JSON 对象")
        if (self._natural_language_handler is not None
                and skill_id == self._natural_language_skill_id
                and (not isinstance(inputs.get("question"), str)
                     or not inputs["question"].strip())):
            raise ValueError("question 必须为非空字符串")
        return skill_id, inputs

    async def _execute_skill(self, task_id: str, skill_id: str, inputs: dict,
                             command: TaskCommand) -> None:
        try:
            if self._natural_language_handler and skill_id == self._natural_language_skill_id:
                result_data = await self._natural_language_handler(inputs["question"], command)
                skill_name = self._natural_language_skill_name
            else:
                result_data = await self._skill_handlers[skill_id](inputs)
                skill_name = self._skill_names.get(skill_id, skill_id)
            if not isinstance(result_data, dict):
                raise ValueError("技能结果必须为 JSON 对象")
            task = self._tasks[task_id]
            if task.status.state in _TERMINAL_STATES:
                return
            task.products = [Product(id=f"prod-{task_id}", name=skill_name,
                description=f"技能 {skill_id} 的执行结果", dataItems=[
                    StructuredDataItem(data=result_data, metadata={"skill_id": skill_id})])]
            self._update_status(task_id, TaskState.AwaitingCompletion)
        except asyncio.CancelledError:
            if self._tasks[task_id].status.state not in _TERMINAL_STATES:
                self._update_status(task_id, TaskState.Canceled)
        except Exception:
            logger.exception("[%s] AIP 技能失败: skill=%s task=%s",
                             self.agent_name, skill_id, task_id)
            if self._tasks[task_id].status.state not in _TERMINAL_STATES:
                self._update_status(task_id, TaskState.Failed,
                    [TextDataItem(text="技能执行失败，请检查服务日志")])
        finally:
            self._executions.pop(task_id, None)

    async def aclose(self) -> None:
        """Cancel in-flight work when this listener is shutting down."""
        executions = list(self._executions.values())
        for task_id in list(self._executions):
            if self._tasks[task_id].status.state not in _TERMINAL_STATES:
                self._update_status(task_id, TaskState.Canceled)
        for execution in executions:
            execution.cancel()
        if executions:
            await asyncio.gather(*executions, return_exceptions=True)


def register_aip_agent_router(
    app: FastAPI, service: AipAgentService, endpoint: str, *,
    identity_binding_enabled: bool = False,
    command_authorizer: Optional[
        Callable[[Request, TaskCommand], Optional[Awaitable[None]]]
    ] = None,
) -> None:
    """SDK 先校验 TLS 对端与 senderId，再统一授权并执行服务独立 dispatch。"""
    from acps_sdk.aip.aip_rpc_server import handle_rpc_request

    @app.post(endpoint, response_model=RpcResponse, tags=["AIP"])
    async def aip_rpc_endpoint(request: Request):
        async def dispatch(rpc_request: RpcRequest) -> RpcResponse:
            if command_authorizer is not None:
                authorization = command_authorizer(request, rpc_request.params.command)
                if inspect.isawaitable(authorization):
                    await authorization
            return await service.dispatch_rpc(rpc_request)

        return await handle_rpc_request(request, service, local_aic=service.aic,
            identity_binding_enabled=identity_binding_enabled, dispatch_request=dispatch)

    @app.get(endpoint.replace("/rpc", "/health"), tags=["AIP"])
    async def aip_health_check():
        return {"status": "healthy", "agent": service.agent_name,
                "agent_type": service.agent_type, "aic": service.aic,
                "skills": service.list_skills(), "timestamp": _now()}
