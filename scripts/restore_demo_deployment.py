#!/usr/bin/env python3
"""Restore the pre-hardening demo topology without restoring databases or secrets.

Linux server only. `prepare` validates and privately backs up the current config;
`apply` makes the reviewed listener changes; `verify` is read-only. The user has
explicitly requested the original public demo/management ports again.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

DIAGNOSTICS = None


def run(command, *, cwd=None, capture=True):
    result = subprocess.run(command, cwd=cwd, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        if DIAGNOSTICS is not None and DIAGNOSTICS.is_dir():
            with (DIAGNOSTICS / "failed-command.log").open("a", encoding="utf-8") as output:
                output.write(f"program={Path(command[0]).name}; exit={result.returncode}\n")
                output.write((result.stdout or "") + (result.stderr or ""))
        raise RuntimeError(f"Command failed: {Path(command[0]).name}; private diagnostics were not printed")
    return result.stdout or ""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def env_value(path, name):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


def update_env(path, changes):
    """Replace only named non-secret values; retain all credentials and other keys."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    seen = set()
    output = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in changes:
            if key not in seen:
                output.append(f"{key}={changes[key]}")
                seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in changes.items() if key not in seen)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(0o600)


def status(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, urllib.error.URLError):
        return 0


def await_status(url, expected=200):
    for _ in range(25):
        if status(url) == expected:
            return
        time.sleep(1)
    raise RuntimeError(f"Expected HTTP {expected} at reviewed local endpoint")


def compose(root, project):
    if project == "openmes":
        folder = root / "services/OpenMes"
        return ["docker", "compose", "-p", "openmes", "--env-file", str(folder / ".env"),
                "-f", str(folder / "docker-compose.yml")]
    folder = root / "services/frappe_docker"
    result = ["docker", "compose", "-p", "erpnext", "--env-file", str(folder / ".env")]
    for relative in ["compose.yaml", "overrides/compose.mariadb.yaml", "overrides/compose.redis.yaml", "overrides/compose.noproxy.yaml"]:
        result.extend(["-f", str(folder / relative)])
    return result


def current_counts(root):
    db = sqlite3.connect(f"file:{root / 'backend/data/demo.db'}?mode=ro", uri=True)
    try:
        return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ["assistant_sessions", "assistant_messages", "project_users"]}
    finally:
        db.close()


def prepare(root, original, state):
    files = [".env", "services/OpenMes/Caddyfile", "services/frappe_docker/.env", "scripts/start_app.sh", "scripts/stop_app.sh"]
    for relative in files:
        if not (original / "files" / relative).is_file() or not (root / relative).is_file():
            raise RuntimeError(f"Required original/current file missing: {relative}")
    if (original / "files/services/OpenMes/Caddyfile").read_bytes() != (root / "services/OpenMes/Caddyfile").read_bytes():
        raise RuntimeError("Original Caddy source has unrelated edits; review before restoring")
    if env_value(original / "files/.env", "OPENMES_BASE_URL") != "http://localhost":
        raise RuntimeError("Unexpected original OpenMES URL; review before restoring")
    if env_value(original / "files/services/frappe_docker/.env", "HTTP_PUBLISH_PORT") != "8080":
        raise RuntimeError("Unexpected original ERP port; review before restoring")
    for name, action in [("start_app.sh", "start"), ("stop_app.sh", "stop")]:
        current = (root / "scripts" / name).read_text()
        guard = f'''# SECURE_WEB_SYSTEMD_GUARD
if systemctl is-enabled --quiet auto-parts-api.service 2>/dev/null; then
  sudo -n systemctl {action} auto-parts-api.service
  printf '%s\\n' 'Production browser API managed by systemd; public Vite/9000 fallback is disabled.'
  exit 0
fi
'''
        current = current.replace(guard, "")
        previous = (original / "files/scripts" / name).read_text()
        normalized = lambda text: "\n".join(line for line in text.splitlines() if line.strip())
        if normalized(current) != normalized(previous):
            raise RuntimeError(f"Startup helper has unrelated edits: {name}")
    run(["bash", "-n", str(original / "files/scripts/start_app.sh")])
    run(["bash", "-n", str(original / "files/scripts/stop_app.sh")])
    # Parse the original Caddy syntax using the installed, running image.
    raw = (original / "files/services/OpenMes/Caddyfile").read_bytes()
    parsed = subprocess.run(["docker", "exec", "-i", "openmmes-caddy", "caddy", "adapt", "--config", "/dev/stdin", "--adapter", "caddyfile"],
                            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if parsed.returncode:
        raise RuntimeError("Original Caddyfile no longer parses in current image")
    state.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(state, 0o700)
    hashes = {}
    for relative in files:
        source = root / relative
        hashes[relative] = digest(source)
        target = state / "before" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o600)
    runtime = root / "backend/data/wutong-runtime/runtime.json"
    manifest = {"root": str(root), "original_backup": str(original), "current_hashes": hashes,
                "runtime_config_sha256": digest(runtime), "database_counts": current_counts(root),
                "prepared_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    (state / "prepared.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"PREPARED_PRIVATE_RESTORE_STATE={state}")
    print("No service, account, credential, database or listener was changed")


