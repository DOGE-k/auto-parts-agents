"""深度验证：质量冻结和加急场景的时间线事件完整性。"""
import json
import urllib.request
import uuid

BASE = "http://127.0.0.1:8001/api"

def post(path, body, ik):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json", "Idempotency-Key": ik},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def get(path):
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

# 启动全新的质量冻结场景
print("=== 测试质量冻结场景 ===")
r = post("/scenarios/quality_hold/run", {"seed": 99999}, f"deep-audit-qh-{uuid.uuid4()}")
pid = r["project_id"]
print(f"项目: {pid}")

# 获取时间线
timeline = get(f"/projects/{pid}/timeline")
event_types = [e["event_type"] for e in timeline]
print(f"事件数: {len(timeline)}")
print(f"事件列表:")
for e in timeline:
    print(f"  - {e['event_type']}  (by {e.get('source_agent', 'system')})")

# 检查关键事件
expected_qh = ["RFQ_CREATED", "QUOTE_DRAFT_READY", "SALES_ORDER_RELEASED", 
               "MATERIAL_DEMAND_CREATED", "QUALITY_HOLD", "NCR_CREATED", 
               "DELIVERY_RISK"]
missing_qh = [e for e in expected_qh if e not in event_types]
if missing_qh:
    print(f"\n❌ 缺少事件: {missing_qh}")
else:
    print(f"\n✅ 所有关键事件齐全")

# 检查审批
approvals = get("/approvals?status=pending")
project_approvals = [a for a in approvals if a["project_id"] == pid]
print(f"\n待审批数: {len(project_approvals)}")
for a in project_approvals:
    print(f"  - {a['action_type']}: {a['approval_id']}")

ncr_approvals = [a for a in project_approvals if a["action_type"] == "ncr_disposition"]
if ncr_approvals:
    print("✅ 有 NCR 处置审批")
else:
    print("❌ 缺少 NCR 处置审批")


print("\n" + "="*60)
print("=== 测试加急场景 ===")
r = post("/scenarios/expedite/run", {"seed": 88888}, f"deep-audit-exp-{uuid.uuid4()}")
pid = r["project_id"]
print(f"项目: {pid}")

# 获取时间线
timeline = get(f"/projects/{pid}/timeline")
event_types = [e["event_type"] for e in timeline]
print(f"事件数: {len(timeline)}")
print(f"事件列表:")
for e in timeline:
    print(f"  - {e['event_type']}  (by {e.get('source_agent', 'system')})")

# 检查关键事件
expected_exp = ["RFQ_CREATED", "QUOTE_DRAFT_READY", "EXPEDITE_REQUESTED",
                 "COST_ASSESSMENT_READY", "EXPEDITE_OPTIONS_READY"]
# 可能事件名不完全一样，检查有加急相关的
has_expedite = any("EXPEDITE" in t for t in event_types)
has_eta = any("ETA" in t for t in event_types)
has_cost = any("COST" in t for t in event_types)
print(f"\n有加急事件: {has_expedite}")
print(f"有ETA事件: {has_eta}")
print(f"有成本评估事件: {has_cost}")

# 检查审批
approvals = get("/approvals?status=pending")
project_approvals = [a for a in approvals if a["project_id"] == pid]
print(f"\n待审批数: {len(project_approvals)}")
for a in project_approvals:
    print(f"  - {a['action_type']}: {a['approval_id']}")

exp_approvals = [a for a in project_approvals if "expedite" in a["action_type"]]
if exp_approvals:
    print("✅ 有加急方案选择审批")
else:
    print("❌ 缺少加急方案选择审批")
