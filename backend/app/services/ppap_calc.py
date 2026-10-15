"""PPAP 确定性计算契约（MSA 平均极差法、过程能力、尺寸规格比对）。

硬性边界（设计说明 §5.2、§13 AC06/AC07 与真实数据硬性规则）：

- 只接收**原始值**，绝不生成测量数值、绝不补默认值、绝不把汇总值当样本。
- 不内置任何验收阈值。阈值只能来自 ``acceptance_rules.json``（人工确认的
  业务规则）；规则缺失或未覆盖该指标时一律返回 ``NEEDS_CONFIRMATION``，
  参考区间只作为带明确标签的公开指引输出，不参与判定。
- 边界情况必须显式暴露而不是悄悄通过：样本不足、样本布局不齐、零变差、
  AV 开方为负、规格缺失/上下限颠倒、缺少单位或来源。
- 使用公开方法常数（AIAG MSA 平均极差法的 K1/K2/K3 与 d2 表）。这些是
  公开方法定义，不是本项目的业务规则；具体适用方法与样本要求仍待业务确认
  （《PPAP阶段A确认清单》§9 问题 3）。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from app.services import ppap_storage

# ----------------------------------------------------------------------
# 计算结果状态
# ----------------------------------------------------------------------

STATE_OK = "OK"
STATE_INCOMPLETE = "INCOMPLETE"                  # 计算完成但有缺项/需人工补
STATE_INSUFFICIENT = "INSUFFICIENT_DATA"         # 样本不足，无法计算
STATE_INVALID = "INVALID_INPUT"                  # 输入本身不合法（如上下限颠倒）
STATE_UNSUPPORTED = "UNSUPPORTED_CONFIG"         # 超出公开方法常数表范围

# 规格判定（尺寸/材料结果）
RESULT_OK = "OK"
RESULT_NG = "NG"
RESULT_NOT_APPLICABLE = "NOT_APPLICABLE"
RESULT_MISSING = "MISSING"

# 验收判定（必须来自业务规则）
AC_MEETS = "MEETS_RULE"
AC_CONDITIONAL = "CONDITIONAL_PER_RULE"
AC_BELOW = "BELOW_RULE"
AC_NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
AC_NO_VALUE = "NO_VALUE"

# ----------------------------------------------------------------------
# 公开方法常数（AIAG MSA 平均极差法）
# ----------------------------------------------------------------------

K1_BY_TRIALS = {2: 0.8862, 3: 0.5908}
K2_BY_APPRAISERS = {2: 0.7071, 3: 0.5231}
K3_BY_PARTS = {
    2: 0.7071,
    3: 0.5231,
    4: 0.4467,
    5: 0.4030,
    6: 0.3742,
    7: 0.3534,
    8: 0.3375,
    9: 0.3249,
    10: 0.3146,
}
# 子组极差法的 d2 常数（n=2..10）；仅用于 n>=2 的子组。
D2_BY_SUBGROUP_SIZE = {
    2: 1.128,
    3: 1.693,
    4: 2.059,
    5: 2.326,
    6: 2.534,
    7: 2.704,
    8: 2.847,
    9: 2.970,
    10: 3.078,
}
# 单值移动极差（移动跨度 n=2）的 d2。
D2_MOVING_RANGE = D2_BY_SUBGROUP_SIZE[2]

CONSTANTS_SOURCE = "AIAG MSA 平均极差法公开常数表（K1/K2/K3 与 d2）"

# 参考区间：只作为带标签的公开指引输出，绝不参与判定。
REFERENCE_BANDS: dict[str, list[dict[str, str]]] = {
    "percent_grr": [
        {"range": "≤10%", "meaning": "公开惯例通常视为可接受"},
        {"range": "10%~30%", "meaning": "公开惯例需视应用、成本与风险决定"},
        {"range": ">30%", "meaning": "公开惯例通常视为不可接受，应改进"},
    ],
    "ndc": [{"range": "≥5", "meaning": "公开惯例建议的可分辨类别数下限"}],
    "capability": [{"range": "≥1.33", "meaning": "公开惯例对稳定过程能力指数的常见建议"}],
}
REFERENCE_LABEL = "AIAG/公开惯例参考区值——未经本项目业务规则确认，仅作参考，不作为判定依据"

_METRIC_REFERENCE_KEY = {
    "percent_grr": "percent_grr",
    "ndc": "ndc",
    "cp": "capability",
    "cpk": "capability",
    "pp": "capability",
    "ppk": "capability",
}


# ----------------------------------------------------------------------
# 输入校验辅助
# ----------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def _as_float(value: Any) -> float | None:
    return float(value) if _is_number(value) else None


def _r(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _sample_stdev(values: Sequence[float]) -> float | None:
    """样本标准差（n-1）。n<2 时无定义。"""
    if len(values) < 2:
        return None
    mu = _mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def _base_result(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "state": STATE_OK,
        "value": None,
        "flags": [],
        "problems": [],
        "notes": [],
        "constants_source": CONSTANTS_SOURCE,
        "calculated_at": ppap_storage.now_iso(),
    }


# ----------------------------------------------------------------------
# MSA：平均极差法（GRR）
# ----------------------------------------------------------------------


def average_range_grr(
    measurements: Any,
    *,
    tolerance: float | None = None,
) -> dict[str, Any]:
    """平均极差法 GRR。

    ``measurements`` 形如::

        [{"appraiser": "A",
          "parts": [{"part": "P1", "trials": [1.0, 1.1, 1.0]}, ...]}, ...]

    评价人数 2~3、零件数 2~10、重复测次 2~3（超出公开常数表即报
    ``UNSUPPORTED_CONFIG``，不自行外推常数）。
    """
    result = _base_result("average_range_grr")
    result["method"] = "average_range"
    result["tolerance"] = tolerance if _is_number(tolerance) else None
    result["value"] = None
    result["constants"] = {
        "k1_by_trials": dict(K1_BY_TRIALS),
        "k2_by_appraisers": dict(K2_BY_APPRAISERS),
        "k3_by_parts": dict(K3_BY_PARTS),
        "source": CONSTANTS_SOURCE,
    }

    if not isinstance(measurements, list) or not measurements:
        result["state"] = STATE_INSUFFICIENT
        result["problems"].append("未提供 MSA 原始测量数据（评价人/零件/重复测次）")
        return result

    appraisers: list[str] = []
    grid: dict[str, dict[str, list[float]]] = {}
    part_order: list[str] = []

    for idx, entry in enumerate(measurements):
        if not isinstance(entry, Mapping):
            result["state"] = STATE_INVALID
            result["problems"].append(f"第 {idx + 1} 个评价人记录不是对象")
            return result
        name = str(entry.get("appraiser") or "").strip()
        if not name:
            result["state"] = STATE_INVALID
            result["problems"].append(f"第 {idx + 1} 个评价人记录缺少 appraiser 名称")
            return result
        if name in appraisers:
            result["state"] = STATE_INVALID
            result["problems"].append(f"评价人名称重复：{name}")
            return result
        parts = entry.get("parts")
        if not isinstance(parts, list) or not parts:
            result["state"] = STATE_INSUFFICIENT
            result["problems"].append(f"评价人 {name} 未提供零件测量记录")
            return result
        cell: dict[str, list[float]] = {}
        for p_idx, part in enumerate(parts):
            if not isinstance(part, Mapping):
                result["state"] = STATE_INVALID
                result["problems"].append(f"评价人 {name} 第 {p_idx + 1} 个零件记录不是对象")
                return result
            part_id = str(part.get("part") or part.get("part_id") or "").strip()
            if not part_id:
                result["state"] = STATE_INVALID
                result["problems"].append(f"评价人 {name} 第 {p_idx + 1} 个零件记录缺少零件标识")
                return result
            if part_id in cell:
                result["state"] = STATE_INVALID
                result["problems"].append(f"评价人 {name} 的零件 {part_id} 重复出现")
                return result
            trials_raw = part.get("trials")
            if not isinstance(trials_raw, list) or not trials_raw:
                result["state"] = STATE_INSUFFICIENT
                result["problems"].append(f"评价人 {name} 零件 {part_id} 缺少重复测次数据")
                return result
            trials: list[float] = []
            for t_idx, raw in enumerate(trials_raw):
                number = _as_float(raw)
                if number is None:
                    result["state"] = STATE_INVALID
                    result["problems"].append(
                        f"评价人 {name} 零件 {part_id} 第 {t_idx + 1} 次测量值非数值或缺失：{raw!r}"
                    )
                    return result
                trials.append(number)
            cell[part_id] = trials
            if part_id not in part_order:
                part_order.append(part_id)
        appraisers.append(name)
        grid[name] = cell

    appraiser_count = len(appraisers)
    part_count = len(part_order)
    trial_counts = {len(t) for cell in grid.values() for t in cell.values()}

    if len(trial_counts) != 1:
        result["state"] = STATE_INVALID
        result["problems"].append(
            f"重复测次数量不一致（出现 {sorted(trial_counts)} 次），平均极差法要求完整网格"
        )
        return result
    trial_count = trial_counts.pop()

    for name in appraisers:
        missing = [p for p in part_order if p not in grid[name]]
        if missing:
            result["state"] = STATE_INVALID
            result["problems"].append(f"评价人 {name} 缺少零件测量记录：{missing}")
            return result

    if not (2 <= appraiser_count <= 3):
        result["state"] = STATE_UNSUPPORTED
        result["problems"].append(
            f"评价人数 {appraiser_count} 超出平均极差法公开常数表范围（2~3），不自行外推 K2"
        )
    if not (2 <= part_count <= 10):
        result["state"] = STATE_UNSUPPORTED if result["state"] == STATE_OK else result["state"]
        result["problems"].append(
            f"零件数 {part_count} 超出平均极差法公开常数表范围（2~10），不自行外推 K3"
        )
    if not (2 <= trial_count <= 3):
        result["state"] = STATE_UNSUPPORTED if result["state"] == STATE_OK else result["state"]
        result["problems"].append(
            f"重复测次 {trial_count} 超出平均极差法公开常数表范围（2~3），不自行外推 K1"
        )
    if result["problems"]:
        return result

    k1 = K1_BY_TRIALS[trial_count]
    k2 = K2_BY_APPRAISERS[appraiser_count]
    k3 = K3_BY_PARTS[part_count]

    range_sum = 0.0
    range_count = 0
    all_ranges_zero = True
    for name in appraisers:
        for part_id in part_order:
            trials = grid[name][part_id]
            cell_range = max(trials) - min(trials)
            range_sum += cell_range
            range_count += 1
            if cell_range != 0.0:
                all_ranges_zero = False
    mean_range = range_sum / range_count
    ev = mean_range * k1
    if all_ranges_zero:
        result["flags"].append("zero_repeatability_variation")
        result["notes"].append("所有重复测次极差为 0：设备变差 EV 为 0 需人工确认测量分辨率是否足够。")

    appraiser_means = {
        name: _mean([v for part_id in part_order for v in grid[name][part_id]])
        for name in appraisers
    }
    appraiser_spread = max(appraiser_means.values()) - min(appraiser_means.values())

    av_squared = (appraiser_spread * k2) ** 2 - (ev**2 / (part_count * trial_count))
    av_clamped = av_squared < 0
    av = 0.0 if av_clamped else math.sqrt(av_squared)
    if av_clamped:
        result["flags"].append("av_clamped")
        result["notes"].append("AV 开方结果为负，按惯例取 0；说明重复性与评价人差异不显著，需人工确认。")

    grr = math.sqrt(ev**2 + av**2)

    part_means = {
        part_id: _mean([v for name in appraisers for v in grid[name][part_id]])
        for part_id in part_order
    }
    part_spread = max(part_means.values()) - min(part_means.values())
    pv = part_spread * k3
    tv = math.sqrt(grr**2 + pv**2)

    if pv == 0.0:
        result["flags"].append("zero_part_variation")
        result["notes"].append("零件均值极差为 0：样本无法体现零件间变差，样本选取需人工确认。")

    if tv == 0.0:
        result["flags"].append("zero_total_variation")
        percent_ev = percent_av = percent_pv = percent_grr = None
        ndc = None
        result["notes"].append("总变差为 0，无法给出百分比变差与可分辨类别数。")
    else:
        percent_ev = ev / tv * 100
        percent_av = av / tv * 100
        percent_pv = pv / tv * 100
        percent_grr = grr / tv * 100
        ndc = 1.41 * (pv / grr) if grr > 0 else None
        if grr == 0:
            result["flags"].append("zero_grr")
            result["notes"].append("GRR 为 0，可分辨类别数无定义。")

    percent_tolerance = None
    tol = result["tolerance"]
    if tol is not None:
        if tol > 0:
            percent_tolerance = 6 * grr / tol * 100
        else:
            result["flags"].append("invalid_tolerance")
            result["notes"].append("公差为非正值，无法计算公比（%Tolerance）。")

    result["state"] = STATE_OK
    result["appraiser_count"] = appraiser_count
    result["part_count"] = part_count
    result["trial_count"] = trial_count
    result["appraisers"] = list(appraisers)
    result["parts"] = list(part_order)
    result["value"] = {
        "ev": _r(ev),
        "av": _r(av),
        "grr": _r(grr),
        "pv": _r(pv),
        "tv": _r(tv),
        "percent_ev": _r(percent_ev),
        "percent_av": _r(percent_av),
        "percent_pv": _r(percent_pv),
        "percent_grr": _r(percent_grr),
        "ndc": _r(ndc),
        "ndc_truncated": int(math.floor(ndc)) if ndc is not None else None,
        "percent_tolerance": _r(percent_tolerance),
        "mean_range": _r(mean_range),
        "appraiser_mean_spread": _r(appraiser_spread),
        "part_mean_spread": _r(part_spread),
    }
    result["notes"].append(
        "平均极差法常数取自公开方法表；适用方法、样本布局与评价人数要求仍需业务确认。"
    )
    return result


# ----------------------------------------------------------------------
# 过程能力 / 过程性能
# ----------------------------------------------------------------------


def capability_indices(
    values: Any,
    *,
    usl: Any = None,
    lsl: Any = None,
    target: Any = None,
    subgroups: Any = None,
) -> dict[str, Any]:
    """Cp/Cpk（组内/短期）与 Pp/Ppk（总体/长期）。

    - σ_within 优先用子组极差 R̄/d2；无子组时用单值移动极差 R̄/1.128。
    - σ_overall 用样本标准差（n-1）。
    - 任何标准差为 0 时对应指数返回 None（不返回无穷大），并置零变差标记。
    """
    result = _base_result("capability_indices")
    result["value"] = None
    result["usl"] = usl if _is_number(usl) else None
    result["lsl"] = lsl if _is_number(lsl) else None
    result["target"] = target if _is_number(target) else None

    if not isinstance(values, list):
        result["state"] = STATE_INVALID
        result["problems"].append("过程研究样本必须是数值列表")
        return result

    numbers: list[float] = []
    for idx, raw in enumerate(values):
        number = _as_float(raw)
        if number is None:
            result["state"] = STATE_INVALID
            result["problems"].append(f"第 {idx + 1} 个样本值非数值或缺失：{raw!r}")
            return result
        numbers.append(number)

    if len(numbers) < 2:
        result["state"] = STATE_INSUFFICIENT
        result["problems"].append(f"样本数 {len(numbers)} 不足，至少需要 2 个原始样本值")
        result["sample_count"] = len(numbers)
        return result

    usl_v = result["usl"]
    lsl_v = result["lsl"]
    if usl_v is not None and lsl_v is not None and lsl_v > usl_v:
        result["state"] = STATE_INVALID
        result["problems"].append(f"规格上下限颠倒：LSL={lsl_v} > USL={usl_v}")
        return result
    if usl_v is None:
        result["flags"].append("usl_missing")
    if lsl_v is None:
        result["flags"].append("lsl_missing")
    if usl_v is None and lsl_v is None:
        result["notes"].append("未提供任何规格限，只能给出变差统计，无法计算能力指数。")

    sigma_within: float | None = None
    sigma_basis = ""
    if subgroups is not None:
        if not isinstance(subgroups, list) or not subgroups:
            result["state"] = STATE_INVALID
            result["problems"].append("subgroups 必须是非空的子组列表")
            return result
        sizes = set()
        for s_idx, subgroup in enumerate(subgroups):
            if not isinstance(subgroup, list) or not subgroup:
                result["state"] = STATE_INVALID
                result["problems"].append(f"第 {s_idx + 1} 个子组为空或不是列表")
                return result
            sizes.add(len(subgroup))
            for raw in subgroup:
                if _as_float(raw) is None:
                    result["state"] = STATE_INVALID
                    result["problems"].append(f"第 {s_idx + 1} 个子组含非数值项：{raw!r}")
                    return result
        if len(sizes) != 1:
            result["state"] = STATE_INVALID
            result["problems"].append(f"子组大小不一致（出现 {sorted(sizes)}），无法用 R̄/d2 估计组内变差")
            return result
        flattened = [float(value) for subgroup in subgroups for value in subgroup]
        if flattened != numbers:
            result["state"] = STATE_INVALID
            result["problems"].append("子组展开数据必须与原始样本列表逐项一致，不允许混用两套数据")
            return result
        subgroup_size = sizes.pop()
        if subgroup_size not in D2_BY_SUBGROUP_SIZE:
            result["state"] = STATE_UNSUPPORTED
            result["problems"].append(
                f"子组大小 {subgroup_size} 超出公开 d2 常数表范围（2~10），不自行外推"
            )
            return result
        ranges = [max(sg) - min(sg) for sg in subgroups]
        mean_range = _mean(ranges)
        sigma_within = mean_range / D2_BY_SUBGROUP_SIZE[subgroup_size]
        sigma_basis = f"subgroup_range_rbar_over_d2(n={subgroup_size})"
        result["subgroup_count"] = len(subgroups)
        result["subgroup_size"] = subgroup_size
    else:
        moving = [abs(numbers[i] - numbers[i - 1]) for i in range(1, len(numbers))]
        mean_moving_range = _mean(moving) if moving else 0.0
        sigma_within = mean_moving_range / D2_MOVING_RANGE
        sigma_basis = "moving_range_rbar_over_d2(n=2)"
        result["moving_range_count"] = len(moving)

    sigma_overall = _sample_stdev(numbers)
    mean_value = _mean(numbers)

    if sigma_within == 0.0:
        result["flags"].append("zero_within_variation")
        result["notes"].append("组内变差为 0，Cp/Cpk 无定义（不返回无穷大）。")
    if sigma_overall == 0.0:
        result["flags"].append("zero_overall_variation")
        result["notes"].append("总体变差为 0，Pp/Ppk 无定义（不返回无穷大）。")
    if len(numbers) < 30:
        result["flags"].append("small_sample")
        result["notes"].append(
            f"样本数 {len(numbers)} 较少；过程研究所需的样本量与选取规则需业务确认。"
        )

    cp = cpk = pp = ppk = None
    if sigma_within and sigma_within > 0:
        if usl_v is not None and lsl_v is not None:
            cp = (usl_v - lsl_v) / (6 * sigma_within)
        upper_cpk = (usl_v - mean_value) / (3 * sigma_within) if usl_v is not None else None
        lower_cpk = (mean_value - lsl_v) / (3 * sigma_within) if lsl_v is not None else None
        candidates = [v for v in (upper_cpk, lower_cpk) if v is not None]
        cpk = min(candidates) if candidates else None
    if sigma_overall and sigma_overall > 0:
        if usl_v is not None and lsl_v is not None:
            pp = (usl_v - lsl_v) / (6 * sigma_overall)
        upper_ppk = (usl_v - mean_value) / (3 * sigma_overall) if usl_v is not None else None
        lower_ppk = (mean_value - lsl_v) / (3 * sigma_overall) if lsl_v is not None else None
        candidates = [v for v in (upper_ppk, lower_ppk) if v is not None]
        ppk = min(candidates) if candidates else None

    result["state"] = STATE_INCOMPLETE if result["flags"] else STATE_OK
    result["sample_count"] = len(numbers)
    result["sigma_basis"] = sigma_basis
    result["value"] = {
        "count": len(numbers),
        "mean": _r(mean_value),
        "min": _r(min(numbers)),
        "max": _r(max(numbers)),
        "stddev_within": _r(sigma_within),
        "stddev_overall": _r(sigma_overall),
        "cp": _r(cp),
        "cpk": _r(cpk),
        "pp": _r(pp),
        "ppk": _r(ppk),
    }
    return result


# ----------------------------------------------------------------------
# 业务规则（验收阈值）读取与判定
# ----------------------------------------------------------------------


def load_acceptance_rules(path: Any = None) -> dict[str, Any]:
    """读取人工确认的业务规则；不存在即"未确认"，不回落内置阈值。"""
    loaded = ppap_storage.load_rules_document(path)
    document = loaded.get("document") or {}
    metrics = document.get("metrics") if isinstance(document.get("metrics"), dict) else {}
    placeholders = ("", "待确认", "TBD", "PENDING", "UNCONFIRMED")
    confirmed_by = str(document.get("confirmed_by") or "").strip()
    return {
        "configured": bool(loaded.get("configured")) and bool(metrics),
        "path": loaded.get("path"),
        "rule_version": loaded.get("rule_version") or "",
        "source": str(document.get("source") or "").strip(),
        "confirmed_by": confirmed_by,
        "confirmed_at": str(document.get("confirmed_at") or "").strip(),
        "metrics": metrics,
        "error": loaded.get("error"),
        "notes": list(document.get("notes")) if isinstance(document.get("notes"), list) else [],
        "requires_human_confirmation": confirmed_by in placeholders,
    }


def classify_metric(metric: str, value: Any, rules: Mapping[str, Any] | None) -> dict[str, Any]:
    """按业务规则判定单个指标；规则缺失时返回 NEEDS_CONFIRMATION。"""
    number = _as_float(value)
    reference_key = _METRIC_REFERENCE_KEY.get(metric)
    outcome: dict[str, Any] = {
        "metric": metric,
        "value": _r(number),
        "acceptance_state": AC_NEEDS_CONFIRMATION,
        "threshold": None,
        "rule_source": None,
        "rule_version": None,
        "basis": "",
        "reference_bands": REFERENCE_BANDS.get(reference_key, []) if reference_key else [],
        "reference_label": REFERENCE_LABEL,
        "reference_only": True,
    }
    if number is None:
        outcome["acceptance_state"] = AC_NO_VALUE
        outcome["basis"] = "指标无数值，无法判定（不按合格处理）。"
        return outcome

    rules = rules or {}
    confirmed_by = str(rules.get("confirmed_by") or "").strip()
    if rules.get("requires_human_confirmation") or confirmed_by.upper() in ("", "TBD", "PENDING", "UNCONFIRMED") or confirmed_by == "待确认":
        outcome["basis"] = "验收规则尚未由业务人员明确确认，阈值不生效。"
        return outcome
    metric_rule = (rules.get("metrics") or {}).get(metric)
    if not rules.get("configured") or not isinstance(metric_rule, Mapping):
        outcome["basis"] = (
            "未配置该指标的验收规则：acceptance_rules.json 缺失或未覆盖此指标，"
            "按“规则未确认”处理，不套用参考区间。"
        )
        return outcome

    outcome["rule_source"] = str(metric_rule.get("source") or rules.get("source") or "").strip() or None
    outcome["rule_version"] = rules.get("rule_version") or None

    minimum = _as_float(metric_rule.get("min"))
    maximum = _as_float(metric_rule.get("max"))
    conditional_max = _as_float(metric_rule.get("conditional_max"))

    if minimum is not None:
        outcome["threshold"] = {"type": "min", "value": minimum}
        if number >= minimum:
            outcome["acceptance_state"] = AC_MEETS
            outcome["basis"] = f"按已确认规则：{metric} ≥ {minimum}"
        else:
            outcome["acceptance_state"] = AC_BELOW
            outcome["basis"] = f"按已确认规则：{metric} < {minimum}"
        return outcome

    if maximum is not None:
        outcome["threshold"] = (
            {"type": "max", "value": maximum, "conditional_max": conditional_max}
            if conditional_max is not None
            else {"type": "max", "value": maximum}
        )
        if number <= maximum:
            outcome["acceptance_state"] = AC_MEETS
            outcome["basis"] = f"按已确认规则：{metric} ≤ {maximum}"
        elif conditional_max is not None and number <= conditional_max:
            outcome["acceptance_state"] = AC_CONDITIONAL
            outcome["basis"] = (
                f"按已确认规则：{metric} 位于 {maximum} 与条件接受上限 {conditional_max} 之间"
            )
        else:
            outcome["acceptance_state"] = AC_BELOW
            outcome["basis"] = (
                f"按已确认规则：{metric} > {conditional_max if conditional_max is not None else maximum}"
            )
        return outcome

    outcome["basis"] = f"已配置 {metric} 规则但未包含 min/max 阈值，按“规则未确认”处理。"
    outcome["rule_source"] = None
    outcome["rule_version"] = None
    return outcome


# ----------------------------------------------------------------------
# 尺寸 / 材料结果：规格比对（不是 PPAP 结论）
# ----------------------------------------------------------------------


def dimensional_results(rows: Any) -> dict[str, Any]:
    """按原始检验记录逐测点做规格比对。

    输入行字段：``point``（测点编号，必填）、``value``、``usl``、``lsl``、
    ``nominal``、``unit``、``source_ref``、``not_applicable``、``missing_reason``。

    输出只区分 OK / NG / NOT_APPLICABLE / MISSING 与缺规格，绝不填“合格”
    来补缺口，也不代表 PPAP 内部批准结论。
    """
    result = _base_result("dimensional_results")
    result["rows"] = []
    result["summary"] = {
        "total": 0,
        "ok": 0,
        "ng": 0,
        "not_applicable": 0,
        "missing_value": 0,
        "missing_spec": 0,
    }
    result["value"] = result["summary"]
    result["notes"].append(
        "本结果仅为规格比对，不代表 PPAP 批准结论；不合格处置按既有质量流程（NCR）执行。"
    )

    if not isinstance(rows, list) or not rows:
        result["state"] = STATE_INSUFFICIENT
        result["problems"].append("未提供任何测点原始检验记录")
        return result

    seen_points: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            result["state"] = STATE_INVALID
            result["problems"].append(f"第 {idx + 1} 行测点记录不是对象")
            return result
        point = str(row.get("point") or row.get("point_id") or "").strip()
        if not point:
            result["state"] = STATE_INVALID
            result["problems"].append(f"第 {idx + 1} 行测点记录缺少 point 编号")
            return result
        if point in seen_points:
            result["state"] = STATE_INVALID
            result["problems"].append(f"测点编号重复：{point}")
            return result
        seen_points.add(point)

        flags: list[str] = []
        value = _as_float(row.get("value"))
        raw_value_present = row.get("value") not in (None, "")
        usl = _as_float(row.get("usl"))
        lsl = _as_float(row.get("lsl"))
        nominal = _as_float(row.get("nominal"))
        unit = str(row.get("unit") or "").strip()
        source_ref = str(row.get("source_ref") or "").strip()
        not_applicable = bool(row.get("not_applicable"))
        missing_reason = str(row.get("missing_reason") or "").strip()

        if not unit:
            flags.append("missing_unit")
        if not source_ref:
            flags.append("missing_source_ref")

        entry: dict[str, Any] = {
            "point": point,
            "value": _r(value, 8) if value is not None else None,
            "usl": _r(usl, 8),
            "lsl": _r(lsl, 8),
            "nominal": _r(nominal, 8),
            "unit": unit,
            "source_ref": source_ref,
            "flags": flags,
            "result": None,
            "result_basis": "",
        }

        if not_applicable:
            entry["result"] = RESULT_NOT_APPLICABLE
            entry["result_basis"] = missing_reason or "已确认该测点不适用"
            if not missing_reason:
                entry["flags"].append("not_applicable_without_basis")
            result["summary"]["not_applicable"] += 1
        elif value is None:
            entry["result"] = RESULT_MISSING
            entry["result_basis"] = (
                f"缺少实测值（{missing_reason}）" if missing_reason else "缺少实测值"
            )
            if raw_value_present:
                entry["flags"].append("value_not_numeric")
            result["summary"]["missing_value"] += 1
        elif usl is None and lsl is None:
            entry["result"] = None
            entry["result_basis"] = "缺少规格上下限，无法比对（不按合格处理）"
            entry["flags"].append("missing_spec")
            result["summary"]["missing_spec"] += 1
        else:
            if lsl is not None and usl is not None and lsl > usl:
                result["state"] = STATE_INVALID
                result["problems"].append(f"测点 {point} 规格上下限颠倒：LSL={lsl} > USL={usl}")
                return result
            if usl is None or lsl is None:
                entry["flags"].append("single_sided_spec")
            out_of_spec = []
            if usl is not None and value > usl:
                out_of_spec.append(f"超出上限 {usl}")
            if lsl is not None and value < lsl:
                out_of_spec.append(f"低于下限 {lsl}")
            if out_of_spec:
                entry["result"] = RESULT_NG
                entry["result_basis"] = "；".join(out_of_spec)
                result["summary"]["ng"] += 1
            else:
                entry["result"] = RESULT_OK
                entry["result_basis"] = "位于已确认规格范围内"
                result["summary"]["ok"] += 1

        result["rows"].append(entry)

    result["summary"]["total"] = len(result["rows"])
    if result["summary"]["missing_value"] or result["summary"]["missing_spec"]:
        result["state"] = STATE_INCOMPLETE if result["state"] == STATE_OK else result["state"]
        result["notes"].append("存在缺测值或缺规格的测点，已单独标出，未按合格处理。")
    return result
