"""测试所有 4 个场景。"""
import sys
import uuid
sys.path.insert(0, r"e:\competition\汽车零部件工厂智能体开发\backend")

from app.persistence.database import SessionLocal, init_database
from app.runtime.scenarios import run_scenario

init_database()

scenarios = ["normal_order", "material_shortage", "quality_hold", "expedite"]

for name in scenarios:
    with SessionLocal() as session:
        try:
            result = run_scenario(
                session,
                name,
                seed=hash(name) % 100000,
                idempotency_key=f"test-{name}-{uuid.uuid4()}",
            )
            print(f"✅ {name}: project_id={result['project_id']}, approval_id={result.get('approval_id', 'N/A')}")
        except Exception as e:
            import traceback
            print(f"❌ {name}: {e}")
            traceback.print_exc()
            print()

print("\n🎉 所有场景测试完成！")
