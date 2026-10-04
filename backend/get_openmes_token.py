"""Configure OpenMES token and ERP API key in project .env, then verify."""
import json
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENMES_ENV = PROJECT_ROOT / "services" / "OpenMes" / ".env"
PROJECT_ENV = PROJECT_ROOT / ".env"

def parse_env(path):
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data

def api_get(url, headers=None):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if headers:
        req.headers.update(headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"error": str(e)}


def create_erp_api_key(base_url, token):
    """Create an ERP integration key and return its one-time plaintext value."""
    body = json.dumps({
        "name": "erp-integration",
        "scopes": [
            "erp:production:read",
            "erp:quality:read",
            "erp:orders:import",
            "erp:masterdata:write",
        ],
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/api/v1/api-keys",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"    HTTP {exc.code}: {exc.read().decode()[:300]}")
        return ""
    except Exception as exc:
        print(f"    FAILED: {exc}")
        return ""

    print(f"    Response: {json.dumps(payload, ensure_ascii=False)[:300]}")
    # The current OpenMES controller returns plaintext_key at the top level;
    # data is deliberately redacted after creation. Keep the nested fallback
    # for older builds that returned data.key.
    key = payload.get("plaintext_key", "") or payload.get("data", {}).get("key", "")
    if not key:
        print("    FAILED: API response did not include the one-time plaintext key")
        return ""
    print(f"    ERP API Key: {key[:8]}...")
    return key


def verify_erp_api_key(base_url, erp_key):
    """Return whether an existing key can access both required ERP exports."""
    print(f"[4] Verifying ERP API key ({erp_key[:8]}...) via /api/v1/erp/production/completions ...")
    code, completions = api_get(
        f"{base_url}/api/v1/erp/production/completions",
        {"X-Api-Key": erp_key},
    )
    if code == 200:
        print(f"    OK: {len(completions.get('data', []))} completions")
    else:
        print(f"    HTTP {code}: {json.dumps(completions, ensure_ascii=False)[:200]}")

    print("[5] Verifying ERP quality issues endpoint ...")
    code2, issues = api_get(
        f"{base_url}/api/v1/erp/quality/issues",
        {"X-Api-Key": erp_key},
    )
    if code2 == 200:
        print(f"    OK: {len(issues.get('data', []))} quality issues")
    else:
        print(f"    HTTP {code2}: {json.dumps(issues, ensure_ascii=False)[:200]}")
    return code == 200 and code2 == 200

def main():
    om_env = parse_env(OPENMES_ENV)
    admin_user = om_env.get("ADMIN_USERNAME", "admin")
    admin_pass = om_env.get("ADMIN_PASSWORD", "")
    base_url = om_env.get("APP_URL", "http://localhost")

    # Step 1: Login to get token
    print("[1] Logging in to get Bearer token ...")
    body = json.dumps({"username": admin_user, "password": admin_pass}).encode()
    req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        token = data.get("data", {}).get("token", "")
        if not token:
            print(f"    FAILED: no token in response")
            return
        print(f"    OK - token: {token[:8]}...")
    except Exception as e:
        print(f"    FAILED: {e}")
        return

    # Step 2: Get ERP API key from project .env (if exists)
    proj_env = parse_env(PROJECT_ENV) if PROJECT_ENV.exists() else {}
    erp_key = proj_env.get("OPENMES_ERP_API_KEY", "")

    # Step 3: Verify token
    print("[2] Verifying Bearer token via /api/auth/me ...")
    code, me = api_get(f"{base_url}/api/auth/me", {"Authorization": f"Bearer {token}"})
    if code == 200:
        print(f"    OK: {me.get('username','')} (account_type={me.get('account_type','')})")
    else:
        print(f"    FAILED: HTTP {code}")
        return

    print("[3] Verifying work orders endpoint ...")
    code, wos = api_get(f"{base_url}/api/v1/work-orders?per_page=5", {"Authorization": f"Bearer {token}"})
    if code == 200:
        print(f"    OK: {len(wos.get('data', []))} work orders")
    else:
        print(f"    FAILED: HTTP {code}")

    # Step 4: Verify the durable ERP key. A personal token from Settings →
    # API Tokens is a Bearer session token, not an X-Api-Key and will fail here.
    # If a stale/wrong key is already in .env, replace it with a new scoped key.
    if erp_key and not verify_erp_api_key(base_url, erp_key):
        print("    Existing ERP API key is invalid or lacks the required scopes; creating a new one")
        erp_key = ""
    if not erp_key:
        print("[4] No valid ERP API key found - will create one via API")
        print("    Creating ERP API key ...")
        erp_key = create_erp_api_key(base_url, token)
        if not erp_key:
            return

    # Step 5: Write to project .env
    print("\n[6] Writing OpenMES config to project .env ...")
    env_lines = []
    if PROJECT_ENV.exists():
        env_lines = PROJECT_ENV.read_text(encoding="utf-8").splitlines()

    updates = {
        "OPENMES_TOKEN": token,
        "OPENMES_BASE_URL": base_url,
    }
    if erp_key:
        updates["OPENMES_ERP_API_KEY"] = erp_key

    found_keys = set()
    new_lines = []
    for line in env_lines:
        stripped = line.strip()
        matched = False
        for key, val in updates.items():
            if stripped.startswith(f"{key}="):
                new_lines.append(f"{key}={val}")
                found_keys.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in found_keys:
            new_lines.append(f"{key}={val}")

    PROJECT_ENV.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"    Updated {PROJECT_ENV.name}")
    print(f"    OPENMES_TOKEN = {token[:8]}...")
    print(f"    OPENMES_BASE_URL = {base_url}")
    if erp_key:
        print(f"    OPENMES_ERP_API_KEY = {erp_key[:8]}...")

    print("\nDone. OpenMES token configured.")

if __name__ == "__main__":
    main()
