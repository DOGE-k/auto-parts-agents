"""AMP 日志直报（Kafka PLAINTEXT）。

按梧桐平台 AMP 规范，把心跳与 access 日志直接投递到平台 Kafka（端口 19092，
PLAINTEXT），不依赖部署侧 Fluent Bit。心跳驱动 Discovery 的 alive 视图；
access 的 correlationId 填写 taskId，供平台按任务对统计调用量。

上报是尽力而为：aiokafka 缺失、Kafka 不可达或发送失败都只记 WARNING 并继续，
绝不影响 AIP RPC 主流程。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any

from acps_sdk.amp.models import (
    AccessBody,
    AccessLogRecord,
    AccessParticipant,
    AccessRequest,
    AccessResponse,
    HeartbeatBody,
    HeartbeatLogRecord,
)

logger = logging.getLogger(__name__)

HEARTBEAT_TOPIC = "amp.heartbeat"
ACCESS_TOPIC = "amp.access"


@dataclass(frozen=True)
class AmpSettings:
    """runtime.json 的 amp 段。"""

    kafka_bootstrap: str
    heartbeat_interval_seconds: int = 30
    report_access: bool = True


def build_heartbeat_record(aic: str, uptime_seconds: float, *, service_name: str | None = None) -> dict:
    """构造一条与 SDK HeartbeatLogRecord 线缆格式一致的心跳记录。

    resource.service.name 与平台部署形态对齐（Discover alive 视图与统计归因使用）。
    """
    record = HeartbeatLogRecord(
        log_id=HeartbeatLogRecord.new_log_id(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        aic=aic,
        body=HeartbeatBody(uptime_seconds=round(uptime_seconds, 3)),
    )
    dumped = record.model_dump(mode="json", by_alias=True, exclude_none=True)
    dumped["timestamp"] = dumped["timestamp"].replace("+00:00", "Z")
    if service_name:
        dumped["resource"] = {"service.name": service_name}
    return dumped


def build_access_record(
    *,
    aic: str,
    role: str,
    method: str | None,
    path: str,
    status_code: int,
    duration_ms: float,
    caller_aic: str | None = None,
    task_id: str | None = None,
    route: str | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    callee_aic: str | None = None,
    callee_service_name: str | None = None,
) -> dict:
    """构造一条与 SDK AccessLogRecord 线缆格式一致的访问记录。

    correlation_id=taskId 满足平台「按 taskId 双方匹配作为一次调用/被调」的统计口径；
    route/bodySizeBytes/resource 等字段与平台部署形态对齐，避免统计侧解析丢弃。
    入站（callee 侧）记录的被调方默认是本身份；出站（caller 侧）记录用
    callee_aic/callee_service_name 指向对端 Partner，双方记录才能按 taskId 配对。
    """
    body = AccessBody(
        duration_ms=round(duration_ms, 3),
        request=AccessRequest(
            method=method, url=path, route=route or path, body_size_bytes=request_bytes,
        ),
        response=AccessResponse(status_code=status_code, body_size_bytes=response_bytes),
        caller=AccessParticipant(aic=caller_aic, service_name=caller_aic) if caller_aic else None,
        callee=AccessParticipant(aic=callee_aic or aic, service_name=callee_service_name or role),
    )
    record = AccessLogRecord(
        log_id=AccessLogRecord.new_log_id(),
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        aic=aic,
        body=body,
        correlation_id=task_id,
    )
    return record.model_dump(mode="json", by_alias=True, exclude_none=True)


class AmpKafkaReporter:
    """进程内共享的 AMP Kafka 上报器（一个角色进程一个实例）。

    producer 懒加载；任何失败降级为本地 WARNING，不抛出。
    """

    def __init__(self, settings: AmpSettings) -> None:
        self._settings = settings
        self._producer: Any = None
        self._lock = asyncio.Lock()
        self._disabled = False
        self._start_monotonic = time.monotonic()
        self.sent = 0
        self.failed = 0

    @property
    def settings(self) -> AmpSettings:
        return self._settings

    async def _get_producer(self):
        if self._disabled:
            return None
        if self._producer is not None:
            return self._producer
        async with self._lock:
            if self._producer is not None or self._disabled:
                return self._producer
            try:
                from aiokafka import AIOKafkaProducer
            except ImportError:
                logger.warning("[AMP] aiokafka 未安装，心跳与 access 上报停用")
                self._disabled = True
                return None
            producer = AIOKafkaProducer(
                bootstrap_servers=self._settings.kafka_bootstrap,
                client_id="auto-parts-amp",
            )
            try:
                await producer.start()
            except Exception:
                logger.warning(
                    "[AMP] Kafka 连接失败：%s（本轮停用，稍后重试）",
                    self._settings.kafka_bootstrap, exc_info=True,
                )
                with suppress(Exception):
                    await producer.stop()
                return None
            self._producer = producer
            logger.info("[AMP] Kafka 已连接：%s", self._settings.kafka_bootstrap)
            return producer

    async def send(self, topic: str, record: dict) -> bool:
        producer = await self._get_producer()
        if producer is None:
            self.failed += 1
            return False
        try:
            key = (record.get("aic") or "").encode("utf-8") or None
            await producer.send_and_wait(
                topic, value=json.dumps(record, ensure_ascii=False).encode("utf-8"), key=key,
            )
            self.sent += 1
            return True
        except Exception:
            self.failed += 1
            logger.warning("[AMP] 上报失败 topic=%s", topic, exc_info=True)
            with suppress(Exception):
                await producer.stop()
            self._producer = None
            return False

    async def send_heartbeat(self, aic: str, *, service_name: str | None = None) -> bool:
        uptime = time.monotonic() - self._start_monotonic
        return await self.send(
            HEARTBEAT_TOPIC, build_heartbeat_record(aic, uptime, service_name=service_name),
        )

    async def report_access(self, **kwargs: Any) -> bool:
        if not self._settings.report_access:
            return False
        return await self.send(ACCESS_TOPIC, build_access_record(**kwargs))

    async def run_heartbeat(self, aic: str, *, service_name: str | None = None) -> None:
        """周期发心跳直到被 cancel；单次失败不中断循环。"""
        interval = self._settings.heartbeat_interval_seconds
        while True:
            await self.send_heartbeat(aic, service_name=service_name)
            await asyncio.sleep(interval)

    async def aclose(self) -> None:
        if self._producer is not None:
            with suppress(Exception):
                await self._producer.stop()
            self._producer = None


def extract_task_id(rpc_request: Any, rpc_response: Any = None) -> str | None:
    """尽量从请求 command 或响应结果里取 taskId，用于 correlationId。"""
    command = getattr(getattr(rpc_request, "params", None), "command", None)
    task_id = getattr(command, "taskId", None)
    if isinstance(task_id, str) and task_id:
        return task_id
    result = getattr(rpc_response, "result", None)
    dumped = None
    if hasattr(result, "model_dump"):
        with suppress(Exception):
            dumped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(result, dict):
        dumped = result
    if isinstance(dumped, dict):
        value = dumped.get("taskId")
        if isinstance(value, str) and value:
            return value
    return None
