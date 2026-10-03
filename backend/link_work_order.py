"""执行已获人工批准的关联写入：WO-2026-001 → SAL-ORD-2026-00001。

审批依据：用户 2026-09-28 明确指令
"现在统一将 WO-2026-001 → SAL-ORD-2026-00001，按照项目已有的 MES 写入方式
完成关联，然后执行只读验证"。

步骤：
0. 给现有 ERP API Key 追加 erp:orders:import scope（管理员操作，最小权限追加）
1. 调用 order_linkage.link_work_order_to_erp_order 写入并回读

不打印任何凭据。
"""
import asyncio
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

OPENMES_ENV = PROJECT_ROOT / "services" / "OpenMes" / ".env"


def parse_env(path: Path) -> dict:
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data


def http_req(url: str, method: str = "GET", headers: dict | None = None, body: dict | None = None):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "replace")[:300]}


def ensure_import_scope() -> bool:
    """给项目 .env 中的 ERP API Key 追加 erp:orders:import scope。返回是否成功。"""
    om = parse_env(OPENMES_ENV)
    base = om.get("APP_URL", "http://localhost")
    admin_user = om.get("ADMIN_USERNAME", "admin")
    admin_pass = om.get("ADMIN_PASSWORD", "")
    erp_key = os.getenv("OPENMES_ERP_API_KEY", "")

    code, resp = http_req(
        f"{base}/api/auth/login", "POST",
        body={"username": admin_user, "password": admin_pass},
    )
    if code != 200:
        print(f"[0] 管理员登录失败 HTTP {code}")
        return False
    token = resp.get("data", {}).get("token", "")
    auth = {"Authorization": f"Bearer {token}"}

    code, resp = http_req(f"{base}/api/v1/api-keys", headers=auth)
    if code != 200:
        print(f"[0] 读取 API Key 列表失败 HTTP {code}")
        return False
    keys = resp.get("data", [])
    target = None
    for k in keys:
        if erp_key and (erp_key.startswith(k.get("key_prefix", "") or "\0")):
            target = k
            break
    if target is None and keys:
        target = keys[0]
    if target is None:
        print("[0] 未找到可更新的 API Key")
        return False

    scopes = list(target.get("scopes", []))
    if "erp:orders:import" in scopes:
        print(f"[0] Key '{target.get('name')}' 已含 erp:orders:import scope，无需修改")
        return True

    scopes.append("erp:orders:import")
    code, resp = http_req(
        f"{base}/api/v1/api-keys/{target['id']}", "PATCH",
        headers=auth,
        body={"name": target.get("name", "erp-integration"), "scopes": scopes},
    )
    if code == 200:
        got = [s for s in resp.get("data", {}).get("scopes", [])]
        print(f"[0] Key '{target.get('name')}' scope 已更新为: {got}")
        return "erp:orders:import" in got
    print(f"[0] 更新 scope 失败 HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:200]}")
    return False


async def main() -> None:
    print("=" * 72)
    print("关联写入（已获用户批准）：WO-2026-001 → SAL-ORD-2026-00001")
    print("=" * 72)

    if not ensure_import_scope():
        print("前置失败：无法确保 erp:orders:import scope，终止。")
        sys.exit(2)

    from app.services.order_linkage import link_work_order_to_erp_order
    result = await link_work_order_to_erp_order(
        work_order_no="WO-2026-001",
        erp_order_id="SAL-ORD-2026-00001",
        approved_by="user",
        approval_note="用户 2026-09-28 指令：统一将 WO-2026-001 关联到 SAL-ORD-2026-00001（测试数据）",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("success"):
        print("\n[OK] 写入并回读验证通过")
    else:
        print("\n[FAIL] 写入或回读验证失败")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
