"""调查失败的 API 响应格式。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8001/api"

def get(path):
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode("utf-8")}

# 1. 项目快照格式
projects = get("/projects")
pid = projects[0]["project_id"]
print(f"=== 项目快照 ({pid}) ===")
snap = get(f"/projects/{pid}/snapshot")
print(f"顶层 keys: {list(snap.keys())}")
if "timeline" in snap:
    print(f"timeline 数量: {len(snap['timeline'])}")
    if snap["timeline"]:
        print(f"首个事件 keys: {list(snap['timeline'][0].keys())}")
if "cases" in snap:
    print(f"cases 数量: {len(snap['cases'])}")
print()

# 2. 审批列表 + 审批请求格式
approvals = get("/approvals?status=pending")
print(f"=== 审批列表 ({len(approvals)} 项) ===")
if approvals:
    a = approvals[0]
    print(f"审批 keys: {list(a.keys())}")
    print(f"action_type: {a.get('action_type')}")
    print(f"approval_id: {a.get('approval_id')}")
    # 尝试审批
    print("\n尝试审批 (缺少 Idempotency-Key):")
    r = post(f"/approvals/{a['approval_id']}/approve", {"decision": "approve"})
    print(f"  结果: {r}")
    
    print("\n尝试审批 (有 Idempotency-Key, 空 body):")
    r = post(f"/approvals/{a['approval_id']}/approve", {}, {"Idempotency-Key": "test-investigate-1"})
    print(f"  结果: {r}")
print()

# 3. RFQ 分析
print("=== RFQ 分析 ===")
r = post("/quotation/analyze-rfq", {"description": "test 100 件", "use_llm": False})
print(f"结果: {r}")
print()

# 4. ACS 文件格式
import os
acs_dir = r"e:\competition\汽车零部件工厂智能体开发\backend\acs"
fname = "quotation_acs.json"
fpath = os.path.join(acs_dir, fname)
with open(fpath, "r", encoding="utf-8") as f:
    data = json.load(f)
print(f"=== ACS 文件 {fname} ===")
print(f"顶层 keys: {list(data.keys())}")
print(f"skills 数量: {len(data.get('skills', []))}")
# 检查嵌套
if "capabilities" in data:
    print(f"capabilities keys: {list(data['capabilities'].keys())}")
