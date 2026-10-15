"""AC06: independent numerical checks and failure/confirmation boundaries.

These are labeled, deterministic test fixtures, not factory test results.
"""
from __future__ import annotations

import copy
import math
import statistics

import pytest

from app.services import ppap_calc as calc


GRID = [
    {"appraiser": "fixture-A", "parts": [{"part": "fixture-P1", "trials": [1.0, 1.1]}, {"part": "fixture-P2", "trials": [2.0, 2.1]}]},
    {"appraiser": "fixture-B", "parts": [{"part": "fixture-P1", "trials": [1.2, 1.3]}, {"part": "fixture-P2", "trials": [2.2, 2.3]}]},
]


def test_grr_against_independent_hand_calculation():
    result = calc.average_range_grr(GRID, tolerance=1)
    ev = 0.1 * 0.8862
    av = math.sqrt((0.2 * 0.7071) ** 2 - ev ** 2 / 4)
    grr = math.hypot(ev, av)
    pv = 1.0 * 0.7071
    total = math.hypot(grr, pv)
    expected = {"ev": ev, "av": av, "grr": grr, "pv": pv, "tv": total,
                "percent_grr": grr / total * 100, "percent_tolerance": 6 * grr * 100,
                "ndc": 1.41 * pv / grr}
    for key, value in expected.items():
        assert result["value"][key] == pytest.approx(value, abs=1e-6)
    assert result["appraiser_count"] == 2 and result["part_count"] == 2 and result["trial_count"] == 2


@pytest.mark.parametrize("value", [True, "1.0", None, math.nan, math.inf])
def test_grr_rejects_non_raw_finite_numeric_measurement(value):
    grid = copy.deepcopy(GRID)
    grid[0]["parts"][0]["trials"][0] = value
    result = calc.average_range_grr(grid)
    assert result["state"] == calc.STATE_INVALID
    assert result["value"] is None and result["problems"]


def test_grr_incomplete_and_unsupported_layout_exposed():
    assert calc.average_range_grr([])["state"] == calc.STATE_INSUFFICIENT
    grid = copy.deepcopy(GRID)
    grid[0]["parts"].pop()
    assert calc.average_range_grr(grid)["state"] == calc.STATE_INVALID
    grid = copy.deepcopy(GRID)
    grid[0]["parts"][0]["trials"].append(1.2)
    assert calc.average_range_grr(grid)["state"] == calc.STATE_INVALID
    assert calc.average_range_grr(GRID[:1])["state"] == calc.STATE_UNSUPPORTED


def test_grr_zero_variance_and_negative_av_radicand_never_infinity():
    grid = copy.deepcopy(GRID)
    for appraiser in grid:
        for part in appraiser["parts"]:
            part["trials"] = [1.0, 1.0]
    zero = calc.average_range_grr(grid, tolerance=0)
    assert zero["value"]["percent_grr"] is None and zero["value"]["ndc"] is None
    assert "zero_total_variation" in zero["flags"] and "invalid_tolerance" in zero["flags"]
    identical = copy.deepcopy(GRID)
    identical[1]["parts"] = copy.deepcopy(identical[0]["parts"])
    clamped = calc.average_range_grr(identical)
    assert clamped["value"]["av"] == 0 and "av_clamped" in clamped["flags"]


def test_process_capability_matches_independent_moving_range_and_sample_sd():
    values = [9.8, 10.0, 10.2, 10.1, 9.9]
    result = calc.capability_indices(values, usl=10.5, lsl=9.5)
    mean = statistics.mean(values)
    within = statistics.mean(abs(a - b) for a, b in zip(values, values[1:])) / 1.128
    overall = statistics.stdev(values)
    assert result["value"]["mean"] == pytest.approx(mean)
    assert result["value"]["cp"] == pytest.approx(1 / (6 * within), abs=1e-6)
    assert result["value"]["cpk"] == pytest.approx(min(10.5 - mean, mean - 9.5) / (3 * within), abs=1e-6)
    assert result["value"]["pp"] == pytest.approx(1 / (6 * overall), abs=1e-6)
    assert result["value"]["ppk"] == pytest.approx(min(10.5 - mean, mean - 9.5) / (3 * overall), abs=1e-6)
    assert "small_sample" in result["flags"]


def test_process_capability_subgroup_range_basis_matches_independent_result():
    groups = [[9.8, 10.0], [10.2, 10.1], [9.9, 10.0]]
    result = calc.capability_indices([v for group in groups for v in group], subgroups=groups, usl=10.5, lsl=9.5)
    within = statistics.mean(max(g) - min(g) for g in groups) / 1.128
    assert result["value"]["stddev_within"] == pytest.approx(within, abs=1e-6)
    assert result["sigma_basis"] == "subgroup_range_rbar_over_d2(n=2)"


def test_process_subgroups_cannot_mix_a_different_sample_list():
    result = calc.capability_indices([1, 2, 3, 4], subgroups=[[1, 2], [7, 8]], usl=10, lsl=0)
    assert result["state"] == calc.STATE_INVALID and result["value"] is None
    wrong_order = calc.capability_indices([1, 3, 2, 4], subgroups=[[1, 2], [3, 4]])
    assert wrong_order["state"] == calc.STATE_INVALID
    wrong_count = calc.capability_indices([1, 2, 3], subgroups=[[1, 2], [3, 4]])
    assert wrong_count["state"] == calc.STATE_INVALID


