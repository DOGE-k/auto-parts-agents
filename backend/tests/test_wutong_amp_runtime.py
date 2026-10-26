"""Exercise the actual runtime AMP wrapper without Kafka or business credentials."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.aip.amp_reporter import AmpSettings
from app.aip.wutong_runtime import AgentEndpoint, RuntimeConfig, create_app


@pytest.mark.parametrize("amp_mode", ["absent", "disabled", "enabled"])
@pytest.mark.parametrize("business_outcome", ["success", "rpc_error", "exception"])
def test_amp_wrapper_preserves_business_dispatch(monkeypatch, tmp_path, amp_mode, business_outcome):
    reporters = []

    class Reporter:
        def __init__(self, settings):
            self.settings = settings
            self.calls = []
            reporters.append(self)

        async def report_access(self, **record):
            self.calls.append(record)
            return True

    monkeypatch.setattr("app.aip.amp_reporter.AmpKafkaReporter", Reporter)
    endpoint = AgentEndpoint(
        role="quotation", aic="synthetic-test-aic", port=9444,
        cert_file=tmp_path / "unused.pem", key_file=tmp_path / "unused.key",
        allowed_callers=("*",),
    )
    config = RuntimeConfig(
        path=tmp_path / "unused.json", bind_host="127.0.0.1", public_host="127.0.0.1",
        trust_bundle=tmp_path / "unused-ca.pem", client_cert=tmp_path / "unused-client.pem",
        client_key=tmp_path / "unused-client.key", agents={"quotation": endpoint},
        amp=None if amp_mode == "absent" else AmpSettings(
            kafka_bootstrap="unused.invalid:19092", report_access=amp_mode == "enabled",
        ),
    )
    request = SimpleNamespace(
        method="start", params=SimpleNamespace(command=SimpleNamespace(taskId="fixture-task")),
    )
    response = SimpleNamespace(
        error={"code": -32000} if business_outcome == "rpc_error" else None,
        result={"taskId": "fixture-task"},
    )
    business_error = RuntimeError("synthetic business failure")

    class Service:
        aic = endpoint.aic
        calls = []

        async def dispatch_rpc(self, incoming):
            self.calls.append(incoming)
            if business_outcome == "exception":
                raise business_error
            return response

    service = Service()
    app = create_app(config, "quotation", service=service)
    # Do not enter lifespan: no certificate access or producer/network startup.
    if business_outcome == "exception":
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(service.dispatch_rpc(request))
        assert caught.value is business_error
    else:
        assert asyncio.run(service.dispatch_rpc(request)) is response
    assert service.calls == [request]

    if amp_mode == "absent":
        assert app.state.amp_reporter is None
        assert reporters == []
    else:
        assert app.state.amp_reporter is reporters[0]
        records = reporters[0].calls
        if amp_mode == "disabled":
            assert records == []
        else:
            assert len(records) == 1
            record = records[0]
            assert record["status_code"] == (200 if business_outcome == "success" else 500)
            assert record["route"] == endpoint.rpc_path
            assert record["task_id"] == "fixture-task"
            assert record["request_bytes"] > 0
            if business_outcome != "exception":
                assert record["response_bytes"] > 0
            else:
                assert "response_bytes" not in record