def apply(root, original, state, secure_state):
    manifest = json.loads((state / "prepared.json").read_text())
    if manifest["root"] != str(root) or manifest["original_backup"] != str(original):
        raise RuntimeError("Prepared paths do not match requested restoration")
    for relative, expected in manifest["current_hashes"].items():
        if digest(root / relative) != expected:
            raise RuntimeError(f"Configuration changed after prepare: {relative}")
    runtime = root / "backend/data/wutong-runtime/runtime.json"
    if digest(runtime) != manifest["runtime_config_sha256"]:
        raise RuntimeError("AIP configuration changed after prepare")
    # Do not kill an unrelated process merely because it occupies a demo port.
    for line in run(["ss", "-H", "-lnt"]).splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[3].rsplit(":", 1)[-1] in {"5173", "9000"}:
            raise RuntimeError("A demo port is already occupied; inspect its owner before restoring")
    unit_text = run(["systemctl", "cat", "auto-parts-api.service"])
    if str(root) not in unit_text or "--uds /run/auto-parts-api/backend.sock" not in unit_text:
        raise RuntimeError("Production unit is not the one created by this task")
    update_env(root / ".env", {"OPENMES_BASE_URL": "http://localhost", "PROJECT_AUTH_REQUIRE_LOGIN": "true", "PROJECT_AUTH_COOKIE_SECURE": "false"})
    update_env(root / "services/frappe_docker/.env", {"HTTP_PUBLISH_PORT": "8080"})
    for project, service in [("openmes", "caddy"), ("erpnext", "frontend")]:
        expanded = json.loads(run(compose(root, project) + ["config", "--format", "json"]))
        ports = expanded["services"][service].get("ports", [])
        if project == "openmes":
            if {int(p["target"]) for p in ports} != {80, 443}:
                raise RuntimeError("Original OpenMES port preflight failed")
        elif len(ports) != 1 or int(ports[0]["target"]) != 8080 or str(ports[0]["published"]) != "8080" or ports[0].get("host_ip", "0.0.0.0") not in {"0.0.0.0", ""}:
            raise RuntimeError("Original ERP port preflight failed")
    print("Original port configuration preflight passed; retiring only the added API unit", flush=True)
    run(["sudo", "systemctl", "disable", "--now", "auto-parts-api.service"])
    unit = Path("/etc/systemd/system/auto-parts-api.service")
    if unit.exists():
        run(["sudo", "mv", "--", str(unit), str(state / "retired-auto-parts-api.service")])
        run(["sudo", "systemctl", "daemon-reload"])
    if secure_state.exists():
        if secure_state.is_symlink() or secure_state.name != "auto-parts-secure-web":
            raise RuntimeError("Unexpected secure deployment state path")
        secure_state.rename(state / "retired-secure-web")
    # Restore only our changed startup wrappers; credentials/databases are untouched.
    for relative in ["scripts/start_app.sh", "scripts/stop_app.sh", "services/OpenMes/Caddyfile"]:
        shutil.copyfile(original / "files" / relative, root / relative)
        (root / relative).chmod(0o755 if relative.endswith(".sh") else 0o644)
    for project, service in [("openmes", "caddy"), ("erpnext", "frontend")]:
        run(compose(root, project) + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", service])
        print(f"Restored {project} {service} container configuration", flush=True)
    await_status("http://127.0.0.1/api/health")
    await_status("http://127.0.0.1:8080/api/method/ping")
    # Existing source defaults remain same-origin; do not restore the browser's loopback API bug.
    environment = os.environ.copy()
    environment.update({"VITE_API_BASE_URL": "/api", "PROJECT_AUTH_REQUIRE_LOGIN": "true", "PROJECT_AUTH_COOKIE_SECURE": "false"})
    result = subprocess.run(["bash", "scripts/start_app.sh"], cwd=root, env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (state / "start-demo.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Demo startup failed; diagnostic output retained privately")
    for command in [["bash", "scripts/stop_wutong.sh"], ["bash", "scripts/start_wutong.sh", str(runtime)]]:
        result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
        with (state / "aip-restart.log").open("a", encoding="utf-8") as output:
            output.write(result.stdout)
        if result.returncode:
            raise RuntimeError("AIP restart failed; diagnostic output retained privately")
    verify(root, state)
    (state / "applied.json").write_text(json.dumps({"applied_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}), encoding="utf-8")
    print("DEMO_TOPOLOGY_RESTORED; login and session isolation remain enabled")


def verify(root, state):
    for url in ["http://127.0.0.1/api/health", "http://127.0.0.1:8080/api/method/ping", "http://127.0.0.1:9000/api/health", "http://127.0.0.1:5173/"]:
        await_status(url)
    for url in ["http://127.0.0.1:9000/api/real-orders/assistant/sessions", "http://127.0.0.1:5173/api/real-orders/assistant/sessions"]:
        await_status(url, 401)
    listing = run(["ss", "-H", "-lnt"])
    lines = [line.split() for line in listing.splitlines()]
    for port, host in [("5173", "0.0.0.0"), ("9000", "127.0.0.1")]:
        if not any(len(row) >= 4 and row[3] == host + ":" + port for row in lines):
            raise RuntimeError("Expected demo listener missing")
    if any(len(row) >= 4 and row[3].rsplit(":", 1)[-1] == "18081" for row in lines):
        raise RuntimeError("Retired MES management listener remains")
    if subprocess.run(["systemctl", "is-active", "--quiet", "auto-parts-api.service"]).returncode == 0:
        raise RuntimeError("Retired production API is still active")
    if subprocess.run(["systemctl", "is-enabled", "--quiet", "auto-parts-api.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("Retired production API is still enabled at boot")
    for name, expected_targets in [("erpnext-frontend-1", {"8080/tcp"}), ("openmmes-caddy", {"80/tcp", "443/tcp"})]:
        inspected = json.loads(run(["docker", "inspect", name]))[0]
        bindings = inspected["HostConfig"]["PortBindings"]
        if set(bindings) != expected_targets or any(not entries or not any(item.get("HostIp", "") in {"", "0.0.0.0", "::"} for item in entries) for entries in bindings.values()):
            raise RuntimeError("A management port was not restored to its original public mapping")
    manifest = json.loads((state / "prepared.json").read_text())
    runtime = root / "backend/data/wutong-runtime/runtime.json"
    if digest(runtime) != manifest["runtime_config_sha256"]:
        raise RuntimeError("AIP identity/endpoint configuration was changed")
    after = current_counts(root)
    if any(after[key] < value for key, value in manifest["database_counts"].items()):
        raise RuntimeError("A protected database row count decreased")
    for role in ["leader", "quotation", "procurement", "tracking", "quality-document"]:
        run([str(root / ".venv/bin/python"), "-m", "app.aip.wutong_runtime", "--config", str(runtime), "--role", role, "--probe"], cwd=root / "backend")
    print("HTTP demo, mandatory browser login, preserved data and five-role mTLS health verified")


def main():
    global DIAGNOSTICS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "apply", "verify"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backup", type=Path, required=True, help="Original pre-hardening private backup")
    parser.add_argument("--state", type=Path, required=True, help="New private restoration record directory")
    parser.add_argument("--secure-state", type=Path, default=Path.home() / ".local/state/auto-parts-secure-web")
    args = parser.parse_args()
    if os.name != "posix" or not Path("/proc").is_dir():
        parser.error("Run only on the Linux project server")
    os.umask(0o077)
    root, original, state = args.root.resolve(), args.backup.resolve(), args.state.resolve()
    if any(path.is_symlink() for path in [args.root, args.backup, args.state, args.secure_state]):
        parser.error("Symlinked control paths are not accepted")
    # Compose must use the reviewed env-file port value, not a stale exported override.
    os.environ.pop("HTTP_PUBLISH_PORT", None)
    if args.action == "prepare":
        prepare(root, original, state)
    elif args.action == "apply":
        DIAGNOSTICS = state
        apply(root, original, state, args.secure_state.resolve())
    else:
        DIAGNOSTICS = state
        verify(root, state)


if __name__ == "__main__":
    main()
