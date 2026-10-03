from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.services.real_order import _estimate_eta_from_observed_rate


def test_eta_uses_observed_execution_rate_and_remaining_quantity() -> None:
    now = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
    result = _estimate_eta_from_observed_rate(
        planned=Decimal("100"),
        completed=Decimal("40"),
        progress=[
            {
                "operation_id": "step-2",
                "completed_qty": "40",
                "actual_start_at": "2026-09-29T00:00:00Z",
                "actual_end_at": "2026-09-30T00:00:00Z",
            }
        ],
        batches=[],
        status="IN_PROGRESS",
        now=now,
    )

    assert result["eta_status"] == "RATE_BASED"
    assert result["eta_basis"] == "observed_production_rate"
    assert result["observed_rate"]["units_per_hour"] == 1.6667
    assert result["observed_rate"]["remaining_qty"] == "60"
    assert result["eta"] == "2026-10-01T12:00:00+00:00"


def test_eta_is_data_missing_without_actual_timing_and_never_uses_due_date() -> None:
    result = _estimate_eta_from_observed_rate(
        planned=Decimal("100"),
        completed=Decimal("40"),
        progress=[
            {
                "operation_id": "overall",
                "completed_qty": "40",
                "planned_start_at": "2026-09-01T00:00:00Z",
            }
        ],
        batches=[],
        status="IN_PROGRESS",
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )

    assert result["eta_status"] == "DATA_MISSING"
    assert result["eta"] is None
    assert "due_date" not in result["eta_basis"]
    assert result["data_gaps"][0]["field"] == "observed_production_rate"


def test_eta_can_use_real_batch_step_when_snapshot_has_no_timing() -> None:
    result = _estimate_eta_from_observed_rate(
        planned=Decimal("200"),
        completed=Decimal("100"),
        progress=[],
        batches=[
            {
                "batch_id": "B-1",
                "steps": [
                    {
                        "step_id": "S-1",
                        "passed_qty": "100",
                        "started_at": "2026-09-29T00:00:00Z",
                        "completed_at": "2026-09-30T00:00:00Z",
                    }
                ],
            }
        ],
        status="IN_PROGRESS",
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )

    assert result["eta_status"] == "RATE_BASED"
    assert result["observed_rate"]["source"] == "S-1"