@pytest.mark.parametrize("values", [[True, 2], [1, "2"], [1, None], [1, math.nan], [1, math.inf]])
def test_process_capability_non_numeric_sample_rejected(values):
    result = calc.capability_indices(values)
    assert result["state"] == calc.STATE_INVALID and result["value"] is None


def test_process_capability_missing_single_sided_zero_and_bad_spec():
    assert calc.capability_indices([])["state"] == calc.STATE_INSUFFICIENT
    assert calc.capability_indices([1])["state"] == calc.STATE_INSUFFICIENT
    bad = calc.capability_indices([1, 2], usl=0, lsl=3)
    assert bad["state"] == calc.STATE_INVALID and bad["value"] is None
    no_spec = calc.capability_indices([1, 2, 3])
    assert all(no_spec["value"][k] is None for k in ("cp", "cpk", "pp", "ppk"))
    one_sided = calc.capability_indices([1, 2, 3], usl=4)
    assert one_sided["value"]["cp"] is None and one_sided["value"]["cpk"] is not None
    zero = calc.capability_indices([2, 2, 2], usl=3, lsl=1)
    assert all(zero["value"][k] is None for k in ("cp", "cpk", "pp", "ppk"))
    assert "zero_within_variation" in zero["flags"] and "zero_overall_variation" in zero["flags"]


def test_process_capability_inconsistent_and_unsupported_subgroups():
    uneven = calc.capability_indices([1, 2, 3, 4, 5], subgroups=[[1, 2], [3, 4, 5]])
    assert uneven["state"] == calc.STATE_INVALID
    too_large = calc.capability_indices(list(range(11)), subgroups=[list(range(11))])
    assert too_large["state"] == calc.STATE_UNSUPPORTED


def test_rules_missing_corrupt_and_unconfirmed_are_distinct(tmp_path):
    missing = calc.load_acceptance_rules(tmp_path / "missing.json")
    assert not missing["configured"] and missing["error"] is None
    assert calc.classify_metric("cpk", 9.99, missing)["acceptance_state"] == calc.AC_NEEDS_CONFIRMATION
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{ invalid", encoding="utf-8")
    broken = calc.load_acceptance_rules(corrupt)
    assert not broken["configured"] and broken["error"]
    assert calc.classify_metric("percent_grr", 0, {})["acceptance_state"] == calc.AC_NEEDS_CONFIRMATION
    assert calc.classify_metric("cpk", None, {})["acceptance_state"] == calc.AC_NO_VALUE


def test_explicit_rule_boundaries_only():
    rules = {"configured": True, "confirmed_by": "fixture-reviewer", "requires_human_confirmation": False,
             "rule_version": "TEST-RULE", "source": "test fixture only", "metrics": {"cpk": {"min": 1.2}, "percent_grr": {"max": 10, "conditional_max": 20}}}
    assert calc.classify_metric("cpk", 1.2, rules)["acceptance_state"] == calc.AC_MEETS
    assert calc.classify_metric("cpk", 1.199, rules)["acceptance_state"] == calc.AC_BELOW
    assert calc.classify_metric("percent_grr", 10, rules)["acceptance_state"] == calc.AC_MEETS
    assert calc.classify_metric("percent_grr", 20, rules)["acceptance_state"] == calc.AC_CONDITIONAL
    assert calc.classify_metric("percent_grr", 20.001, rules)["acceptance_state"] == calc.AC_BELOW
    assert calc.classify_metric("ppk", 5, rules)["acceptance_state"] == calc.AC_NEEDS_CONFIRMATION


def test_dimensional_zero_missing_not_applicable_ng_and_unit_exposed():
    result = calc.dimensional_results([
        {"point": "FIXTURE-1", "value": 0, "usl": 1, "lsl": -1, "unit": "mm", "source_ref": "test fixture"},
        {"point": "FIXTURE-2", "value": None, "usl": 1, "lsl": -1, "unit": "mm", "source_ref": "test fixture"},
        {"point": "FIXTURE-3", "not_applicable": True, "missing_reason": "explicit fixture basis"},
        {"point": "FIXTURE-4", "value": 2, "usl": 1, "lsl": -1, "unit": "mm", "source_ref": "test fixture"},
        {"point": "FIXTURE-5", "value": 1.0},
    ])
    assert [row["result"] for row in result["rows"]] == ["OK", "MISSING", "NOT_APPLICABLE", "NG", None]
    assert result["summary"] == {"total": 5, "ok": 1, "ng": 1, "not_applicable": 1, "missing_value": 1, "missing_spec": 1}
    assert "missing_unit" in result["rows"][-1]["flags"] and "missing_source_ref" in result["rows"][-1]["flags"]


def test_dimensional_duplicate_point_and_reversed_spec_rejected():
    assert calc.dimensional_results([{"point": "P"}, {"point": "P"}])["state"] == calc.STATE_INVALID
    assert calc.dimensional_results([{"point": "P", "value": 1, "usl": 0, "lsl": 2}])["state"] == calc.STATE_INVALID
