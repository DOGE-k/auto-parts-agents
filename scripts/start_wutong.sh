#!/usr/bin/env bash
# Linux only. Starts only the five Wutong mTLS listeners; keeps the existing app running.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${WUTONG_PYTHON:-$root/.venv/bin/python}"
config="${1:-$root/backend/data/wutong-runtime/runtime.json}"
if [[ $# -gt 1 || ! -x "$python" ]]; then
  printf '%s\n' '[失败] 用法：bash scripts/start_wutong.sh [配置文件绝对路径]；需项目 .venv/bin/python。' >&2
  exit 1
fi
exec "$python" - "$root" "$config" "$python" <<'PY'
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROLES = ("leader", "quotation", "procurement", "tracking", "quality-document")
root = Path(sys.argv[1]).resolve()
config = Path(sys.argv[2]).resolve(strict=True)
python = str(Path(sys.argv[3]).absolute())
state = root / "backend/data/wutong-runtime"
os.umask(0o077)
if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
    raise SystemExit("[失败] 此脚本需要 Linux 的 pidfd 支持；未启动任何服务。")
for directory in (state, state / "pids", state / "logs"):
    if directory.is_symlink():
        raise SystemExit("[失败] 运行目录不能是符号链接。")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.geteuid():
        raise SystemExit("[失败] 运行目录所有者与当前用户不同。")
    directory.chmod(0o700)
lock_fd = os.open(state / "management.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("[失败] 另一条梧桐启停命令正在运行。")

def command(role):
    return [python, "-m", "app.aip.wutong_runtime", "--config", str(config), "--role", role]

def identity(pid):
    proc = Path("/proc") / str(pid)
    try:
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        argv = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        return {"pid": pid, "startTicks": fields[19], "uid": proc.stat().st_uid,
                "argv": [os.fsdecode(arg) for arg in argv], "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None

def matches(record):
    actual = identity(record["pid"])
    return actual is not None and actual["state"] != "Z" and all(
        actual[key] == record[key] for key in ("startTicks", "uid", "argv")
    ) and record["uid"] == os.geteuid()

def owns_listener(record, port):
    """A successful probe must belong to our recorded process, not another listener."""
    inodes = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        for row in Path(filename).read_text().splitlines()[1:]:
            fields = row.split()
            if fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                inodes.add("socket:[" + fields[9] + "]")
    try:
        for descriptor in (Path("/proc") / str(record["pid"]) / "fd").iterdir():
            try:
                if os.readlink(descriptor) in inodes:
                    return True
            except FileNotFoundError:
                continue
    except FileNotFoundError:
        return False
    return False

def stop_owned(record):
    if not matches(record):
        return
    try:
        fd = os.pidfd_open(record["pid"], 0)
    except ProcessLookupError:
        return
    try:
        if not matches(record):
            return
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while matches(record) and time.monotonic() < deadline:
            time.sleep(0.1)
        if matches(record):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while matches(record) and time.monotonic() < deadline:
                time.sleep(0.1)
    finally:
        os.close(fd)

check = subprocess.run([python, "-m", "app.aip.wutong_runtime", "--config", str(config), "--check"],
                       cwd=root / "backend", check=False)
if check.returncode:
    raise SystemExit("[失败] 配置或证书检查未通过；未启动任何新服务。")
settings = json.loads(config.read_text(encoding="utf-8"))
existing = {}
for role in ROLES:
    pid_file = state / "pids" / (role + ".json")
    if pid_file.is_symlink():
        raise SystemExit("[失败] PID 文件不能是符号链接。")
    if pid_file.exists():
        record = json.loads(pid_file.read_text(encoding="utf-8"))
        if matches(record):
            if record["argv"] != command(role):
                raise SystemExit("[失败] 已有角色使用其他配置；请先用 stop_wutong.sh 停止该接入进程。")
            existing[role] = record
        else:
            pid_file.unlink()

# Preflight every new listener before starting any of them. Do not kill a port occupant.
for role in ROLES:
    if role in existing:
        continue
    family = socket.AF_INET6 if ":" in settings["bindHost"] else socket.AF_INET
    probe_socket = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe_socket.bind((settings["bindHost"], settings["agents"][role]["port"]))
    except OSError:
        raise SystemExit("[失败] " + role + " 端口被占用；未停止占用进程。")
    finally:
        probe_socket.close()

started = []
def interrupted(signum, frame):
    raise SystemExit(1)
signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGHUP, interrupted)
try:
    for role in ROLES:
        if role in existing:
            continue
        log_path = state / "logs" / (role + ".log")
        log_fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.fchmod(log_fd, 0o600)
        with os.fdopen(log_fd, "ab", buffering=0) as output:
            proc = subprocess.Popen(command(role), cwd=root / "backend", stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                    close_fds=True)
        record = identity(proc.pid)
        if record is None or record["state"] == "Z":
            raise RuntimeError(role + " 启动后立即退出，请查看该角色日志。")
        record.update({"role": role, "config": str(config)})
        # Track immediately, so a later readiness failure rolls back only this invocation.
        started.append(record)
        target = state / "pids" / (role + ".json")
        temporary = target.with_suffix(".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(record, output)
            output.write("\n")
        os.replace(temporary, target)
    for role in ROLES:
        record = existing.get(role) or next(item for item in started if item["role"] == role)
        deadline = time.monotonic() + 45
        while True:
            if not matches(record):
                raise RuntimeError(role + " 接入进程已退出，请查看该角色日志。")
            try:
                result = subprocess.run(command(role) + ["--probe"], cwd=root / "backend",
                                        capture_output=True, text=True, timeout=5)
            except subprocess.TimeoutExpired:
                result = None
            if (result is not None and result.returncode == 0 and matches(record)
                    and owns_listener(record, settings["agents"][role]["port"])):
                print("[通过] " + role + " 本机 mTLS 健康检查通过。", flush=True)
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(role + " 在 45 秒内未通过本机 mTLS 健康检查，请查看该角色日志。")
            time.sleep(0.5)
    print("[完成] 五个梧桐接入进程已通过本机 mTLS 检查；公网可达和平台调用还需另行验证。")
    print("[日志] " + str(state / "logs"))
except BaseException as error:
    rollback_failed = False
    for record in reversed(started):
        try:
            stop_owned(record)
            if matches(record):
                raise RuntimeError("进程仍未退出。")
            target = state / "pids" / (record["role"] + ".json")
            if target.exists() and json.loads(target.read_text(encoding="utf-8")) == record:
                target.unlink()
        except (OSError, ValueError, RuntimeError) as rollback_error:
            rollback_failed = True
            print("[失败] 回滚 " + record["role"] + ": " + str(rollback_error), file=sys.stderr)
    print("[失败] 本次启动未完成；" + ("有进程未能回滚，请用停止脚本检查。" if rollback_failed else "本次新启动进程已回滚。"), file=sys.stderr)
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise SystemExit(1)
    raise SystemExit(str(error))
PY
