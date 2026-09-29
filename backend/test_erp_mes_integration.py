"""ERP/MES 集成测试：验证适配器工厂和 API 端点。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8001/api"

passed = 0
failed = 0

def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  ✅ {name}")
        passed += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        failed += 1

def api_get(path):
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================================
print("📋 测试 1: 集成状态 + 适配器模式")
# ============================================================

def test_adapter_status():
    r = api_get("/integrations/status")
    assert "adapters" in r, "缺少 adapters 字段"
    adapters = r["adapters"]
    assert "mode" in adapters
    assert "erp" in adapters
    assert "mes" in adapters
    # 未配置的情况下应该是 mock 模式
    assert adapters["erp"]["active_mode"] == "mock", f"ERP 模式应为 mock，实际: {adapters['erp']['active_mode']}"
    assert adapters["mes"]["active_mode"] == "mock", f"MES 模式应为 mock，实际: {adapters['mes']['active_mode']}"

test("集成状态含适配器信息", test_adapter_status)


# ============================================================
print("\n📋 测试 2: ERP 适配器 API（Mock 模式）")
# ============================================================

def test_erp_customer():
    r = api_get("/erp/customers/DEMO-CUSTOMER-001")
    assert r["found"] == True
    assert "MockERP" in r.get("authority", "")

test("ERP 客户查询 (Mock)", test_erp_customer)

def test_erp_item():
    r = api_get("/erp/items/DEMO-AL-102")
    assert r["found"] == True
    assert "MockERP" in r.get("authority", "")

test("ERP 物料查询 (Mock)", test_erp_item)

def test_erp_prices():
    r = api_get("/erp/items/DEMO-AL-102/prices")
    assert isinstance(r, list)
    assert len(r) > 0
    assert r[0].get("authority") == "MockERP"

test("ERP 价格查询 (Mock)", test_erp_prices)

def test_erp_bom():
    r = api_get("/erp/items/DEMO-BRACKET-001/bom")
    assert r["found"] == True
    assert len(r.get("items", [])) > 0
    assert r["authority"] == "MockERP"

test("ERP BOM 查询 (Mock)", test_erp_bom)

def test_erp_inventory():
    r = api_get("/erp/inventory?item_codes=DEMO-AL-102")
    assert isinstance(r, list)
    assert len(r) > 0
    assert r[0]["authority"] == "MockERP"

test("ERP 库存查询 (Mock)", test_erp_inventory)


# ============================================================
print("\n📋 测试 3: MES 适配器 API（Mock 模式）")
# ============================================================

def test_mes_work_orders():
    r = api_get("/mes/work-orders")
    assert isinstance(r, list)
    assert len(r) > 0
    assert r[0].get("authority") == "MockMES"

test("MES 工单列表 (Mock)", test_mes_work_orders)

def test_mes_operation_progress():
    # 先获取一个工单 ID
    orders = api_get("/mes/work-orders")
    if not orders:
        raise AssertionError("没有工单数据")
    wo_id = orders[0].get("work_order_id")
    r = api_get(f"/mes/work-orders/{wo_id}/progress")
    assert isinstance(r, list)
    # Mock 模式可能有工序数据
    assert r[0].get("authority") == "MockMES" if r else True

test("MES 工序进度 (Mock)", test_mes_operation_progress)

def test_mes_quality_records():
    r = api_get("/mes/quality-records?work_order_id=WO-DEMO-001")
    assert isinstance(r, list)
    assert len(r) > 0
    assert r[0].get("authority") == "MockMES"

test("MES 质量记录 (Mock)", test_mes_quality_records)

def test_mes_production_documents():
    r = api_get("/mes/production-documents?work_order_id=WO-DEMO-001")
    assert isinstance(r, list)
    assert len(r) > 0
    assert r[0].get("authority") == "MockMES"

test("MES 生产文档 (Mock)", test_mes_production_documents)


# ============================================================
print("\n📋 测试 4: 4 场景回归（确保 Mock 模式正常）")
# ============================================================

def api_post(path, body, ik):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json", "Idempotency-Key": ik},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

import uuid
for scenario in ["normal_order", "material_shortage", "quality_hold", "expedite"]:
    def _test(s=scenario):
        ik = f"erp-mes-test-{s}-{uuid.uuid4()}"
        r = api_post(f"/scenarios/{s}/run", {"seed": hash(s) % 100000}, ik)
        assert "project_id" in r
        assert r["project_id"].startswith("PRJ-")
    test(f"场景回归: {scenario}", _test)


# ============================================================
# 汇总
# ============================================================
print(f"\n{'='*60}")
print(f"  ERP/MES 集成测试：✅ {passed} 通过，❌ {failed} 失败")
print(f"{'='*60}")

if failed > 0:
    exit(1)
