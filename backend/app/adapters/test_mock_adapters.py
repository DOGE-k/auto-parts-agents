"""测试 Mock Adapter 完整性。"""
import asyncio
from app.adapters.erp.mock import MockERPAdapter, get_mock_erp
from app.adapters.mes.mock import MockMESAdapter, get_mock_mes


async def test_erp():
    print("=== MockERP ===")
    erp = MockERPAdapter()

    cust = await erp.get_customer("DEMO-CUSTOMER-001")
    print(f"  客户: {cust['customer_name']} (found={cust['found']})")

    item = await erp.get_item("DEMO-BRACKET-001")
    print(f"  物料: {item['item_name']} (found={item['found']})")

    prices = await erp.get_prices("DEMO-AL-102", "2026-09-28")
    print(f"  价格数: {len(prices)}")

    bom = await erp.get_bom("DEMO-BRACKET-001")
    print(f"  BOM 行: {len(bom['items'])} (found={bom['found']})")

    inv = await erp.get_inventory(["DEMO-AL-102", "DEMO-BOLT-008"])
    print(f"  库存记录: {len(inv)}")

    demands = await erp.get_material_demands("SO-TEST-001")
    print(f"  物料需求: {len(demands)}")

    quote_draft = await erp.create_quote_draft({
        "customer_id": "DEMO-CUSTOMER-001",
        "total_price": "15000.00",
        "currency": "CNY",
    })
    print(f"  报价草稿: {quote_draft['draft_id']}")

    read_back = await erp.read_back({"id": quote_draft["draft_id"], "doctype": "Quotation"})
    print(f"  回读确认: {read_back['read_back']}")

    print("  ✅ MockERP 全部通过")


async def test_mes():
    print()
    print("=== MockMES ===")
    mes = MockMESAdapter()

    wos = await mes.get_work_orders({})
    print(f"  工单数: {len(wos)}")

    ops = await mes.get_operation_progress("WO-DEMO-001")
    print(f"  工序数: {len(ops)}")

    wips = await mes.get_wip({})
    print(f"  WIP 数: {len(wips)}")

    qrs = await mes.get_quality_records({})
    print(f"  质量记录: {len(qrs)}")

    docs = await mes.get_production_documents({})
    print(f"  生产文档: {len(docs)}")

    evts = await mes.read_authoritative_events({})
    print(f"  权威事件: {len(evts)}")

    # 测试分页
    evts_paged = await mes.read_authoritative_events({}, cursor="2")
    print(f"  分页事件: {len(evts_paged)}")

    req = await mes.create_non_authoritative_request({"request_type": "expedite"})
    print(f"  请求状态: {req['status']} (authoritative={req['authoritative']})")

    print("  ✅ MockMES 全部通过")


async def test_singleton():
    print()
    print("=== 单例模式 ===")
    erp1 = get_mock_erp()
    erp2 = get_mock_erp()
    print(f"  ERP 单例: {erp1 is erp2}")

    mes1 = get_mock_mes()
    mes2 = get_mock_mes()
    print(f"  MES 单例: {mes1 is mes2}")
    print("  ✅ 单例模式正确")


async def main():
    await test_erp()
    await test_mes()
    await test_singleton()
    print()
    print("🎉 所有 Mock Adapter 测试通过！")


if __name__ == "__main__":
    asyncio.run(main())
