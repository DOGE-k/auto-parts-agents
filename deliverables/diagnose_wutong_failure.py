"""服务器侧只读诊断：找出梧桐接入进程真实的失败原因（不打印任何密钥）。

用法（服务器上，任意目录）：
    /home/ubuntu/apps/auto-parts-agents/.venv/bin/python /home/ubuntu/diagnose_wutong_failure.py \
        --root /home/ubuntu/apps/auto-parts-agents

输出全部脱敏：密钥只显示长度、是否 ASCII、以及"进程内值与 .env 是否一致"。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

ROLES = ("leader", "quotation", "procurement", "tracking", "quality-document")
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"),
)


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[已隐藏]", text)
    return text


def section(title: str) -> None:
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)


def run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<命令执行失败: {type(exc).__name__}>"
    return (result.stdout or "") + (result.stderr or "")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        values[name] = value.strip().strip('"').strip("'")
    return values


def key_facts(value: str) -> dict:
    non_ascii = [i for i, ch in enumerate(value) if ord(ch) > 127]
    return {
        "length": len(value),
        "is_ascii": value.isascii(),
        "first_non_ascii_position": non_ascii[0] if non_ascii else None,
        "starts_with_sk": value.startswith("sk-"),
    }


def process_env(pid: str, name: str) -> str | None:
    try:
        raw = (Path("/proc") / pid / "environ").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    for item in raw.split(b"\0"):
        if item.startswith(name.encode() + b"="):
            return os.fsdecode(item.split(b"=", 1)[1])
    return None


def listening_processes() -> list[dict]:
    text = run(["ps", "-eo", "pid,lstart,cmd"])
    found = []
    for line in text.splitlines():
        if "wutong_runtime" not in line or "--role" not in line:
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        pid = parts[0]
        started = " ".join(parts[1:6])
        role = ""
        for index, token in enumerate(parts):
            if token == "--role" and index + 1 < len(parts):
                role = parts[index + 1]
        try:
            start_dt = datetime.strptime(started, "%a %b %d %H:%M:%S %Y")
        except ValueError:
            start_dt = None
        found.append({"pid": pid, "role": role, "started": started, "start_dt": start_dt})
    return found


def tcp_check(host: str, port: int, timeout: float = 4.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "可连接"
    except OSError as exc:
        return f"失败({type(exc).__name__})"


def tail_failures(log_path: Path, wanted: int = 45) -> None:
    if not log_path.is_file():
        print(f"{log_path.name}: 文件不存在")
        return
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    last_failure = None
    for index, line in enumerate(lines):
        if "AIP 技能失败" in line:
            last_failure = index
    print(f"{log_path.name}: 共 {len(lines)} 行，最后一次技能失败在第 {last_failure + 1 if last_failure is not None else '—'} 行")
    if last_failure is None:
        return
    start = max(0, last_failure - 2)
    end = min(len(lines), last_failure + wanted)
    for line in lines[start:end]:
        print("  " + redact(line)[:400])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/ubuntu/apps/auto-parts-agents")
    parser.add_argument("--lines", type=int, default=45)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not (root / "backend" / "app").is_dir():
        print("参数 --root 必须是 auto-parts-agents 项目根目录")
        return 2

    section("1. 代码版本")
    print("HEAD: " + run(["git", "rev-parse", "HEAD"], cwd=root).strip())
    print("最近提交: " + run(["git", "log", "-1", "--oneline"], cwd=root).strip())
    status = run(["git", "status", "--porcelain"], cwd=root).strip()
    print("工作区改动:")
    if status:
        for line in status.splitlines()[:30]:
            print("  " + line)
        if len(status.splitlines()) > 30:
            print("  ... 其余省略")
    else:
        print("  （干净）")

    section("2. .env 与密钥")
    env_path = root / ".env"
    backend_env = root / "backend" / ".env"
    print(f"根 .env: {env_path}  存在={env_path.is_file()}")
    print(f"backend/.env: {backend_env}  存在={backend_env.is_file()}")
    values = read_env_file(env_path)
    if not values.get("DEEPSEEK_API_KEY") and backend_env.is_file():
        values = read_env_file(backend_env)
        print("（根 .env 没有 DEEPSEEK_API_KEY，改用 backend/.env）")
    file_key = values.get("DEEPSEEK_API_KEY", "")
    if not file_key:
        print("未读到 DEEPSEEK_API_KEY")
    else:
        print("文件中的密钥: " + str(key_facts(file_key)))
    print("LLM_MODEL: " + str(values.get("LLM_MODEL", "<未设置>")))
    print("DEEPSEEK_BASE_URL: " + str(values.get("DEEPSEEK_BASE_URL", "<未设置>")))
    if env_path.is_file():
        print(".env 修改时间: " + datetime.fromtimestamp(env_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))

    section("2b. 可能覆盖 .env 的其他来源")
    print("当前 shell 环境变量 DEEPSEEK_API_KEY: " + (
        "未设置" if "DEEPSEEK_API_KEY" not in os.environ
        else str(key_facts(os.environ["DEEPSEEK_API_KEY"]))))
    profile_hits = []
    for candidate in (Path.home() / ".bashrc", Path.home() / ".profile",
                      Path.home() / ".bash_profile", Path("/etc/environment")):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "DEEPSEEK_API_KEY" in line and not line.strip().startswith("#"):
                profile_hits.append(f"{candidate}: {redact(line.strip())[:120]}")
    if profile_hits:
        print("在启动脚本里发现了 DEEPSEEK_API_KEY（这些会覆盖 .env，因为代码用的是 override=False）：")
        for hit in profile_hits:
            print("  " + hit)
    else:
        print("~/.bashrc、~/.profile、/etc/environment 里没有 DEEPSEEK_API_KEY")

    section("3. 运行中的接入进程")
    processes = listening_processes()
    if not processes:
        print("没有找到 wutong_runtime 进程（接入服务未运行）")
    for item in processes:
        print(f"  {item['role']:<16} pid={item['pid']:<7} 启动于 {item['started']}")
        running = process_env(item["pid"], "DEEPSEEK_API_KEY")
        if running is None:
            print("      进程环境: 读不到（可能不是同一用户）")
            continue
        same = running == file_key
        print(f"      进程内密钥: {key_facts(running)} 与 .env 一致={same}")
        if not same:
            print("      >>> 进程用的是旧密钥，必须重启接入进程才会生效")
        if item["start_dt"] and env_path.is_file():
            if env_path.stat().st_mtime > item["start_dt"].timestamp():
                print("      >>> .env 在该进程启动之后被修改过，进程内仍是旧值")

    section("4. 最近一次技能失败日志")
    logs = root / "backend" / "data" / "wutong-runtime" / "logs"
    if not logs.is_dir():
        print(f"日志目录不存在: {logs}")
    else:
        for role in ROLES:
            tail_failures(logs / f"{role}.log", args.lines)

    section("5. 依赖连通性（服务器本机）")
    print("ERPNext localhost:8080 -> " + tcp_check("127.0.0.1", 8080))
    print("OpenMES localhost:80   -> " + tcp_check("127.0.0.1", 80))
    print("DeepSeek api:443       -> " + tcp_check("api.deepseek.com", 443))

    section("6. 怎么读这份输出")
    print("- 第 2、3 节里若出现「与 .env 一致=False」或「.env 在进程启动之后被修改」，重启接入进程即可。")
    print("- 第 4 节最后一段 Traceback 的异常类型就是真实原因。")
    print("- 第 5 节任何一项失败，说明对应依赖在该服务器上不可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
