"""Seed OpenMES work orders and quality issues via user API."""
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
    print(f"    OK")

    # Get product types
    print("[2] Getting product types ...")
    code, pts = api_call(f"{base_url}/api/v1/product-types?per_page=20", headers=bearer)
    if code != 200:
        print(f"    FAILED: {code}")
        return
    pt_map = {}
    for pt in pts.get("data", []):
        pt_map[pt["code"]] = pt["id"]
        print(f"    {pt['code']} -> id={pt['id']} name={pt['name']}")

    # Get lines
    print("\n[3] Getting lines ...")
    code, lines = api_call(f"{base_url}/api/v1/lines", headers=bearer)
    line_id = None
    if code == 200:
        for ln in lines.get("data", []):
            print(f"    id={ln['id']} code={ln['code']} name={ln['name']}")
            if ln["code"] == "DEMO_LINE_01":
                line_id = ln["id"]

    # Get issue types
    print("\n[4] Getting issue types ...")
    code, itypes = api_call(f"{base_url}/api/v1/issue-types", headers=bearer)
    issue_type_map = {}
    if code == 200:
        for it in itypes.get("data", []):
            issue_type_map[it["name"]] = it["id"]
            print(f"    id={it['id']} name={it['name']} critical={it.get('is_critical','')}")

    # Create work orders via user API
    print("\n[5] Creating work orders via user API ...")
    work_orders = [
        {
            "order_no": "WO-2026-001",
            "product_type_id": pt_map.get("BD-2401"),
            "planned_qty": 500,
            "line_id": line_id,
            "priority": 5,
            "due_date": "2026-10-15",
            "product_name": "制动盘-前轮 Brake Disc Front"
        },
        {
            "order_no": "WO-2026-002",
            "product_type_id": pt_map.get("SK-3401"),
            "planned_qty": 300,
            "line_id": line_id,
            "priority": 3,
            "due_date": "2026-10-20",
            "product_name": "转向节 Steering Knuckle"
        },
        {
            "order_no": "WO-2026-003",
            "product_type_id": pt_map.get("TS-4501"),
            "planned_qty": 200,
            "line_id": line_id,
            "priority": 7,
            "due_date": "2026-10-25",
            "product_name": "传动轴总成 Transmission Shaft Assembly"
        }
    ]

    wo_ids = []
    for wo in work_orders:
        code, resp = api_call(f"{base_url}/api/v1/work-orders", "POST", wo, bearer)
        if code in (200, 201):
            wo_id = resp.get("data", {}).get("id")
            wo_ids.append(wo_id)
            print(f"    OK: {wo['order_no']} -> id={wo_id} status={resp.get('data',{}).get('status','')}")
        else:
            print(f"    FAILED: {wo['order_no']} -> HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:300]}")

    # Create quality issue for WO-2026-001
    print("\n[6] Creating quality issue ...")
    if wo_ids:
        # Use "Measurement / Dimension Error" issue type
        dim_type_id = issue_type_map.get("Measurement / Dimension Error")
        if dim_type_id and wo_ids[0]:
            issue_body = {
                "work_order_id": wo_ids[0],
                "issue_type_id": dim_type_id,
                "description": "制动盘外径尺寸超差0.05mm,超出公差带。Brake disc outer diameter deviation 0.05mm beyond tolerance."
            }
            code, resp = api_call(f"{base_url}/api/v1/issues", "POST", issue_body, bearer)
            if code in (200, 201):
                print(f"    OK: issue id={resp.get('data',{}).get('id','')}")
            else:
                print(f"    FAILED: HTTP {code}: {json.dumps(resp, ensure_ascii=False)[:300]}")
        else:
            print(f"    Skipped (missing type_id={dim_type_id} or wo_id={wo_ids[0] if wo_ids else None})")

    # Verify
    print("\n[7] Verification ...")
    code, wos = api_call(f"{base_url}/api/v1/work-orders?per_page=20", headers=bearer)
    if code == 200:
        print(f"    Work orders: {len(wos.get('data', []))}")
        for wo in wos.get("data", []):
            pt_name = wo.get("product_type", {}).get("name", wo.get("product_name", ""))
            print(f"      id={wo.get('id')} order_no={wo.get('order_no')} status={wo.get('status')} qty={wo.get('quantity','')} product={pt_name}")

    code, issues = api_call(f"{base_url}/api/v1/issues?per_page=20", headers=bearer)
    if code == 200:
        print(f"    Issues: {len(issues.get('data', []))}")
        for iss in issues.get("data", []):
            print(f"      id={iss.get('id')} status={iss.get('status')} desc={iss.get('description','')[:60]}")

    print("\nDone.")

if __name__ == "__main__":
    main()
