"""
测试 AIP 智能体调用流程。
验证四个智能体的 AIP 服务是否正常工作。
"""
import asyncio
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.aip.aip_agent_client import AipAgentClient


async def test_quotation_cost():
    """测试报价智能体 - 成本计算"""
    print("\n" + "=" * 60)
    print("🧪 测试报价智能体: 成本计算")
    print("=" * 60)

    client = AipAgentClient(
        partner_url="http://127.0.0.1:8000/aip/quotation/rpc",
        leader_id="test-leader-001",
    )

    try:
        result = await client.invoke_skill(
            skill_id="quotation.calculate_cost",
            inputs={
                "product_id": "PROD-GEAR-001",
                "quantity": 1000,
                "bom_items": [
                    {"material_id": "MAT-STEEL-001", "quantity": 2.5, "unit_price": "15.00"},
                    {"material_id": "MAT-ALU-002", "quantity": 0.8, "unit_price": "28.50"},
                ],
                "labor_hours": 3.5,
                "labor_rate": "45.00",
                "overhead_rate": "0.25",
            },
            session_id="test-session-001",
        )
        print(f"✅ 调用成功!")
        print(f"📊 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as e:
        print(f"❌ 调用失败: {e}")
    finally:
        await client.close()


async def test_procurement_net_requirement():
    """测试采购智能体 - 净需求计算"""
    print("\n" + "=" * 60)
    print("🧪 测试采购智能体: 净需求计算")
    print("=" * 60)

    client = AipAgentClient(
        partner_url="http://127.0.0.1:8000/aip/procurement/rpc",
        leader_id="test-leader-001",
    )

    try:
        result = await client.invoke_skill(
            skill_id="procurement.calculate_net_requirement",
            inputs={
                "material_id": "MAT-STEEL-001",
                "gross_requirement": 5000,
                "stock_on_hand": 1200,
                "on_order": 800,
                "safety_stock": 500,
                "lot_size": 1000,
            },
            session_id="test-session-001",
        )
        print(f"✅ 调用成功!")
        print(f"📊 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as e:
        print(f"❌ 调用失败: {e}")
    finally:
        await client.close()


async def test_tracking_eta():
    """测试跟单智能体 - 交期预估"""
    print("\n" + "=" * 60)
    print("🧪 测试跟单智能体: 交期预估")
    print("=" * 60)

    client = AipAgentClient(
        partner_url="http://127.0.0.1:8000/aip/tracking/rpc",
        leader_id="test-leader-001",
    )

    try:
        result = await client.invoke_skill(
            skill_id="tracking.calculate_eta",
            inputs={
                "project_id": "PROJ-TEST-001",
                "order_date": "2026-09-01",
                "promised_delivery": "2026-10-15",
                "current_progress_pct": 45,
                "total_processes": 8,
                "completed_processes": 4,
                "avg_cycle_days_per_process": 3,
            },
            session_id="test-session-001",
        )
        print(f"✅ 调用成功!")
        print(f"📊 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as e:
        print(f"❌ 调用失败: {e}")
    finally:
        await client.close()


async def test_quality_document():
    """测试质量文档智能体 - 资料清单生成"""
    print("\n" + "=" * 60)
    print("🧪 测试质量文档智能体: 资料清单生成")
    print("=" * 60)

    client = AipAgentClient(
        partner_url="http://127.0.0.1:8000/aip/quality-document/rpc",
        leader_id="test-leader-001",
    )

    try:
        result = await client.invoke_skill(
            skill_id="quality_document.build_checklist",
            inputs={
                "project_id": "PROJ-TEST-001",
                "product_type": "safety_critical",
                "customer_requirements": ["rohs", "reach"],
            },
            session_id="test-session-001",
        )
        print(f"✅ 调用成功!")
        print(f"📊 结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as e:
        print(f"❌ 调用失败: {e}")
    finally:
        await client.close()


async def test_cross_agent_call():
    """测试跨智能体调用：跟单调用报价（模拟真实协作场景）"""
    print("\n" + "=" * 60)
    print("🧪 测试跨智能体调用: 跟单 → 报价（成本评估）")
    print("=" * 60)

    # 跟单智能体发现物料短缺，调用报价智能体做成本评估
    tracking_client = AipAgentClient(
        partner_url="http://127.0.0.1:8000/aip/tracking/rpc",
        leader_id="test-coordinator-001",
    )
    quotation_client = AipAgentClient(
        partner_url="http://127.0.0.1:8000/aip/quotation/rpc",
        leader_id="tracking-agent-001",
    )

    try:
        # 1. 跟单检测风险
        risk_result = await tracking_client.invoke_skill(
            skill_id="tracking.detect_risk",
            inputs={
                "project_id": "PROJ-TEST-001",
                "material_shortage": True,
                "quality_issue": False,
                "progress_pct": 45,
                "expected_pct": 60,
            },
        )
        print(f"📋 风险检测结果: {risk_result['risk_count']} 个风险，级别: {risk_result['overall_severity']}")

        # 2. 检测到物料短缺，调用报价做成本评估
        print("🔄 检测到物料短缺风险，调用报价智能体做成本评估...")
        cost_result = await quotation_client.invoke_skill(
            skill_id="quotation.create_cost_assessment",
            inputs={
                "cost_assessment_id": "CA-001",
                "parent_order_id": "SO-001",
                "accepted_quote_id": "Q-001",
                "accepted_quote_status": "accepted",
                "shortage_quantity": 500,
                "baseline_unit_price": "15.00",
                "options": [
                    {"option_id": "OPT-A", "supplier_id": "SUP-A", "unit_price": "18.50"},
                    {"option_id": "OPT-B", "supplier_id": "SUP-B", "unit_price": "16.80"},
                    {"option_id": "OPT-C", "supplier_id": "SUP-C", "unit_price": "17.20"},
                ],
            },
            session_id="collab-001",
        )
        print(f"✅ 跨智能体调用成功!")
        print(f"📊 成本评估结果:")
        for opt in cost_result.get("option_deltas", []):
            print(f"   - {opt['supplier_id']}: 额外成本 {opt['delta_for_shortage']} 元")
    except Exception as e:
        print(f"❌ 调用失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await tracking_client.close()
        await quotation_client.close()


async def main():
    print("🚀 开始 AIP 智能体互联测试")

    await test_quotation_cost()
    await test_procurement_net_requirement()
    await test_tracking_eta()
    await test_quality_document()
    await test_cross_agent_call()

    print("\n" + "=" * 60)
    print("🎉 所有测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
