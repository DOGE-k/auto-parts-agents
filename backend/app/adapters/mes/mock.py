from __future__ import annotations

from typing import Any


class MockMES:
    """Fixture-facing MES simulator; no OpenMES fields or transport are assumed."""

    def quality_status(self, project_id: str, released: bool) -> dict[str, Any]:
        return {
            "object_id": f"QUALITY-{project_id}",
            "event_type": "QUALITY_RELEASED" if released else "QUALITY_HOLD",
            "status": "QUALITY_RELEASED" if released else "QUALITY_HOLD",
            "authority": "MockMES",
            "evidence_id": "ev-mock-mes-quality",
        }
