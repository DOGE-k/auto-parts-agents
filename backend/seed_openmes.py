"""Seed OpenMES with realistic auto-parts factory data via API and database."""
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
    erp_key = None
    proj_env = parse_env(PROJECT_ROOT / ".env")
    erp_key = proj_env.get("OPENMES_ERP_API_KEY", "")

    # Login to get Bearer token
    print("[1] Logging in to OpenMES ...")
    code, data = api_call(f"{base_url}/api/auth/login", "POST", {
        "username": admin_user, "password": admin_pass
    })
    if code != 200:
        print(f"    FAILED: {code}")
        return
    token = data["data"]["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    erp_headers = {"X-Api-Key": erp_key}
    print(f"    OK - token: {token[:8]}...")

    # Step 2: Add erp:masterdata:write scope to API key
    print("[2] Updating API key scopes ...")
    code, resp = api_call(
        f"{base_url}/api/v1/api-keys/1", "PATCH",
        {"name": "DEMO_AGENT_READONLY", "scopes": [
            "erp:production:read", "erp:quality:read",
            "erp:orders:import", "erp:masterdata:write"
        ]},
        bearer
    )
    if code == 200:
        print(f"    OK - scopes updated")
    else:
        print(f"    WARNING: {code} - {json.dumps(resp, ensure_ascii=False)[:200]}")

    # Step 3: Create product types via ERP import
    print("[3] Importing product types (auto parts) ...")
    products = {
        "external_system": "erpnext",
        "only_categories": ["FINISHED"],
        "products": [
            {"code": "BD-2401", "name": "制动盘-前轮 Brake Disc Front", "category": "FINISHED", "unit_of_measure": "pcs"},
            {"code": "BD-2402", "name": "制动盘-后轮 Brake Disc Rear", "category": "FINISHED", "unit_of_measure": "pcs"},
            {"code": "SK-3401", "name": "转向节 Steering Knuckle", "category": "FINISHED", "unit_of_measure": "pcs"},
            {"code": "TS-4501", "name": "传动轴总成 Transmission Shaft Assembly", "category": "FINISHED", "unit_of_measure": "pcs"},
            {"code": "CP-5601", "name": "离合器片 Clutch Plate", "category": "FINISHED", "unit_of_measure": "pcs"},
        ]
    }
    code, resp = api_call(
        f"{base_url}/api/v1/erp/products/import", "POST", products, erp_headers
    )
    if code in (200, 207):
        d = resp.get("data", resp)
        print(f"    OK - imported={d.get('imported',0)} updated={d.get('updated',0)} skipped={d.get('skipped',0)}")
        if d.get("errors"):
            print(f"    Errors: {json.dumps(d['errors'], ensure_ascii=False)[:200]}")
    else:
        print(f"    FAILED: {code} - {json.dumps(resp, ensure_ascii=False)[:300]}")

    # Step 4: Create work orders via ERP import
    print("[4] Importing work orders ...")
    orders = {
        "strategy": "update_or_create",
        "orders": [
            {
                "order_no": "WO-2026-001",
                "line_code": "DEMO_LINE_01",
                "product_type_code": "BD-2401",
                "planned_qty": 500,
                "due_date": "2026-10-15",
                "customer_order_no": "SO-2026-001",
                "priority": 5
            },
            {
                "order_no": "WO-2026-002",
                "line_code": "DEMO_LINE_01",
                "product_type_code": "SK-3401",
                "planned_qty": 300,
                "due_date": "2026-10-20",
                "customer_order_no": "SO-2026-002",
                "priority": 3
            },
            {
                "order_no": "WO-2026-003",
                "line_code": "DEMO_LINE_01",
                "product_type_code": "TS-4501",
                "planned_qty": 200,
                "due_date": "2026-10-25",
                "customer_order_no": "SO-2026-003",
                "priority": 7
            }
        ]
    }
    code, resp = api_call(
        f"{base_url}/api/v1/erp/work-orders/import", "POST", orders, erp_headers
    )
    if code in (200, 207):
        d = resp.get("data", resp)
        print(f"    OK - imported={d.get('imported',0)} updated={d.get('updated',0)} skipped={d.get('skipped',0)}")
        if d.get("errors"):
            print(f"    Errors: {json.dumps(d['errors'], ensure_ascii=False)[:200]}")
    else:
        print(f"    FAILED: {code} - {json.dumps(resp, ensure_ascii=False)[:300]}")

    # Step 5: Create quality issue for WO-2026-001 via user API
    print("[5] Creating quality issue ...")
    # First get issue types
    code, types = api_call(f"{base_url}/api/v1/issue-types", headers=bearer)
    issue_type_id = None
    if code == 200 and types.get("data"):
        issue_type_id = types["data"][0].get("id")
        print(f"    Issue type: {types['data'][0].get('name','')} (id={issue_type_id})")
    else:
        print(f"    No issue types found, trying to create issue without type_id")

    # Get the work order ID for WO-2026-001
    code, wos = api_call(f"{base_url}/api/v1/work-orders?search=WO-2026-001", headers=bearer)
    wo_id = None
    if code == 200:
        for wo in wos.get("data", []):
            if wo.get("order_no") == "WO-2026-001":
                wo_id = wo.get("id")
                break
    if wo_id:
        print(f"    Work order WO-2026-001 found: id={wo_id}")
    else:
        print(f"    WARNING: WO-2026-001 not found in work orders list")

    if wo_id and issue_type_id:
        issue_body = {
            "work_order_id": wo_id,
            "issue_type_id": issue_type_id,
            "description": "制动盘外径尺寸超差 0.05mm，需返修。Dimensional deviation on brake disc outer diameter: 0.05mm over tolerance, rework required."
        }
        code, resp = api_call(f"{base_url}/api/v1/issues", "POST", issue_body, bearer)
        if code in (200, 201):
            print(f"    OK - issue created: id={resp.get('data',{}).get('id','')}")
        else:
            print(f"    FAILED: {code} - {json.dumps(resp, ensure_ascii=False)[:300]}")
    else:
        print("    Skipped quality issue creation (missing work_order_id or issue_type_id)")

    # Step 6: Verify data
    print("\n[6] Verification ...")
    code, wos = api_call(f"{base_url}/api/v1/work-orders?per_page=20", headers=bearer)
    if code == 200:
        print(f"    Work orders: {len(wos.get('data', []))}")
        for wo in wos.get("data", []):
            print(f"      id={wo.get('id')} order_no={wo.get('order_no')} status={wo.get('status')} qty={wo.get('quantity')}")

    code, issues = api_call(f"{base_url}/api/v1/issues?per_page=20", headers=bearer)
    if code == 200:
        print(f"    Issues: {len(issues.get('data', []))}")
        for iss in issues.get("data", []):
            print(f"      id={iss.get('id')} status={iss.get('status')} desc={iss.get('description','')[:50]}")

    # Check ERP API endpoints
    code, completions = api_call(
        f"{base_url}/api/v1/erp/production/completions", headers=erp_headers
    )
    print(f"    ERP completions: HTTP {code}, {len(completions.get('data', []))} records")

    code, qissues = api_call(
        f"{base_url}/api/v1/erp/quality/issues", headers=erp_headers
    )
    print(f"    ERP quality issues: HTTP {code}, {len(qissues.get('data', []))} records")

    print("\nDone. OpenMES data seeded.")

if __name__ == "__main__":
    main()
