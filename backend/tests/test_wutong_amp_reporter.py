"""AMP Kafka 直报（心跳/access）的单元测试：不连接任何真实 Kafka。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.aip.amp_reporter import (
    ACCESS_TOPIC,
    AmpKafkaReporter,
    AmpSettings,
    HEARTBEAT_TOPIC,
    build_access_record,
    build_heartbeat_record,
    extract_task_id,
)
from app.aip.wutong_runtime import load_config


def test_build_heartbeat_record_matches_sdk_wire_format():
    from acps_sdk.amp.models import HeartbeatLogRecord

    record = build_heartbeat_record("1.2.156.3088.1.BUPT.R2QTUC.TEST.TEST.TEST", 12.3456)
    # 线缆格式必须能被 SDK 模型原样读回（camelCase 键、可缺省字段不出现）。
    restored = HeartbeatLogRecord.model_validate(record)
    assert restored.aic == "1.2.156.3088.1.BUPT.R2QTUC.TEST.TEST.TEST"
    assert restored.body is not None and restored.body.uptime_seconds == 12.346
    assert record["log_type"] == "heartbeat"
    assert "integrity" not in record


def test_build_access_record_matches_sdk_wire_format():
    from acps_sdk.amp.models import AccessLogRecord

    record = build_access_record(
        aic="1.2.156.3088.1.BUPT.R2QTUC.TEST.TEST.TEST", role="quotation",
        method="start", path="/aip/quotation/rpc", status_code=200,
        duration_ms=4321.5, caller_aic="1.2.156.3088.1.BUPT.CALLER.A.B.C",
        task_id="task-abc-123",
    )
    restored = AccessLogRecord.model_validate(record)
    assert restored.log_type == "access"
    assert restored.correlation_id == "task-abc-123"
    assert restored.body is not None
    assert restored.body.duration_ms == 4321.5
    assert restored.body.request is not None and restored.body.request.method == "start"
    assert restored.body.response is not None and restored.body.response.status_code == 200
    assert restored.body.caller is not None and restored.body.caller.aic.endswith("C")
    assert restored.body.callee is not None and restored.body.callee.service_name == "quotation"
    # 无调用方/无任务时对应字段整体缺省，而不是空值。
    minimal = build_access_record(
        aic="aic", role="leader", method=None, path="/aip/leader/rpc",
        status_code=500, duration_ms=1.0,
    )
    assert "correlation_id" not in minimal
    assert "caller" not in minimal["body"]


class FakeProducer:
    def __init__(self, *, fail: bool = False):
        self.messages = []
        self.fail = fail
        self.stopped = False

    async def send_and_wait(self, topic, value=None, key=None):
        if self.fail:
            raise RuntimeError("kafka down")
        self.messages.append((topic, json.loads(value.decode("utf-8")), key))

    async def stop(self):
        self.stopped = True


async def _reporter_with(producer: FakeProducer) -> AmpKafkaReporter:
    reporter = AmpKafkaReporter(AmpSettings(kafka_bootstrap="wt.ioa.pub:19092"))
    reporter._get_producer = lambda: _fake_get(producer)  # type: ignore[method-assign]
    return reporter


async def _fake_get(producer: FakeProducer):
    return producer


def test_send_heartbeat_and_access_use_expected_topics():
    async def run():
        producer = FakeProducer()
        reporter = await _reporter_with(producer)
        assert await reporter.send_heartbeat("aic-1") is True
        assert await reporter.report_access(
            aic="aic-1", role="tracking", method="get", path="/aip/tracking/rpc",
            status_code=200, duration_ms=5.0, task_id="t-1",
        ) is True
        return producer, reporter

    producer, reporter = asyncio.run(run())
    assert [m[0] for m in producer.messages] == [HEARTBEAT_TOPIC, ACCESS_TOPIC]
    heartbeat, access = (m[1] for m in producer.messages)
    assert heartbeat["aic"] == "aic-1"
    assert access["correlation_id"] == "t-1"
    assert reporter.sent == 2 and reporter.failed == 0


def test_send_failure_is_tolerated_and_producer_recycled():
    async def run():
        producer = FakeProducer(fail=True)
        reporter = await _reporter_with(producer)
        assert await reporter.send_heartbeat("aic-1") is False
        return producer, reporter

    producer, reporter = asyncio.run(run())
    assert reporter.failed == 1 and reporter.sent == 0
    assert producer.stopped is True  # 连接疑似损坏时关闭待重建


def test_report_access_respects_toggle():
    async def run():
        producer = FakeProducer()
        reporter = AmpKafkaReporter(AmpSettings(
            kafka_bootstrap="wt.ioa.pub:19092", heartbeat_interval_seconds=5, report_access=False,
        ))
        reporter._get_producer = lambda: _fake_get(producer)  # type: ignore[method-assign]
        assert await reporter.report_access(
            aic="a", role="leader", method="start", path="/p", status_code=200, duration_ms=1.0,
        ) is False
        return producer

    assert asyncio.run(run()).messages == []


def test_run_heartbeat_loop_keeps_going_after_failure():
    async def run():
        producer = FakeProducer(fail=True)
        reporter = await _reporter_with(producer)
        task = asyncio.create_task(reporter.run_heartbeat("aic-loop"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return reporter

    reporter = asyncio.run(run())
    assert reporter.failed >= 1  # 循环没有被单次失败打断


def _base_runtime_dict(tmp_path: Path) -> dict:
    import copy

    template = Path(__file__).resolve().parents[2] / "registration/wutong/runtime.example.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data.update(bindHost="127.0.0.1", publicHost="127.0.0.1",
                trustBundle=str(tmp_path / "trust.pem"))
    data["leaderClient"] = {
        "certFile": str(tmp_path / "leader-client.pem"),
        "keyFile": str(tmp_path / "leader-client.key"),
    }
    for role, agent in data["agents"].items():
        agent.update(
            port=9500 + len(role), certFile=str(tmp_path / f"{role}.pem"),
            keyFile=str(tmp_path / f"{role}.key"),
        )
    return copy.deepcopy(data)


def test_load_config_without_amp_section_keeps_default():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "runtime.json"
        path.write_text(json.dumps(_base_runtime_dict(Path(directory))), encoding="utf-8")
        config = load_config(path)
        assert config.amp is None


@pytest.mark.parametrize("amp_section, match", [
    ({"kafkaBootstrap": "wt.ioa.pub:19092", "heartbeatIntervalSeconds": 30, "reportAccess": True}, None),
    ({"kafkaBootstrap": "h1:19092,h2:19093", "heartbeatIntervalSeconds": 15, "reportAccess": False}, None),
    ({"kafkaBootstrap": "wt.ioa.pub 19092", "heartbeatIntervalSeconds": 30, "reportAccess": True}, "kafkaBootstrap"),
    ({"kafkaBootstrap": "wt.ioa.pub:nope", "heartbeatIntervalSeconds": 30, "reportAccess": True}, "kafkaBootstrap"),
    ({"kafkaBootstrap": "wt.ioa.pub:19092", "heartbeatIntervalSeconds": 1, "reportAccess": True}, "heartbeatIntervalSeconds"),
    ({"kafkaBootstrap": "wt.ioa.pub:19092", "heartbeatIntervalSeconds": 30, "reportAccess": "yes"}, "reportAccess"),
])
def test_load_config_amp_section_validation(amp_section, match):
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "runtime.json"
        data = _base_runtime_dict(Path(directory))
        data["amp"] = amp_section
        path.write_text(json.dumps(data), encoding="utf-8")
        if match is None:
            config = load_config(path)
            assert config.amp is not None
            assert config.amp.kafka_bootstrap == amp_section["kafkaBootstrap"]
            assert config.amp.heartbeat_interval_seconds == amp_section["heartbeatIntervalSeconds"]
            assert config.amp.report_access is amp_section["reportAccess"]
        else:
            with pytest.raises(ValueError, match=match):
                load_config(path)


def test_load_config_rejects_unknown_extra_top_level_field():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "runtime.json"
        data = _base_runtime_dict(Path(directory))
        data["unexpected"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="runtime"):
            load_config(path)


def test_runtime_config_replace_still_works_with_amp_default():
    """老测试用 dataclasses.replace 拷贝配置；amp 默认 None 不应破坏该用法。"""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "runtime.json"
        path.write_text(json.dumps(_base_runtime_dict(Path(directory))), encoding="utf-8")
        config = load_config(path)
        cloned = replace(config, bind_host="127.0.0.2")
        assert cloned.bind_host == "127.0.0.2"
        assert cloned.amp is None


class _Command:
    def __init__(self, task_id):
        self.taskId = task_id


class _Params:
    def __init__(self, command):
        self.command = command


class _Request:
    def __init__(self, method, command):
        self.method = method
        self.params = _Params(command)


class _Result:
    def __init__(self, task_id):
        self.task_id = task_id


class _ResultModel:
    """模拟 pydantic 响应结果（model_dump 返回 by_alias 字典）。"""

    def __init__(self, task_id):
        self._task_id = task_id

    def model_dump(self, **_kwargs):
        return {"taskId": self._task_id}


class _Response:
    def __init__(self, result):
        self.result = result
        self.error = None


def test_extract_task_id_prefers_command_then_response():
    assert extract_task_id(_Request("start", _Command(None))) is None
    assert extract_task_id(_Request("start", _Command("t-cmd"))) == "t-cmd"
    assert extract_task_id(
        _Request("get", _Command(None)), _Response(_ResultModel("t-resp")),
    ) == "t-resp"
    assert extract_task_id(_Request("get", _Command(None)), _Response({"taskId": "t-dict"})) == "t-dict"
    assert extract_task_id(_Request("get", _Command(None)), _Response(_Result("ignored"))) is None
