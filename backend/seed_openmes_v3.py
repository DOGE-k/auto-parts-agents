"""Accept work orders, start production, and create quality issue with title."""
import json
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENMES_ENV = PROJECT_ROOT / "services" / "OpenMes" / ".env"

def parse_env(path):
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data

def api_call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except:
            return e.code, {"raw": raw[:500]}
    except Exception as e:
        return 0, {"error": str(e)}

def main():
    om_env = parse_env(OPENMES_ENV)
    base_url = om_env.get("APP_URL", "http://localhost")
    admin_user = om_env.get("ADMIN_USERNAME", "admin")
    admin_pass = om_env.get("ADMIN_PASSWORD", "")

    print("[1] Logging in ...")
    code, data = api_call(f"{base_url}/api/auth/login", "POST", {
        "username": admin_user, "password": admin_pass
    })
    if code != 200:
        print(f"    FAILED: {code}")
        return
    token = data["data"]["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    print("    OK")

    # Accept WO-2026-001 (id=2)
    print("\n[2] Accepting work orders ...")
    for wo_id, wo_no in [(2, "WO-2026-001"), (3, "WO-2026-002"), (4, "WO-2026-003")]:
        code, resp = api_call(f"{base_url}/api/v1/work-orders/{wo_id}/accept", "POST", {}, bearer)
        if code in (200, 201):
            status = resp.get("data", {}).get("status", "")
            print(f"    OK: {wo_no} (id={wo_id}) -> {status}")
        else:
            print(f"    {wo_no}: HTTP {code} - {json.dumps(resp, ensure_ascii=False)[:200]}")

    # Start WO-2026-001 (change to in_progress)
    print("\n[3] Starting WO-2026-001 ...")
    code, resp = api_call(f"{base_url}/api/v1/work-orders/2/start", "POST", {}, bearer)
    if code in (200, 201):
        status = resp.get("data", {}).get("status", "")
        print(f"    OK: WO-2026-001 -> {status}")
    else:
        print(f"    HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:300]}")

    # Create quality issue with title
    print("\n[4] Creating quality issue ...")
    issue_body = {
        "work_order_id": 2,
        "issue_type_id": 7,  # Measurement / Dimension Error
        "title": "制动盘外径尺寸超差",
        "description": "制动盘外径尺寸超差0.05mm，超出公差带0.02mm。需返修后重新检验。Brake disc outer diameter deviation 0.05mm beyond tolerance."
    }
    code, resp = api_call(f"{base_url}/api/v1/issues", "POST", issue_body, bearer)
    if code in (200, 201):
        issue_id = resp.get("data", {}).get("id")
        print(f"    OK: issue id={issue_id}")
    else:
        print(f"    FAILED: HTTP {code} - {json.dumps(resp, ensure_ascii=False)[:300]}")

    # Create material shortage issue for WO-2026-002
    print("\n[5] Creating material shortage issue for WO-2026-002 ...")
    shortage_body = {
        "work_order_id": 3,
        "issue_type_id": 2,  # Material Shortage
        "title": "铸铁毛坯库存不足",
        "description": "转向节生产所需铸铁毛坯库存不足，预计缺口50件。Cast iron blank shortage for steering knuckle production, estimated gap: 50 units."
    }
    code, resp = api_call(f"{base_url}/api/v1/issues", "POST", shortage_body, bearer)
    if code in (200, 201):
        print(f"    OK: issue id={resp.get('data',{}).get('id')}")
    else:
        print(f"    FAILED: HTTP {code} - {json.dumps(resp, ensure_ascii=False)[:300]}")

    # Verify
    print("\n[6] Verification ...")
    code, wos = api_call(f"{base_url}/api/v1/work-orders?per_page=20", headers=bearer)
    if code == 200:
        print(f"    Work orders: {len(wos.get('data', []))}")
        for wo in wos.get("data", []):
            pt = wo.get("product_type", {})
            print(f"      id={wo.get('id')} order_no={wo.get('order_no')} status={wo.get('status')} planned={wo.get('planned_qty','')} produced={wo.get('produced_qty','')} product={pt.get('name',wo.get('product_name',''))}")

    code, issues = api_call(f"{base_url}/api/v1/issues?per_page=20", headers=bearer)
    if code == 200:
        print(f"    Issues: {len(issues.get('data', []))}")
        for iss in issues.get("data", []):
            print(f"      id={iss.get('id')} title={iss.get('title','')} status={iss.get('status')} wo={iss.get('work_order_id')}")

    # Check ERP API endpoints
    proj_env = parse_env(PROJECT_ROOT / ".env")
    erp_key = proj_env.get("OPENMES_ERP_API_KEY", "")
    erp_headers = {"X-Api-Key": erp_key}

    code, completions = api_call(f"{base_url}/api/v1/erp/production/completions", headers=erp_headers)
    print(f"\n    ERP completions: HTTP {code}, {len(completions.get('data', []))} records")

    code, qissues = api_call(f"{base_url}/api/v1/erp/quality/issues", headers=erp_headers)
    print(f"    ERP quality issues: HTTP {code}, {len(qissues.get('data', []))} records")
    if code == 200:
        for qi in qissues.get("data", [])[:5]:
            print(f"      id={qi.get('id')} type={qi.get('issue_type','')} title={qi.get('title','')}")

    print("\nDone.")

if __name__ == "__main__":
    main()
