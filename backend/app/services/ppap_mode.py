"""Pure classification and gates for immutable PPAP REAL / TEST tasks.

No database, files, credentials, or upstream clients are accessed here. English
fixture/mock tokens and explicit test identifiers are provenance markers; free
Chinese descriptions containing 测试 (for example 试验测试报告) are not markers.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MODE_REAL = "REAL"
MODE_TEST = "TEST"
MODES = (MODE_REAL, MODE_TEST)
TEST_SOURCE_TYPE = "synthetic_demo_only"

LOCAL_SOURCE_TYPES = frozenset({"user_upload", "user_confirmation", "internal_ppap_package"})
UPSTREAM_SOURCE_TYPES = frozenset({
    "openmes_work_order", "openmes_quality_package", "openmes_engineering_document",
    "openmes_inspection_record", "erp_sales_order", "procurement_plan",
    "quality_issue_workflow", "ship_gate",
})
KNOWN_SOURCE_TYPES = LOCAL_SOURCE_TYPES | UPSTREAM_SOURCE_TYPES | {TEST_SOURCE_TYPE}
INPUT_SOURCE_TYPES = frozenset({"user_confirmation", TEST_SOURCE_TYPE})
BUSINESS_LINK_KEYS = frozenset({
    "work_order_no", "work_order_id", "quotation_id", "erp_order_id", "issue_id",
    "quotation_approved", "work_order_batch_no",
})
_IDENTIFIER_MARKER = re.compile(r"(?i)(?<![a-z0-9])(?:test[-_]|demo[-_]|synthetic_demo_only(?:\b|$))")
_SEMANTIC_MARKER = re.compile(r"(?i)(?<![a-z0-9])(?:fixture|mock)(?![a-z0-9])")
_MODE_KEYS = frozenset({"mode", "data_mode", "task_mode"})
_TRUE_TEST_KEYS = frozenset({"synthetic_demo_only", "test_only", "demo_only", "is_test", "is_mock", "is_fixture"})
_PROVENANCE_KEYS = frozenset({"authorization_ref", "source_type", "source_ref", "source_id", "source_system", "data_source", "locator"})


def normalize_mode(mode: Any = MODE_REAL) -> str:
    value = str(mode or MODE_REAL).strip().upper()
    if value not in MODES:
        raise ValueError("任务模式必须是 REAL（正式）或 TEST（测试）")
    return value


def task_mode(task: Any) -> str:
    if isinstance(task, Mapping):
        return normalize_mode(task.get("data_mode", task.get("mode", MODE_REAL)))
    return normalize_mode(getattr(task, "data_mode", MODE_REAL))


def is_test_mode(task: Any) -> bool:
    return task_mode(task) == MODE_TEST


def mode_error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "ok": False, "code": code, "error": message}


def has_test_marker(value: Any, *, semantic: bool = False) -> bool:
    """Inspect explicit provenance markers, recursively including nested data.

    A number or a Chinese description alone cannot classify data as synthetic.
    Mode and boolean provenance fields carry explicit structured meaning.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).strip().lower()
            if name in _MODE_KEYS and isinstance(item, str) and item.strip().upper() == MODE_TEST:
                return True
            if name in _TRUE_TEST_KEYS and (item is True or str(item).strip().lower() in {"true", "1"}):
                return True
            # fixture can mean real production tooling in an operation name or
            # note. Only source provenance and identifiers give it test meaning.
            classified = semantic or name in _PROVENANCE_KEYS or name.endswith(("_id", "_no", "_ref"))
            if has_test_marker(item, semantic=classified):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(has_test_marker(item, semantic=semantic) for item in value)
    if isinstance(value, str):
        return bool(_IDENTIFIER_MARKER.search(value) or (semantic and _SEMANTIC_MARKER.search(value)))
    return False


def classify_source(source_type: Any) -> str:
    value = str(source_type or "").strip()
    if value == TEST_SOURCE_TYPE:
        return "TEST"
    if value in LOCAL_SOURCE_TYPES:
        return "LOCAL"
    if value in UPSTREAM_SOURCE_TYPES:
        return "UPSTREAM"
    return "UNKNOWN"


def validate_task_data(mode: Any, values: Mapping[str, Any]) -> dict[str, Any] | None:
    mode = normalize_mode(mode)
    if mode == MODE_TEST:
        business = values.get("business_object", values.get("business_object_json", {}))
        merged = {**dict(business if isinstance(business, Mapping) else {}), **dict(values)}
        links = sorted(key for key in BUSINESS_LINK_KEYS if merged.get(key) not in (None, "", False))
        if links:
            return {**mode_error("test_business_link_forbidden", "测试任务不能关联真实 ERP/MES 业务对象"), "fields": links}
    elif has_test_marker(values):
        return mode_error("test_data_in_real_task", "正式任务不能保存明确标记为测试、模拟或演示的数据；请创建测试任务")
    return None


def validate_mode_patch(patch: Mapping[str, Any]) -> dict[str, Any] | None:
    if any(key in patch for key in ("mode", "data_mode", "task_mode")):
        return mode_error("task_mode_immutable", "创建后不能切换任务模式；请另建任务并使用对应来源")
    return None


def _validate_source(mode: Any, source_type: Any, provenance: Any) -> dict[str, Any] | None:
    mode = normalize_mode(mode)
    classification = classify_source(source_type)
    if classification == "UNKNOWN":
        return mode_error("unknown_source_type", "不支持的来源类别；不能使用自定义来源绕过正式/测试隔离")
    if mode == MODE_TEST and classification == "UPSTREAM":
        return mode_error("test_real_source_forbidden", "测试任务只能使用本地测试资料，不能关联或读取真实 ERP/MES 来源")
    if mode == MODE_REAL and (classification == "TEST" or has_test_marker(provenance)):
        return mode_error("test_data_in_real_task", "测试、模拟或演示来源不能保存到正式任务")
    return None


def validate_input_source(mode: Any, source_type: Any, source_ref: Any, payload: Any) -> dict[str, Any] | None:
    if str(source_type or "").strip() not in INPUT_SOURCE_TYPES:
        return mode_error("unknown_input_source_type", "人工输入只接受 user_confirmation；测试任务另可使用 synthetic_demo_only。业务取证和批准包引用必须走受控证据入口")
    return _validate_source(mode, source_type, {"source_type": source_type, "source_ref": source_ref, "payload": payload})


def validate_evidence_source(mode: Any, source_type: Any, source_id: Any, locator: Any,
                             payload: Any = None) -> dict[str, Any] | None:
    return _validate_source(mode, source_type, {"source_type": source_type, "source_id": source_id,
        "locator": locator, "payload": payload})


def validate_collect(mode: Any) -> dict[str, Any] | None:
    if normalize_mode(mode) == MODE_TEST:
        return mode_error("test_real_source_forbidden", "测试任务不执行真实 ERP/MES 取证；请登记本地测试资料")
    return None


def validate_customer_state(mode: Any) -> dict[str, Any] | None:
    if normalize_mode(mode) == MODE_TEST:
        return mode_error("test_customer_state_forbidden", "测试任务可本地审核归档，不能登记正式客户提交、接受或退回状态")
    return None
