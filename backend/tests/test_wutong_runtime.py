"""Actual local mTLS tests; generated fixtures are unrelated to platform keys."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn

from app.aip.aip_agent_client import AipAgentClient
from app.aip.aip_agent_service import AipAgentService
from app.aip.leader_service import create_leader_aip_service, PARTNER_ROLES
from app.aip.wutong_runtime import (
    ROLES, check_configuration, client_ssl_context, create_app, load_config,
    make_server_config, probe_role, LocalPeerTransport, deployment_acs, export_acs,
    deepseek_key_status,
)


@pytest.fixture(scope="module")
def tls_config(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.fail("OpenSSL is required to run the Wutong TLS acceptance tests")
    directory = tmp_path_factory.mktemp("synthetic-wutong-tls")

    def openssl(*args):
        result = subprocess.run(["openssl", *map(str, args)], capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr.decode(errors="replace")

    ca, ca_key = directory / "ca.pem", directory / "ca.key"
    openssl("req", "-x509", "-newkey", "ed25519", "-nodes", "-keyout", ca_key,
            "-out", ca, "-days", "2", "-subj", "/CN=Local test CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    template = Path(__file__).resolve().parents[2] / "registration/wutong/runtime.example.json"
    data = json.loads(template.read_text(encoding="utf-8"))
    data.update(bindHost="127.0.0.1", publicHost="127.0.0.1", trustBundle=str(ca))

    def certificate(name, aic, purpose):
        key, csr, cert = (directory / (name + ext) for ext in (".key", ".csr", ".pem"))
        extfile = directory / (name + ".ext")
        extfile.write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
            f"extendedKeyUsage=critical,{purpose}\n"
            f"subjectAltName=URI:acps://{aic},IP:127.0.0.1,IP:129.204.33.138\n", encoding="ascii")
        openssl("req", "-new", "-newkey", "ed25519", "-nodes", "-keyout", key,
                "-out", csr, "-subj", f"/CN={aic}")
        openssl("x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key,
                "-CAcreateserial", "-out", cert, "-days", "2", "-extfile", extfile)
        return str(cert), str(key)

    sockets = []
    try:
        for role in ROLES:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
            agent = data["agents"][role]
            agent["port"] = sock.getsockname()[1]
            agent["certFile"], agent["keyFile"] = certificate(role, agent["aic"], "serverAuth")
    finally:
        for sock in sockets:
            sock.close()
    # Exercise a restricted Partner as well as the public authenticated template.
    data["agents"]["quotation"]["allowedCallerAics"] = [data["agents"]["leader"]["aic"]]
    data["leaderClient"]["certFile"], data["leaderClient"]["keyFile"] = certificate(
        "leader-client", data["agents"]["leader"]["aic"], "clientAuth")
    outsider = certificate("other-client", data["agents"]["procurement"]["aic"], "clientAuth")
    path = directory / "runtime.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_config(path), outsider


def fixture_service(config, role="quotation"):
    service = AipAgentService("Synthetic local fixture", role, config.agents[role].aic)

    async def echo(inputs):
        return {"fixture": True, "inputs": inputs}

    service.register_skill("quotation.get_real", "Test only", echo)
    return service


@contextmanager
def listener(config, role, service):
    app = create_app(config, role, service=service)
    server = uvicorn.Server(make_server_config(config, role, app))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "TLS listener failed to start"
        yield app
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        assert not thread.is_alive(), "TLS listener did not stop"


def payload(sender, *, task_id=None, command="start", session="fixture-session", text="quotation.get_real|{}"):
    return {"id": str(uuid4()), "params": {"command": {
        "id": str(uuid4()), "sentAt": "2026-10-16T00:00:00Z", "senderRole": "leader",
        "senderId": sender, "command": command, "sessionId": session,
        "taskId": task_id, "dataItems": [{"type": "text", "text": text}],
    }}}


def test_configuration_checks_all_chains_purposes_identity_and_key_pairs(tls_config):
    config, _ = tls_config
    check_configuration(config)
    wrong_key = replace(config.agents["leader"], key_file=config.client_key)
    with pytest.raises(ssl.SSLError):
        check_configuration(replace(config, agents={**config.agents, "leader": wrong_key}))
    wrong_purpose = replace(config.agents["leader"], cert_file=config.client_cert, key_file=config.client_key)
    with pytest.raises(ValueError, match="purpose check failed"):
        check_configuration(replace(config, agents={**config.agents, "leader": wrong_purpose}))
    wrong_aic = replace(config.agents["quotation"], aic=config.agents["procurement"].aic)
    with pytest.raises(ValueError, match="AIC mismatch"):
        check_configuration(replace(config, agents={**config.agents, "quotation": wrong_aic}))


@pytest.mark.parametrize("mutation", ["same-port", "local-aic", "mixed-wildcard", "unknown-field", "url-host", "same-key"])
def test_bad_runtime_config_fails_before_start(tls_config, tmp_path, mutation):
    config, _ = tls_config
    data = json.loads(config.path.read_text())
    if mutation == "same-port":
        data["agents"]["quotation"]["port"] = data["agents"]["leader"]["port"]
    elif mutation == "local-aic":
        data["agents"]["leader"]["aic"] = "local-dev"
    elif mutation == "mixed-wildcard":
        data["agents"]["leader"]["allowedCallerAics"].append(data["agents"]["quotation"]["aic"])
    elif mutation == "unknown-field":
        data["password"] = "not-a-real-credential"
    elif mutation == "url-host":
        data["publicHost"] = "http://localhost:9000/path"
    else:
        data["leaderClient"]["keyFile"] = data["agents"]["leader"]["keyFile"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_config(path)


def test_mtls_probe_checks_public_ip_locally_and_only_mounts_one_role(tls_config):
    config, _ = tls_config
    service = fixture_service(config)
    with listener(config, "quotation", service):
        # Connect to loopback while verifying the actual public-IP SAN.
        public = replace(config, public_host="129.204.33.138")
        assert probe_role(public, "quotation")["aic"] == service.aic
        with httpx.Client(verify=client_ssl_context(config), trust_env=False) as client:
            root = config.url("quotation").split("/aip/")[0]
            for route in ("/api/health", "/api/real-orders/assistant/ask", "/aip/procurement/rpc", "/docs"):
                assert client.get(root + route).status_code == 404


def test_tls_rejects_no_certificate_and_rpc_rejects_forged_or_disallowed_aic(tls_config):
    config, outsider_files = tls_config
    service = fixture_service(config)
    leader = config.agents["leader"].aic
    outsider = config.agents["procurement"].aic
    with listener(config, "quotation", service):
        no_certificate = ssl.create_default_context(cafile=str(config.trust_bundle))
        with httpx.Client(verify=no_certificate, trust_env=False) as client:
            with pytest.raises(httpx.HTTPError):
                client.post(config.url("quotation"), json=payload(leader))
        with httpx.Client(verify=client_ssl_context(config), trust_env=False) as client:
            response = client.post(config.url("quotation"), json=payload(outsider)).json()
            assert response["error"]["code"] == -32009
        other_context = ssl.create_default_context(cafile=str(config.trust_bundle))
        other_context.load_cert_chain(*outsider_files)
        with httpx.Client(verify=other_context, trust_env=False) as client:
            response = client.post(config.url("quotation"), json=payload(outsider))
            assert response.status_code == 403
        assert not service._tasks


def test_real_sdk_client_task_cycle_and_unexpected_server_identity(tls_config):
    config, _ = tls_config
    service = fixture_service(config)

    async def exchange():
        client = AipAgentClient(config.url("quotation"), config.agents["leader"].aic,
            client_ssl_context(config), True, service.aic)
        try:
            assert await client.invoke_skill("quotation.get_real", {"quotation_id": "test-fixture"}) == {
                "fixture": True, "inputs": {"quotation_id": "test-fixture"}}
        finally:
            await client.close()
        wrong = AipAgentClient(config.url("quotation"), config.agents["leader"].aic,
            client_ssl_context(config), True, config.agents["procurement"].aic)
        try:
            from acps_sdk.aip.aip_identity import SenderIdentityMismatchError
            with pytest.raises(SenderIdentityMismatchError):
                await wrong.invoke_skill("quotation.get_real", {})
        finally:
            await wrong.close()

    with listener(config, "quotation", service):
        asyncio.run(exchange())


def test_public_policy_still_requires_valid_tls_but_accepts_other_authenticated_agents(tls_config):
    config, outsider_files = tls_config
    public = replace(config, agents={**config.agents,
        "quotation": replace(config.agents["quotation"], allowed_callers=("*",))})
    service = fixture_service(config)
    context = ssl.create_default_context(cafile=str(config.trust_bundle))
    context.load_cert_chain(*outsider_files)
    with listener(public, "quotation", service):
        with httpx.Client(verify=context, trust_env=False) as client:
            response = client.post(public.url("quotation"), json=payload(config.agents["procurement"].aic))
            assert response.status_code == 200
            assert response.json()["result"]["senderId"] == service.aic


def test_leader_question_really_calls_partner_over_mtls(tls_config):
    config, _ = tls_config
    quotation = fixture_service(config)
    public = replace(config, public_host="129.204.33.138")
    context = client_ssl_context(config)
    clients = {role: AipAgentClient(public.url(role), config.agents["leader"].aic,
        context, True, config.agents[role].aic,
        transport=LocalPeerTransport(public.public_host, "127.0.0.1", context)) for role in PARTNER_ROLES}

    class CoordinatorFixture:
        def __init__(self, skill_invoker, allowed_skill_ids):
            self.invoke = skill_invoker
            assert "quotation.get_real" in allowed_skill_ids

        async def ask(self, text, run_meta):
            result = await self.invoke("quotation", "quotation.get_real", {"quotation_id": text})
            return {"answer": "synthetic TLS fixture", "result": result,
                    "namespace": run_meta["session_id"]}

        async def aclose(self):
            pass

    leader = create_leader_aip_service(config.agents["leader"].aic,
        outbound_clients=clients, coordinator_factory=CoordinatorFixture)

    async def exchange():
        caller = AipAgentClient(config.url("leader"), config.agents["leader"].aic,
            client_ssl_context(config), True, leader.aic)
        try:
            answer = await caller.invoke_skill("leader.ask", {"question": "fixture-query"}, timeout=6)
            assert answer["result"] == {"fixture": True, "inputs": {"quotation_id": "fixture-query"}}
            assert answer["namespace"].startswith("aip-")
            assert len(quotation._tasks) == 1
        finally:
            await caller.close()
            await asyncio.gather(*(client.close() for client in clients.values()))

    with listener(config, "quotation", quotation), listener(config, "leader", leader):
        asyncio.run(exchange())


def test_export_preserves_registry_identity_provider_and_certificate(tls_config):
    config, _ = tls_config
    receipts = Path(__file__).resolve().parents[2] / "registration/wutong/approved"
    for role in ROLES:
        original = json.loads((receipts / (role + ".acs.json")).read_text(encoding="utf-8"))
        exported = deployment_acs(config, role, original)
        for field in ("aic", "active", "provider", "certificate", "version", "name", "webAppUrl"):
            assert exported[field] == original[field]
        assert original["endPoints"] == []
        assert exported["endPoints"][0]["url"] == config.url(role)
        assert exported["securitySchemes"]["mtls"]["type"] == "mutualTLS"
        assert exported["skills"][0]["id"] == ("leader.ask" if role == "leader" else original["skills"][0]["id"])
    wrong = json.loads((receipts / "leader.acs.json").read_text(encoding="utf-8"))
    wrong["aic"] = config.agents["quotation"].aic
    with pytest.raises(ValueError, match="does not match"):
        deployment_acs(config, "leader", wrong)


def test_export_does_not_generate_publishable_files_when_listeners_are_offline(tls_config):
    config, _ = tls_config
    with pytest.raises(OSError):
        export_acs(config)
    assert not (config.path.parent / "acs").exists()


def test_configuration_entry_import_does_not_load_business_database_or_model_settings():
    child = subprocess.run([sys.executable, "-c",
        "import sys; from app.aip import wutong_runtime; "
        "assert 'app.persistence.database' not in sys.modules; "
        "assert 'app.services.coordinator' not in sys.modules; "
        "assert 'app.main' not in sys.modules"], capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr.decode(errors="replace")

def test_deepseek_key_status_reports_encoding_without_exposing_value(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc123")
    clean = deepseek_key_status(from_shell=True)
    assert clean == {"configured": True, "length": 9, "ascii": True,
                     "firstNonAsciiPosition": None, "source": "shellEnvironment"}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-密钥")
    dirty = deepseek_key_status(from_shell=False)
    assert dirty["ascii"] is False
    assert dirty["firstNonAsciiPosition"] == 3
    assert dirty["source"] == "dotenv"
    # 返回值里绝不能出现密钥内容
    assert "密钥" not in json.dumps(dirty, ensure_ascii=False)

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    missing = deepseek_key_status(from_shell=False)
    assert missing["configured"] is False and missing["length"] == 0
