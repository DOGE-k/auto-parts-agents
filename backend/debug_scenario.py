"""直接测试 quality_hold 场景。"""
import sys
sys.path.insert(0, r"e:\competition\汽车零部件工厂智能体开发\backend")

from app.persistence.database import SessionLocal, init_database
from app.runtime.scenarios import run_scenario

init_database()

with SessionLocal() as session:
    try:
        result = run_scenario(
            session,
            "quality_hold",
            seed=20260923,
            idempotency_key=f"test-debug-{__import__('uuid').uuid4()}",
        )
        print(f"SUCCESS: {result['project_id']}")
        print(f"approval_id: {result.get('approval_id', 'N/A')}")
    except Exception as e:
        import traceback
        traceback.print_exc()
