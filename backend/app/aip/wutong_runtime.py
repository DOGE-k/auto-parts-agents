"""One authenticated AIP listener per approved Wutong identity.

This entry point never mounts the business web application's write APIs.  The
configuration contains public identities and file paths, not credential values.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

ROLES = ("leader", "quotation", "procurement", "tracking", "quality-document")


class LocalPeerTransport(httpx.AsyncBaseTransport):
    """Reach a co-located Partner locally; still verify its public TLS name.

    httpcore's SNI extension changes only the TLS server name.  The URL and
    HTTP Host still describe the advertised peer; proxy environment variables
    cannot redirect these local connections.
    """
    def __init__(self, public_host: str, connect_host: str, context: ssl.SSLContext):
        self.public_host = public_host
        self.connect_host = connect_host
        self.inner = httpx.AsyncHTTPTransport(verify=context, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != self.public_host:
            raise ValueError("Local transport only accepts its configured HTTPS peer")
        local_request = httpx.Request(
            request.method, request.url.copy_with(host=self.connect_host),
            headers=request.headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": self.public_host},
        )
        return await self.inner.handle_async_request(local_request)

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass(frozen=True)
class AgentEndpoint:
    role: str
    aic: str
    port: int
    cert_file: Path
    key_file: Path
    allowed_callers: tuple[str, ...]

    @property
    def rpc_path(self) -> str:
        return f"/aip/{self.role}/rpc"


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    bind_host: str
    public_host: str
    trust_bundle: Path
    client_cert: Path
    client_key: Path
    agents: dict[str, AgentEndpoint]
    amp: Any = None

    def url(self, role: str) -> str:
        host = f"[{self.public_host}]" if ":" in self.public_host else self.public_host
        endpoint = self.agents[role]
        return f"https://{host}:{endpoint.port}{endpoint.rpc_path}"


def _fields(data: Any, required: set[str], label: str) -> dict:
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError(f"{label}: configuration fields do not match the runtime template")
    return data


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label}: a non-empty trimmed string is required")
    return value


def _aic(value: Any) -> str:
    from acps_sdk.aip.aip_identity import normalize_and_validate_expected_aic

    value = _string(value, "AIC")
    if normalize_and_validate_expected_aic(value) != value:
        raise ValueError("AIC must use its canonical spelling")
    return value


def _absolute_path(value: Any, label: str) -> Path:
    path = Path(_string(value, label))
    if not path.is_absolute():
        raise ValueError(f"{label}: use an absolute server path")
    return path


def _amp_settings(value: Any) -> Any:
    """解析可选的 amp 段；字段集固定，地址只允许 host:port 列表。"""
    from app.aip.amp_reporter import AmpSettings

    raw = _fields(value, {"kafkaBootstrap", "heartbeatIntervalSeconds", "reportAccess"}, "amp")
    bootstrap = _string(raw["kafkaBootstrap"], "amp.kafkaBootstrap")
    if any(c.isspace() for c in bootstrap) or not bootstrap.isascii():
        raise ValueError("amp.kafkaBootstrap: ASCII host:port list without whitespace required")
    endpoints = []
    for part in bootstrap.split(","):
        host, _, port_text = part.rpartition(":")
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError(f"amp.kafkaBootstrap: invalid endpoint '{part}'") from None
        if not host or not 1 <= port <= 65535:
            raise ValueError(f"amp.kafkaBootstrap: invalid endpoint '{part}'")
        endpoints.append(f"{host}:{port}")
    interval = raw["heartbeatIntervalSeconds"]
    if type(interval) is not int or not 5 <= interval <= 600:
        raise ValueError("amp.heartbeatIntervalSeconds: integer from 5 to 600 required")
    if type(raw["reportAccess"]) is not bool:
        raise ValueError("amp.reportAccess: boolean required")
    return AmpSettings(
        kafka_bootstrap=",".join(endpoints),
        heartbeat_interval_seconds=interval,
        report_access=raw["reportAccess"],
    )


def load_config(path: str | Path) -> RuntimeConfig:
    path = Path(path).resolve()
    required = {"version", "bindHost", "publicHost", "trustBundle", "leaderClient", "agents"}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not required.issubset(set(data)) or not set(data) <= required | {"amp"}:
        raise ValueError("runtime: configuration fields do not match the runtime template")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported runtime configuration version")
    bind_host = _string(data["bindHost"], "bindHost")
    ipaddress.ip_address(bind_host)
    public_host = _string(data["publicHost"], "publicHost")
    try:
        ipaddress.ip_address(public_host)
    except ValueError:
        parsed = urlsplit(f"https://{public_host}")
        # Only an IP or host name, never a URL, port, credentials or path.
        if parsed.hostname != public_host or parsed.port or parsed.path or parsed.username:
            raise ValueError("publicHost must contain only a host name or IP address")
    if any(c.isspace() for c in public_host) or not public_host.isascii():
        raise ValueError("publicHost must be an ASCII host name or IP address")
    client = _fields(data["leaderClient"], {"certFile", "keyFile"}, "leaderClient")
    raw_agents = _fields(data["agents"], set(ROLES), "agents")
    agents: dict[str, AgentEndpoint] = {}
    for role in ROLES:
        raw = _fields(raw_agents[role], {
            "aic", "port", "certFile", "keyFile", "allowedCallerAics",
        }, role)
        port = raw["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError(f"{role}: port must be an integer from 1024 to 65535")
        callers = raw["allowedCallerAics"]
        if not isinstance(callers, list) or any(not isinstance(c, str) for c in callers):
            raise ValueError(f"{role}: allowedCallerAics must be an array of AIC strings")
        if "*" in callers and callers != ["*"]:
            raise ValueError(f"{role}: wildcard must appear alone")
        if len(callers) != len(set(callers)):
            raise ValueError(f"{role}: duplicate allowed caller")
        for caller in callers:
            if caller != "*":
                _aic(caller)
        agents[role] = AgentEndpoint(
            role, _aic(raw["aic"]), port,
            _absolute_path(raw["certFile"], f"{role}.certFile"),
            _absolute_path(raw["keyFile"], f"{role}.keyFile"), tuple(callers),
        )
    if len({e.port for e in agents.values()}) != len(ROLES):
        raise ValueError("Each role needs its own TLS listening port")
    if len({e.aic for e in agents.values()}) != len(ROLES):
        raise ValueError("Each role needs its own approved AIC")
    private_keys = [e.key_file.resolve() for e in agents.values()]
    private_keys.append(_absolute_path(client["keyFile"], "leaderClient.keyFile").resolve())
    if len(set(private_keys)) != len(private_keys):
        raise ValueError("Server and client identities must use separate private key files")
    return RuntimeConfig(
        path, bind_host, public_host,
        _absolute_path(data["trustBundle"], "trustBundle"),
        _absolute_path(client["certFile"], "leaderClient.certFile"),
        _absolute_path(client["keyFile"], "leaderClient.keyFile"), agents,
        _amp_settings(data["amp"]) if "amp" in data else None,
    )


def client_ssl_context(config: RuntimeConfig) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(config.trust_bundle))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(config.client_cert), str(config.client_key))
    return context


def server_ssl_context(config: RuntimeConfig, role: str) -> ssl.SSLContext:
    endpoint = config.agents[role]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(endpoint.cert_file), str(endpoint.key_file))
    context.load_verify_locations(cafile=str(config.trust_bundle))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _validate_certificate(config: RuntimeConfig, cert_file: Path, aic: str, purpose: str) -> None:
    from acps_sdk.aip.aip_identity import AipIdentityError, extract_peer_identity

    decoded = ssl._ssl._test_decode_cert(str(cert_file))
    try:
        identity = extract_peer_identity(decoded)
    except AipIdentityError as exc:
        raise ValueError(f"Invalid certificate AIC in {cert_file.name}") from exc
    if identity.aic != aic:
        raise ValueError(f"Certificate AIC mismatch for {cert_file.name}")
    command = ["openssl", "verify", "-CAfile", str(config.trust_bundle),
               "-untrusted", str(cert_file), "-purpose", purpose]
    if purpose == "sslserver":
        try:
            ipaddress.ip_address(config.public_host)
        except ValueError:
            command.extend(["-verify_hostname", config.public_host])
        else:
            command.extend(["-verify_ip", config.public_host])
    command.append(str(cert_file))
    verified = subprocess.run(command, capture_output=True, timeout=15, check=False)
    if verified.returncode:
        # Do not dump subprocess output or credentials/configuration on failure.
        raise ValueError(f"Certificate chain, validity, address or purpose check failed: {cert_file.name}")


def check_configuration(config: RuntimeConfig) -> None:
    if shutil.which("openssl") is None:
        raise ValueError("OpenSSL is required for offline certificate verification")
    for endpoint in config.agents.values():
        _validate_certificate(config, endpoint.cert_file, endpoint.aic, "sslserver")
        server_ssl_context(config, endpoint.role)  # also validates key/certificate pairing
    _validate_certificate(config, config.client_cert, config.agents["leader"].aic, "sslclient")
    client_ssl_context(config)


def probe_role(config: RuntimeConfig, role: str, *, timeout: float = 4.0) -> dict[str, str]:
    """Verify local listener over mTLS without requiring public-IP hairpin NAT."""
    import http.client
    from acps_sdk.aip.aip_identity import extract_peer_identity

    endpoint = config.agents[role]
    context = client_ssl_context(config)
    host = "::1" if ":" in config.bind_host else "127.0.0.1"
    if config.bind_host not in {"0.0.0.0", "::"}:
        host = config.bind_host
    with socket.create_connection((host, endpoint.port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=config.public_host) as secured:
            if extract_peer_identity(secured.getpeercert()).aic != endpoint.aic:
                raise ValueError(f"{role}: unexpected TLS peer AIC")
            secured.sendall(
                f"GET /health HTTP/1.1\r\nHost: {config.public_host}\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            response = http.client.HTTPResponse(secured)
            response.begin()
            body = response.read(16385)
            if response.status != 200 or len(body) > 16384:
                raise ValueError(f"{role}: health request failed")
            payload = json.loads(body)
            if payload.get("role") != role or payload.get("aic") != endpoint.aic or payload.get("status") != "healthy":
                raise ValueError(f"{role}: health identity mismatch")
    return {"status": "healthy", "role": role, "aic": endpoint.aic}


def deployment_acs(config: RuntimeConfig, role: str, approved: dict) -> dict:
    """Preserve the registry receipt, updating only implemented runtime fields."""
    import copy

    if approved.get("aic") != config.agents[role].aic or approved.get("active") is not True:
        raise ValueError(f"{role}: approved ACS does not match the runtime identity")
    acs = copy.deepcopy(approved)
    acs["securitySchemes"] = {"mtls": {"type": "mutualTLS"}}
    acs["endPoints"] = [{"url": config.url(role), "transport": "JSONRPC", "security": [{"mtls": []}]}]
    acs["defaultInputModes"] = ["text/plain", "application/json"]
    acs["defaultOutputModes"] = ["application/json"]
    for skill in acs.get("skills", []):
        skill["inputModes"] = ["text/plain", "application/json"]
    if role == "leader":
        acs["skills"] = [{
            "id": "leader.ask", "name": "汽车零部件协同问答", "version": acs["version"],
            "description": "接收业务问题，按需调用报价、采购、生产跟单与质量文档岗位的真实只读技能，汇总结果和调用依据。",
            "tags": ["汽车零部件", "业务协同", "质量", "采购", "生产"],
            "examples": ["查询当前已有 PPAP 任务及其编制状态", "按订单号查询生产进度与质量风险"],
            "inputModes": ["text/plain", "application/json"], "outputModes": ["application/json"],
        }]
    return acs


def export_acs(config: RuntimeConfig) -> Path:
    """Export complete public ACS files only after all local mTLS probes pass.

    This does not submit to the registry and does not prove public reachability.
    """
    import os

    for role in ROLES:
        probe_role(config, role)
    project = Path(__file__).resolve().parents[3]
    prepared = {}
    for index, role in enumerate(ROLES, 1):
        original = json.loads((project / "registration/wutong/approved" / f"{role}.acs.json").read_text(encoding="utf-8"))
        prepared[f"0{index}-{role}.acs.json"] = deployment_acs(config, role, original)
    target = config.path.parent / "acs"
    if target.is_symlink():
        raise ValueError("ACS output directory must not be a symbolic link")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, acs in prepared.items():
        destination = target / name
        if destination.is_symlink():
            raise ValueError("ACS output file must not be a symbolic link")
        temporary = target / (name + ".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(acs, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(destination)
    return target


# 启动脚本在拉起子进程前会把 .env 的值写进子进程环境；此时子进程无法仅凭
# "变量已存在"判断来源。启动脚本用这个标记显式告知来源，避免把 dotenv 误报成 shell。
_KEY_SOURCE_MARKER = "DEEPSEEK_API_KEY_SOURCE"


def deepseek_key_status(*, from_shell: bool) -> dict:
    """只报告密钥的编码判定，绝不返回值本身。

    平台侧失败时只看到「技能执行失败」，根因往往在密钥编码；这里在启动阶段
    就把判定写进角色日志，运维不必再去翻请求期异常。
    """
    import os

    value = os.getenv("DEEPSEEK_API_KEY", "").strip()
    non_ascii = [index for index, char in enumerate(value) if ord(char) > 127]
    marker = os.getenv(_KEY_SOURCE_MARKER, "").strip().lower()
    if marker == "dotenv":
        source = "dotenv"
    elif marker == "shellenvironment":
        source = "shellEnvironment"
    else:
        source = "shellEnvironment" if from_shell else "dotenv"
    return {
        "configured": bool(value),
        "length": len(value),
        "ascii": value.isascii(),
        "firstNonAsciiPosition": non_ascii[0] if non_ascii else None,
        "source": source,
    }


def _business_environment() -> None:
    import os
    from dotenv import load_dotenv

    project = Path(__file__).resolve().parents[3]
    # load_dotenv(override=False) 让已存在的环境变量优先于 .env；
    # 因此必须先记录启动时是否已有该变量，才能说清密钥到底来自哪里。
    key_from_shell = "DEEPSEEK_API_KEY" in os.environ
    load_dotenv(project / ".env", override=False)
    load_dotenv(project / "backend" / ".env", override=False)
    if os.getenv("APP_ADAPTER_MODE", "").strip().lower() != "real":
        raise ValueError("The Wutong listener requires APP_ADAPTER_MODE=real")
    if os.getenv("MOCK_DEMO_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise ValueError("Disable MOCK_DEMO_ENABLED for the Wutong listener")
    status = deepseek_key_status(from_shell=key_from_shell)
    print("[配置] DeepSeek 密钥判定: " + json.dumps(status, ensure_ascii=False), flush=True)
    if status["configured"] and not status["ascii"]:
        source = ("启动时的 shell 环境变量（优先级高于 .env）"
                  if status["source"] == "shellEnvironment" else ".env")
        raise ValueError(
            "DEEPSEEK_API_KEY 含非 ASCII 字符，首个位置 %d；来源：%s。"
            "请修正后重启接入进程（若是 shell 环境变量，先 unset 再启动）。"
            % (status["firstNonAsciiPosition"], source)
        )


def _partner_services(config: RuntimeConfig) -> dict:
    from app.aip.agents.quotation_aip import create_quotation_aip_service
    from app.aip.agents.procurement_aip import create_procurement_aip_service
    from app.aip.agents.tracking_aip import create_tracking_aip_service
    from app.aip.agents.quality_document_aip import create_quality_document_aip_service
    from app.services.capability_catalog import (
        allowed_skill_ids_by_agent, build_capability_catalog, store_runtime_catalog,
    )
    from app.services.coordinator import REAL_SKILL_TOOLS

    factories = {
        "quotation": create_quotation_aip_service,
        "procurement": create_procurement_aip_service,
        "tracking": create_tracking_aip_service,
        "quality-document": create_quality_document_aip_service,
    }
    services = {role: factory(aic=config.agents[role].aic) for role, factory in factories.items()}
    catalog = build_capability_catalog(REAL_SKILL_TOOLS, {
        role: set(service.registered_skill_ids()) for role, service in services.items()
    })
    store_runtime_catalog(catalog)
    allowed = allowed_skill_ids_by_agent(catalog)
    for role, service in services.items():
        service.restrict_skills(allowed[role])
    return services


def _outbound_access_reporter(amp_reporter, leader_aic: str, leader_role: str,
                              partner_endpoint: AgentEndpoint):
    """Leader 出站 Partner 调用的 caller 侧 access 上报钩子。

    平台按 taskId 双方匹配计一次调用：Partner 进程已上报 callee 侧记录，
    出站侧必须补一条 caller 记录（caller/callee 对调、correlationId 用同一
    taskId），任务才能配对计入调用量/被调用量统计。
    """
    if amp_reporter is None or not amp_reporter.settings.report_access:
        return None

    async def report(info) -> None:
        await amp_reporter.report_access(
            aic=leader_aic, role=leader_role,
            method="POST", path=partner_endpoint.rpc_path, route=partner_endpoint.rpc_path,
            status_code=info.status_code, duration_ms=info.duration_ms,
            caller_aic=leader_aic,
            callee_aic=partner_endpoint.aic, callee_service_name=partner_endpoint.role,
            task_id=info.task_id,
            request_bytes=info.request_bytes, response_bytes=info.response_bytes,
        )

    return report


def create_app(config: RuntimeConfig, role: str, *, service=None, coordinator_factory=None):
    """Build only the selected role's authenticated RPC surface.

    Test services/factories can be injected without loading business credentials
    or contacting the ERP, MES, registry or model provider.
    """
    from fastapi import FastAPI, HTTPException, Request
    from acps_sdk.aip.aip_peer_cert import AipPeerCertificateMiddleware, get_request_peer_aic
    from app.aip.aip_agent_service import register_aip_agent_router

    endpoint = config.agents[role]
    amp_reporter = None
    if config.amp is not None:
        from app.aip.amp_reporter import AmpKafkaReporter

        amp_reporter = AmpKafkaReporter(config.amp)
    outbound_clients: dict = {}
    if service is None:
        from app.aip.aip_agent_client import AipAgentClient
        from app.aip.leader_service import create_leader_aip_service
        from app.aip.natural_language import attach_natural_language

        services = _partner_services(config)
        if role == "leader":
            context = client_ssl_context(config)
            connect_host = "::1" if ":" in config.bind_host else "127.0.0.1"
            if config.bind_host not in {"0.0.0.0", "::"}:
                connect_host = config.bind_host
            outbound_clients = {partner: AipAgentClient(
                partner_url=config.url(partner), leader_id=endpoint.aic,
                ssl_context=context, identity_binding_enabled=True,
                expected_partner_aic=config.agents[partner].aic,
                transport=LocalPeerTransport(config.public_host, connect_host, context),
                access_reporter=_outbound_access_reporter(
                    amp_reporter, endpoint.aic, role, config.agents[partner],
                ),
            ) for partner in ROLES if partner != "leader"}
            service = create_leader_aip_service(
                endpoint.aic, outbound_clients=outbound_clients, coordinator_factory=coordinator_factory,
            )
        else:
            service = attach_natural_language(services[role], coordinator_factory=coordinator_factory)
    if service.aic != endpoint.aic:
        raise ValueError("Service identity differs from TLS listener configuration")

    _caller_aic: list[str | None] = []
    if amp_reporter is not None and config.amp.report_access:
        from app.aip.amp_reporter import extract_task_id

        original_dispatch = service.dispatch_rpc

        def _rpc_wire_size(payload: Any) -> int | None:
            """序列化后的真实线缆字节数（尽力而为，失败不阻断上报）。"""
            try:
                if payload is None:
                    return None
                if hasattr(payload, "model_dump_json"):
                    return len(payload.model_dump_json().encode("utf-8"))
                return len(json.dumps(payload, default=str).encode("utf-8"))
            except Exception:
                return None

        async def dispatch_rpc_with_amp(rpc_request):
            started = time.monotonic()
            caller = _caller_aic[0] if _caller_aic else None
            try:
                response = await original_dispatch(rpc_request)
            except Exception:
                await amp_reporter.report_access(
                    aic=endpoint.aic, role=role,
                    method=getattr(rpc_request, "method", None),
                    path=endpoint.rpc_path, status_code=500,
                    duration_ms=(time.monotonic() - started) * 1000,
                    caller_aic=caller, task_id=extract_task_id(rpc_request),
                    route=endpoint.rpc_path,
                    request_bytes=_rpc_wire_size(rpc_request),
                )
                raise
            status_code = 200 if getattr(response, "error", None) is None else 500
            await amp_reporter.report_access(
                aic=endpoint.aic, role=role,
                method=getattr(rpc_request, "method", None),
                path=endpoint.rpc_path, status_code=status_code,
                duration_ms=(time.monotonic() - started) * 1000,
                caller_aic=caller, task_id=extract_task_id(rpc_request, response),
                route=endpoint.rpc_path,
                request_bytes=_rpc_wire_size(rpc_request),
                response_bytes=_rpc_wire_size(response),
            )
            return response

        service.dispatch_rpc = dispatch_rpc_with_amp  # type: ignore[method-assign]

    async def authorize(request: Request, command) -> None:
        peer = get_request_peer_aic(request)
        if peer is None or ("*" not in endpoint.allowed_callers and peer not in endpoint.allowed_callers):
            raise HTTPException(status_code=403, detail="Caller AIC is not allowed for this role")
        if amp_reporter is not None:
            _caller_aic.clear()
            _caller_aic.append(peer)

    @asynccontextmanager
    async def lifespan(app):
        amp_task = None
        if amp_reporter is not None:
            amp_task = asyncio.create_task(
                amp_reporter.run_heartbeat(endpoint.aic, service_name=role), name="amp-heartbeat",
            )
        try:
            yield
        finally:
            if amp_task is not None:
                amp_task.cancel()
                with suppress(asyncio.CancelledError):
                    await amp_task
            if amp_reporter is not None:
                await amp_reporter.aclose()
            await asyncio.gather(*(client.close() for client in outbound_clients.values()))
            shutdown = getattr(service, "aclose", None)
            if shutdown is not None:
                await shutdown()

    app = FastAPI(title=f"汽车零部件智能体 · {role}", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(AipPeerCertificateMiddleware)

    register_aip_agent_router(app, service, endpoint.rpc_path,
                             identity_binding_enabled=True, command_authorizer=authorize)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "role": role, "aic": endpoint.aic}

    app.state.wutong_service = service
    app.state.amp_reporter = amp_reporter
    app.state.outbound_clients = outbound_clients
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Wutong AIP identity with mandatory mTLS")
    parser.add_argument("--config", required=True)
    parser.add_argument("--role", choices=ROLES)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--export-acs", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.check:
            check_configuration(config)
            print(json.dumps({"status": "ok", "checkedRoles": list(ROLES)}, ensure_ascii=False))
            return 0
        if args.export_acs:
            check_configuration(config)
            print(json.dumps({"status": "prepared", "directory": str(export_acs(config)),
                              "submitted": False, "publicReachabilityVerified": False}, ensure_ascii=False))
            return 0
        if not args.role:
            raise ValueError("--role is required for starting or probing a listener")
        if args.probe:
            print(json.dumps(probe_role(config, args.role), ensure_ascii=False))
            return 0
        check_configuration(config)
        _business_environment()
        import uvicorn
        app = create_app(config, args.role)
        uvicorn.Server(make_server_config(config, args.role, app)).run()
        return 0
    except (OSError, ValueError, ssl.SSLError, subprocess.SubprocessError) as exc:
        # No exception object/traceback: deployment diagnostics must not reveal
        # credential values loaded by a business integration.
        print(f"Wutong runtime check failed: {type(exc).__name__}", file=sys.stderr)
        if isinstance(exc, ValueError):
            print(str(exc), file=sys.stderr)
        return 1


def make_server_config(config: RuntimeConfig, role: str, app):
    import uvicorn
    from acps_sdk.aip.aip_peer_cert import AipPeerCertH11Protocol

    endpoint = config.agents[role]
    uv_config = uvicorn.Config(
        app, host=config.bind_host, port=endpoint.port,
        http=AipPeerCertH11Protocol, ws="none", proxy_headers=False,
        ssl_certfile=str(endpoint.cert_file), ssl_keyfile=str(endpoint.key_file),
        ssl_ca_certs=str(config.trust_bundle), ssl_cert_reqs=ssl.CERT_REQUIRED,
        ssl_version=ssl.PROTOCOL_TLS_SERVER, access_log=True, lifespan="on",
    )
    uv_config.load()
    uv_config.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    return uv_config


if __name__ == "__main__":
    raise SystemExit(main())
