#!/usr/bin/env bash
# Linux only. Stops only processes created and recorded by start_wutong.sh.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="${WUTONG_PYTHON:-$root/.venv/bin/python}"
if [[ $# -ne 0 || ! -x "$python" ]]; then
  printf '%s\n' '[失败] 用法：bash scripts/stop_wutong.sh；需项目 .venv/bin/python。' >&2
  exit 1
fi
exec "$python" - "$root" <<'PY'
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

root = Path(sys.argv[1]).resolve()
state = root / "backend/data/wutong-runtime"
roles = ("leader", "quotation", "procurement", "tracking", "quality-document")
os.umask(0o077)
if not state.exists():
    raise SystemExit("[完成] 没有由梧桐启动脚本登记的进程。")
if state.is_symlink() or state.stat().st_uid != os.geteuid():
    raise SystemExit("[失败] 运行目录所有者或类型异常，未停止任何进程。")
if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
    raise SystemExit("[失败] 此脚本需要 Linux 的 pidfd 支持，未停止任何进程。")
lock_fd = os.open(state / "management.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("[失败] 另一条梧桐启停命令正在运行。")

def identity(pid):
    proc = Path("/proc") / str(pid)
    try:
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        argv = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        return {"startTicks": fields[19], "uid": proc.stat().st_uid,
                "argv": [os.fsdecode(arg) for arg in argv], "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None

def matches(record):
    actual = identity(record["pid"])
    expected_tail = ["-m", "app.aip.wutong_runtime", "--config", record["config"], "--role", record["role"]]
    return (actual is not None and actual["state"] != "Z" and record["uid"] == os.geteuid()
            and len(record["argv"]) == 7 and record["argv"][1:] == expected_tail
            and all(actual[key] == record[key] for key in ("startTicks", "uid", "argv")))

failed = False
for role in reversed(roles):
    target = state / "pids" / (role + ".json")
    if not target.exists():
        continue
    try:
        if target.is_symlink() or target.stat().st_uid != os.geteuid():
            raise ValueError("PID 文件类型或所有者异常。")
        record = json.loads(target.read_text(encoding="utf-8"))
        if record["role"] != role:
            raise ValueError("PID 文件角色不匹配。")
        if not matches(record):
            # A stale PID must never be used to terminate an unrelated process.
            print("[跳过] " + role + " 没有身份匹配的运行进程，已清除过期记录。")
            target.unlink()
            continue
        try:
            fd = os.pidfd_open(record["pid"], 0)
        except ProcessLookupError:
            target.unlink()
            continue
        try:
            if not matches(record):
                raise ValueError("进程身份在检查期间变化，未发送信号。")
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while matches(record) and time.monotonic() < deadline:
                time.sleep(0.1)
            if matches(record):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                deadline = time.monotonic() + 3
                while matches(record) and time.monotonic() < deadline:
                    time.sleep(0.1)
            if matches(record):
                raise RuntimeError("进程仍未退出，保留 PID 记录。")
        finally:
            os.close(fd)
        target.unlink()
        print("[停止] " + role)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        failed = True
        print("[失败] " + role + ": " + str(error), file=sys.stderr)
if failed:
    raise SystemExit(1)
print("[完成] 梧桐接入进程已停止；未操作 9000/5173 或 ERP/MES 容器。")
PY
