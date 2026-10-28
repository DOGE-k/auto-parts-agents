"""AMP Kafka 直报（心跳/access）的单元测试：不连接任何真实 Kafka。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from acps_sdk.aip import TaskState
from acps_sdk.aip.aip_base_model import StructuredDataItem

from app.aip.aip_agent_client import AipAgentClient, OutboundAccessInfo
from app.aip.amp_reporter import (
    ACCESS_TOPIC,
    AmpKafkaReporter,
    AmpSettings,
    HEARTBEAT_TOPIC,
    build_access_record,
    build_heartbeat_record,
    extract_task_id,
)
from app.aip.wutong_runtime import AgentEndpoint, _outbound_access_reporter, load_config


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


LEADER_AIC = "1.2.156.3088.1.BUPT.R2QTUC.LEAD.A.B.C"
PARTNER_AIC = "1.2.156.3088.1.BUPT.R2QTUC.QUOT.A.B.C"


def _partner_endpoint(tmp_path: Path | None = None) -> AgentEndpoint:
    base = tmp_path or Path(".")
    return AgentEndpoint(
        role="quotation", aic=PARTNER_AIC, port=9444,
        cert_file=base / "partner.pem", key_file=base / "partner.key",
        allowed_callers=(LEADER_AIC,),
    )


def test_build_access_record_caller_side_swaps_callee_participant():
    from acps_sdk.amp.models import AccessLogRecord

    record = build_access_record(
        aic=LEADER_AIC, role="leader",
        method="POST", path="/aip/quotation/rpc", status_code=200,
        duration_ms=12.0, caller_aic=LEADER_AIC,
        callee_aic=PARTNER_AIC, callee_service_name="quotation",
        task_id="task-pair-1", request_bytes=64, response_bytes=256,
    )
    restored = AccessLogRecord.model_validate(record)
    assert restored.aic == LEADER_AIC
    assert restored.correlation_id == "task-pair-1"
    assert restored.body.caller.aic == LEADER_AIC
    assert restored.body.callee.aic == PARTNER_AIC
    assert restored.body.callee.service_name == "quotation"
    assert restored.body.request.body_size_bytes == 64
    assert restored.body.response.body_size_bytes == 256


def test_outbound_access_reporter_builds_paired_caller_side_record(tmp_path):
    async def run():
        producer = FakeProducer()
        reporter = await _reporter_with(producer)
        hook = _outbound_access_reporter(reporter, LEADER_AIC, "leader", _partner_endpoint(tmp_path))
        assert hook is not None
        await hook(OutboundAccessInfo(
            task_id="task-pair-2", skill_id="quotation.calculate_cost", session_id="ns",
            status_code=200, duration_ms=3.5, request_bytes=64, response_bytes=256,
        ))
        return producer

    producer = asyncio.run(run())
    assert [m[0] for m in producer.messages] == [ACCESS_TOPIC]
    record = producer.messages[0][1]
    assert record["aic"] == LEADER_AIC
    assert record["correlation_id"] == "task-pair-2"
    body = record["body"]
    assert body["caller"] == {"aic": LEADER_AIC, "serviceName": LEADER_AIC}
    assert body["callee"] == {"aic": PARTNER_AIC, "serviceName": "quotation"}
    assert body["request"]["route"] == "/aip/quotation/rpc"
    assert body["request"]["bodySizeBytes"] == 64
    assert body["response"]["bodySizeBytes"] == 256


def test_outbound_access_reporter_without_amp_is_none():
    assert _outbound_access_reporter(None, LEADER_AIC, "leader", _partner_endpoint()) is None


def _completed_result(task_id):
    return SimpleNamespace(
        taskId=task_id,
        status=SimpleNamespace(state=TaskState.Completed, dataItems=[]),
        products=[SimpleNamespace(dataItems=[StructuredDataItem(data={"ok": 1})])],
    )


class _FakeRpc:
    def __init__(self, *, error=None, hang=False):
        self.error = error
        self.hang = hang
        self.starts = []
        self.cancels = []

    async def start_task(self, session_id, user_input, task_id):
        self.starts.append((session_id, user_input, task_id))
        if self.hang:
            await asyncio.sleep(5)
        if self.error is not None:
            raise self.error
        return _completed_result(task_id)

    async def cancel_task(self, task_id, session_id):
        self.cancels.append(task_id)


def _client_with(fake_rpc, reporter):
    client = AipAgentClient(
        "https://peer.example:9444/aip/quotation/rpc", leader_id=LEADER_AIC,
        access_reporter=reporter,
    )
    client._client = fake_rpc
    return client


def test_invoke_skill_reports_caller_side_access_on_success():
    infos = []

    async def reporter(info):
        infos.append(info)

    async def run():
        fake = _FakeRpc()
        client = _client_with(fake, reporter)
        data = await client.invoke_skill("quotation.calculate_cost", {"order": "SAL-1"}, timeout=5)
        return data, fake

    data, fake = asyncio.run(run())
    assert data == {"ok": 1}
    assert len(infos) == 1
    info = infos[0]
    assert info.task_id == fake.starts[0][2]
    assert info.skill_id == "quotation.calculate_cost"
    assert info.status_code == 200
    assert info.request_bytes == len(fake.starts[0][1].encode("utf-8"))
    assert info.response_bytes and info.response_bytes > 0


def test_invoke_skill_reports_failed_status():
    infos = []

    async def reporter(info):
        infos.append(info)

    async def run():
        client = _client_with(_FakeRpc(error=RuntimeError("技能未成功")), reporter)
        with pytest.raises(RuntimeError):
            await client.invoke_skill("quotation.calculate_cost", {}, timeout=5)

    asyncio.run(run())
    assert infos[0].status_code == 500


def test_invoke_skill_reports_timeout_and_cancels_remote():
    infos = []

    async def reporter(info):
        infos.append(info)

    async def run():
        fake = _FakeRpc(hang=True)
        client = _client_with(fake, reporter)
        with pytest.raises(asyncio.TimeoutError):
            await client.invoke_skill("quotation.calculate_cost", {}, timeout=0.05, poll_interval=0.01)
        return fake

    fake = asyncio.run(run())
    assert infos[0].status_code == 504
    assert fake.cancels


def test_invoke_skill_without_reporter_still_works():
    async def run():
        client = _client_with(_FakeRpc(), None)
        return await client.invoke_skill("quotation.calculate_cost", {"a": 1}, timeout=5)

    assert asyncio.run(run()) == {"ok": 1}
