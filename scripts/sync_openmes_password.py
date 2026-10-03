"""交互式同步 OpenMES admin 密码到 services/OpenMes/.env。

用途：在 OpenMES 界面改完密码后运行本脚本，把新密码同步给项目
（种子脚本与 get_openmes_token.py 都从该 .env 读管理员凭据）。

- 密码输入不回显（getpass），不出现在聊天/终端记录里；
- 先用新密码真实登录验证，成功才写入 .env（验证失败不落盘）；
- OpenMES 需在运行（默认 http://127.0.0.1）。

用法：python scripts/sync_openmes_password.py
"""
import getpass
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    pw = getpass.getpass("输入 OpenMES admin 新密码（输入不回显）: ").strip()
    if not pw:
        print("未输入密码，退出。")
        return 1

    body = json.dumps({"username": "admin", "password": pw}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1/api/auth/login", data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.load(resp)
            if not (d.get("data") or {}).get("token"):
                print("登录未返回 token——密码可能不正确。未写入 .env。")
                return 1
    except urllib.error.HTTPError as e:
        print(f"登录失败 HTTP {e.code}——密码不正确或 OpenMES 未运行。未写入 .env。")
        return 1
    except Exception as e:  # 连接失败等
        print(f"无法连接 OpenMES：{e}。未写入 .env。")
        return 1

    envp = ROOT / "services" / "OpenMes" / ".env"
    if not envp.exists():
        print(f"未找到 {envp}，请确认仓库结构。")
        return 1
    lines = envp.read_text(encoding="utf-8-sig").splitlines()
    new, found = [], False
    for line in lines:
        if line.strip().startswith("ADMIN_PASSWORD="):
            new.append("ADMIN_PASSWORD=" + pw)
            found = True
        else:
            new.append(line)
    if not found:
        new.append("ADMIN_PASSWORD=" + pw)
    envp.write_text("\n".join(new) + "\n", encoding="utf-8")
    print("登录验证通过，已写入 services/OpenMes/.env 的 ADMIN_PASSWORD。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
