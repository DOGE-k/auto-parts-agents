"""只读诊断：检查服务器上生效的 DEEPSEEK_API_KEY 是否可用（不打印密钥本身）。

用法（在服务器上，项目根目录）：
    .venv/bin/python diagnose_deepseek_key.py
    .venv/bin/python diagnose_deepseek_key.py --live     # 额外发一次最小请求验证密钥真的可用

判定依据：
- 非 ASCII：httpx 编码 Authorization 头会抛 UnicodeEncodeError，
  AIP 技能被统一包装成「技能执行失败，请检查服务日志」。
- 首尾空白/引号/BOM：常见于从文档或聊天窗口复制，密钥实际值会被污染。
输出只含长度、位置和布尔值，不含任何密钥字符。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def load_env(root: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("缺少 python-dotenv，请使用项目 .venv 运行")
        raise SystemExit(2)
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "backend" / ".env", override=False)


def inspect_key(key: str) -> dict:
    non_ascii = [i for i, ch in enumerate(key) if ord(ch) > 127]
    return {
        "present": bool(key),
        "length": len(key),
        "is_ascii": key.isascii(),
        "non_ascii_count": len(non_ascii),
        "first_non_ascii_position": non_ascii[0] if non_ascii else None,
        "leading_or_trailing_space": key != key.strip(),
        "starts_with_sk_dash": key.startswith("sk-"),
        "has_inner_space": " " in key.strip(),
        "has_cr_or_lf": "\r" in key or "\n" in key,
        "has_bom": key.startswith("\ufeff"),
    }


async def live_check(key: str, base_url: str, model: str) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=30.0) as client:
        try:
            response = await client.post(
                "chat/completions",
                headers={"Authorization": f"Bearer {key.strip()}", "Accept": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
        except Exception as exc:  # 不打印请求头
            print(f"[live] 请求失败: {type(exc).__name__}: {str(exc)[:200]}")
            return
    print(f"[live] HTTP {response.status_code}")
    if response.status_code == 200:
        print("[live] 密钥可用，模型可调用")
        return
    try:
        payload = response.json()
    except ValueError:
        print(f"[live] 非 JSON 响应: {response.text[:200]}")
        return
    message = payload.get("message") or payload.get("error") or payload
    print(f"[live] 平台返回: {str(message)[:300]}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="发一次最小请求验证密钥可用性")
    parser.add_argument("--root", default=".", help="项目根目录")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / "backend" / "app").is_dir():
        print("参数须为 auto-parts-agents 项目根目录")
        return 2
    load_env(root)

    key = os.getenv("DEEPSEEK_API_KEY", "")
    info = inspect_key(key)
    for name, value in info.items():
        print(f"{name}: {value}")

    if not info["present"]:
        print("结论：没有读到 DEEPSEEK_API_KEY")
        return 1
    if not info["is_ascii"]:
        print("结论：密钥含非 ASCII 字符，AIP 技能必然报「技能执行失败，请检查服务日志」")
        print("      需要把 .env 里的 DEEPSEEK_API_KEY 换成纯 ASCII 的 sk-... 密钥后重启接入进程")
        return 1
    if info["leading_or_trailing_space"] or info["has_cr_or_lf"] or info["has_bom"]:
        print("结论：密钥含首尾空白/换行/BOM，程序会 strip 掉，通常不是主因，但建议清理")
    print("结论：密钥本身是 ASCII，编码问题可以排除；若仍失败请看 Leader 日志的实际异常")

    if args.live:
        await live_check(key, os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                         os.getenv("LLM_MODEL", "deepseek-flash"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
