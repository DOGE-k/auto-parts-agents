"""服务器侧只读诊断 v2：直接复现 Leader 的模型调用，打印真实异常类型。

用法（服务器）：
    cd /home/ubuntu/apps/auto-parts-agents
    .venv/bin/python /home/ubuntu/diagnose_wutong_failure.py --root /home/ubuntu/apps/auto-parts-agents

相比 v1 增加：
- 直接调用 DeepSeekClient.chat_completion（与 coordinator 同一路径），把异常完整类型和位置打出来；
- 单独测试 httpx 头编码，区分「密钥问题」和「其他 HTTP 头问题」；
- 检查 venv 里 httpx/httpcore 版本，便于比对已知兼容性问题。
输出自动脱敏，不含密钥。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
import traceback
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


def process_env(pid: str, name: str) -> tuple[str | None, str]:
    """返回 (值, 状态说明)。

    注意：/proc/<pid>/environ 只反映 exec 时的环境块，python-dotenv 之后写入
    os.environ 的值不会出现在这里。因此"读不到"不代表进程没加载 .env，
    不能据此判断密钥对错——只能说明该变量是启动后由 .env 注入的。
    """
    try:
        raw = (Path("/proc") / pid / "environ").read_bytes()
    except FileNotFoundError:
        return None, "进程不存在"
    except PermissionError:
        return None, "权限不足（不是同一用户，需要 sudo）"
    except ProcessLookupError:
        return None, "进程已退出"
    for item in raw.split(b"\0"):
        if item.startswith(name.encode() + b"="):
            return os.fsdecode(item.split(b"=", 1)[1]), "来自启动时的 shell 环境"
    return None, "启动时环境里没有（由 .env 在运行时注入，无法从这里读到）"


def wutong_processes() -> list[dict]:
    # 用 lstart 定位时间，用 args 取完整命令行（含 --config 路径），避免被截断
    text = run(["ps", "-eo", "pid,user,lstart,args"])
    found = []
    for line in text.splitlines():
        if "wutong_runtime" not in line or "--role" not in line:
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        pid, user = parts[0], parts[1]
        started = " ".join(parts[2:7])
        argv = parts[7:]
        role = ""
        config = ""
        for index, token in enumerate(argv):
            if token == "--role" and index + 1 < len(argv):
                role = argv[index + 1]
            if token == "--config" and index + 1 < len(argv):
                config = argv[index + 1]
        try:
            start_dt = datetime.strptime(started, "%a %b %d %H:%M:%S %Y")
        except ValueError:
            start_dt = None
        found.append({"pid": pid, "user": user, "role": role, "config": config,
                      "started": started, "start_dt": start_dt})
    return found





def tcp_check(host: str, port: int, timeout: float = 4.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "可连接"
    except OSError as exc:
        return f"失败({type(exc).__name__})"


def tail_failures(log_path: Path, wanted: int) -> None:
    if not log_path.is_file():
        print(f"{log_path.name}: 文件不存在")
        return
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    last = None
    for index, line in enumerate(lines):
        if "AIP 技能失败" in line:
            last = index
    print(f"{log_path.name}: 共 {len(lines)} 行，最后一次技能失败在第 {last + 1 if last is not None else '—'} 行")
    if last is None:
        return
    start = max(0, last - 2)
    end = min(len(lines), last + wanted)
    for line in lines[start:end]:
        print("  " + redact(line)[:400])


def exception_chain(exc: BaseException) -> list[str]:
    """展开 __cause__/__context__，只保留类型和脱敏消息（不打印请求头值）。"""
    chain = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__module__}.{type(current).__name__}: {redact(str(current))[:300]}")
        current = current.__cause__ or current.__context__
    return chain


def last_frame(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "<无栈帧>"
    frame = frames[-1]
    return f"{Path(frame.filename).name}:{frame.lineno} in {frame.name} -> {redact(frame.line or '')[:160]}"


async def reproduce_llm_call(root: Path) -> None:
    """完全复刻 coordinator 的调用方式，定位真正的异常。"""
    section("6. 复现 Leader 的模型调用（真实异常类型）")
    sys.path.insert(0, str(root / "backend"))
    os.chdir(root / "backend")
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "backend" / ".env", override=False)

    try:
        import httpx
        import httpcore

        print(f"httpx={httpx.__version__}  httpcore={httpcore.__version__}")
    except Exception as exc:  # 版本读取失败不影响后续
        print(f"读取 httpx 版本失败: {type(exc).__name__}")

    # 6a：直接编码请求头，看是否与密钥有关
    print("\n[6a] 只编码 Authorization 头（不发网络请求）")
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    try:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        encoded = str(headers).encode("ascii")
        print(f"  通过：头可编码为 ASCII（{len(encoded)} 字节）")
    except UnicodeEncodeError as exc:
        print(f"  失败：UnicodeEncodeError {exc.start}-{exc.end} 位置")
    except Exception as exc:
        print(f"  失败：{type(exc).__name__}: {redact(str(exc))[:200]}")

    # 6b：走项目自己的 DeepSeekClient，与生产路径完全一致
    print("\n[6b] 用项目 DeepSeekClient 发一次最小请求（与 Leader 同一条路径）")
    try:
        from app.integrations.deepseek import DeepSeekClient
        from app.integrations.settings import IntegrationSettings

        settings = IntegrationSettings.from_environment()
        client = DeepSeekClient(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
        try:
            result = await client.chat_completion(
                [{"role": "user", "content": "ping"}], max_tokens=4
            )
            choice = (result.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content", "")
            print(f"  成功：模型={settings.deepseek_model} 返回长度={len(content)}")
        finally:
            await client.aclose()
    except Exception as exc:
        print("  失败，异常链：")
        for line in exception_chain(exc):
            print("    - " + line)
        print("  最内层位置：" + last_frame(exc))

    # 6c：带 tools 的调用（coordinator 实际是带 tools 发的，体量更大）
    print("\n[6c] 带 tools 参数再发一次（贴近 coordinator 实际请求）")
    try:
        from app.integrations.deepseek import DeepSeekClient
        from app.integrations.settings import IntegrationSettings

        settings = IntegrationSettings.from_environment()
        client = DeepSeekClient(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
        tools = [{
            "type": "function",
            "function": {
                "name": "tracking__list_real_work_orders",
                "description": "[tracking智能体] 只读列出 MES 当前生产工单",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        try:
            result = await client.chat_completion(
                [{"role": "user", "content": "现在有哪些 MES 工单"}],
                tools=tools, max_tokens=8,
            )
            choice = (result.get("choices") or [{}])[0]
            calls = (choice.get("message") or {}).get("tool_calls") or []
            print(f"  成功：tool_calls={len(calls)}")
        finally:
            await client.aclose()
    except Exception as exc:
        print("  失败，异常链：")
        for line in exception_chain(exc):
            print("    - " + line)
        print("  最内层位置：" + last_frame(exc))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/ubuntu/apps/auto-parts-agents")
    parser.add_argument("--lines", type=int, default=60)
    parser.add_argument("--skip-live", action="store_true", help="跳过真实模型调用")
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
    print("文件中的密钥: " + (str(key_facts(file_key)) if file_key else "未读到"))
    print("LLM_MODEL: " + str(values.get("LLM_MODEL", "<未设置>")))
    print("DEEPSEEK_BASE_URL: " + str(values.get("DEEPSEEK_BASE_URL", "<未设置>")))
    if env_path.is_file():
        print(".env 修改时间: " + datetime.fromtimestamp(env_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))

    section("3. 运行中的接入进程")
    # 进程环境在 sudo 或不同用户下不可读；先提示，避免误判成"读不到 = 没问题"
    geteuid = getattr(os, "geteuid", None)  # Windows 上不存在，仅 Linux 服务器需要
    if geteuid is not None and geteuid() != 0:
        print(f"当前用户 uid={geteuid()}（非 root）；若进程属于其他用户，/proc/<pid>/environ 会读不到")
    processes = wutong_processes()
    if not processes:
        print("没有找到 wutong_runtime 进程（接入服务未运行）")
    for item in processes:
        print(f"  {item['role']:<16} pid={item['pid']:<7} 用户={item.get('user', '?'):<8} 启动于 {item['started']}")
        print(f"      配置: {item.get('config') or '<未解析到>'}")
        for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "LLM_MODEL", "APP_ADAPTER_MODE"):
            value, state = process_env(item["pid"], name)
            if value is None:
                print(f"      {name}: {state}")
            elif name == "DEEPSEEK_API_KEY":
                print(f"      {name}: {key_facts(value)} 与 .env 一致={value == file_key}（{state}）")
            else:
                expected = values.get(name)
                flag = "" if expected is None or value == expected else f"  <<< 与 .env({expected}) 不一致"
                print(f"      {name}: {value}{flag}（{state}）")
        # 关键判据：进程启动时间 vs .env 修改时间
        if item["start_dt"] and env_path.is_file():
            delta = env_path.stat().st_mtime - item["start_dt"].timestamp()
            if delta > 0:
                print(f"      >>> .env 比该进程晚 {int(delta)} 秒被修改，进程内仍是用启动时的值")
            else:
                print(f"      >>> .env 早于该进程启动 {int(-delta)} 秒，进程加载的就是当前 .env")

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

    if not args.skip_live:
        try:
            asyncio.run(reproduce_llm_call(root))
        except Exception as exc:
            print(f"复现过程本身失败: {type(exc).__name__}: {redact(str(exc))[:200]}")

    section("7. 怎么读这份输出")
    print("- 第 3 节出现「与 .env 一致=False」或「.env 在进程启动之后被修改过」→ 重启接入进程。")
    print("- 第 6b/6c 的异常链第一行就是真实根因（例如 httpx.LocalProtocolError / UnicodeEncodeError / 401）。")
    print("- 第 6b 成功但叮当仍失败 → 问题在工具调用或 ERP/MES，不在模型配置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
