"""Bounded, complete JSON list context; final data remains an isolated fixture."""
from __future__ import annotations

import copy
import json

import pytest

from app.persistence.database import SessionLocal
from app.persistence.models import RealAgentRunRow
from app.services import coordinator as module
from app.services.coordinator import BusinessCoordinator, _model_tool_content, _summarize_result


def work_orders(count=18, *, limit=100):
    return {
        "orders": [{
            "work_order_id": str(i), "work_order_no": f"WO-2026-{i:05d}",
            "customer_order_no": f"SAL-ORD-2026-{i:05d}",
            "product_id": "BD-2401", "product_name": "制动盘-前轮 Brake Disc Front",
            "quantity": "500.00", "completed_qty": "0.00", "rejected_qty": "0.00",
            "status": "ACCEPTED", "priority": 5,
            "due_date": "2026-11-17T23:00:00.000000Z",
            "line_id": "1", "line_name": "Assembly Line 01",
            "planned_start": "2026-11-01T00:00:00.000000Z",
            "planned_end": "2026-11-17T23:00:00.000000Z",
            "actual_start": "", "product_external_code": "BD-2401",
            "product_external_system": "erpnext",
            "authority": "OpenMES", "data_source": "openmes_api",
            "unneeded_description": "不属于列表必需字段的冗长源描述" * 50,
        } for i in range(1, count + 1)],
        "count": count, "limit": limit, "possibly_truncated": count >= limit,
        "adapter_mode": "real", "authority": "OpenMES", "data_source": "openmes_api",
    }


def sales_orders(count=18, *, limit=100):
    return {
        "orders": [{
            "order_id": f"SAL-ORD-2026-{i:05d}", "customer_id": "CUST-001",
            "customer_name": "客户甲", "transaction_date": "2026-10-01",
            "delivery_date": "2026-11-18", "status": "Draft", "total_qty": "500.00",
            "total": "42500.00", "currency": "CNY",
            "authority": "ERPNext", "data_source": "erpnext_api",
        } for i in range(1, count + 1)],
        "count": count, "limit": limit, "possibly_truncated": count >= limit,
        "adapter_mode": "real", "authority": "ERPNext", "data_source": "erpnext_api",
    }


@pytest.mark.parametrize("skill,factory,id_key", [
    ("tracking.list_real_work_orders", work_orders, "work_order_no"),
    ("quotation.list_real_sales_orders", sales_orders, "order_id"),
])
def test_all_18_records_fit_valid_context_with_exact_fields_and_source_metadata(skill, factory, id_key):
    source = factory()
    original = copy.deepcopy(source)
    assert len(json.dumps(source, ensure_ascii=False)) > 4000
    with pytest.raises(json.JSONDecodeError):
        json.loads(_summarize_result(source, limit=4000))

    text = _model_tool_content(skill, source)
    payload = json.loads(text)
    rows = [dict(zip(payload["columns"], row)) for row in payload["rows"]]
    assert len(text) <= module.LIST_TOOL_CONTEXT_LIMIT
    assert len(rows) == payload["count"] == payload["context_included_count"] == 18
    assert payload["context_omitted_count"] == 0
    assert [row[id_key] for row in rows] == [row[id_key] for row in source["orders"]]
    for key in ("limit", "possibly_truncated", "authority", "data_source", "adapter_mode"):
        assert payload[key] == source[key]
    for original_row, compact_row in zip(source["orders"], rows):
        assert all(compact_row[key] == original_row[key] for key in payload["columns"])
    assert "不是数据库总数" in payload["count_scope"]
    assert "total_count" not in payload and "next_page" not in payload
    assert source == original
    if id_key == "work_order_no":
        assert rows[0]["completed_qty"] == "0.00"
        assert rows[0]["due_date"] == "2026-11-17T23:00:00.000000Z"
        assert "unneeded_description" not in payload["columns"]
    else:
        assert rows[0]["total"] == "42500.00"  # amount, never a record count
        assert "product_id" not in payload["columns"]


@pytest.mark.parametrize("source_truncated", [False, True])
def test_budget_omits_only_whole_rows_and_preserves_distinct_source_limit_semantics(source_truncated):
    source = work_orders(100, limit=100)
    source["possibly_truncated"] = source_truncated
    original = copy.deepcopy(source)
    text = _model_tool_content("tracking.list_real_work_orders", source)
    payload = json.loads(text)
    included = payload["context_included_count"]
    assert 0 < included < 100
    assert len(text) <= module.LIST_TOOL_CONTEXT_LIMIT
    assert len(payload["rows"]) == included
    assert payload["context_omitted_count"] == 100 - included
    assert payload["count"] == payload["limit"] == 100
    assert payload["possibly_truncated"] is source_truncated
    column = payload["columns"].index("work_order_no")
    assert [row[column] for row in payload["rows"]] == [row["work_order_no"] for row in source["orders"][:included]]
    assert all(len(row) == len(payload["columns"]) for row in payload["rows"])
    assert source == original


def test_source_ignoring_limit_cannot_expand_model_context_without_bound():
    source = work_orders(250, limit=1)
    payload_text = _model_tool_content("tracking.list_real_work_orders", source)
    payload = json.loads(payload_text)
    assert len(payload_text) <= module.LIST_TOOL_CONTEXT_LIMIT
    assert payload["count"] == 250 and payload["limit"] == 1
    assert payload["possibly_truncated"] is True
    assert payload["context_included_count"] + payload["context_omitted_count"] == 250
    assert payload["context_omitted_count"] > 0


def test_one_oversized_record_is_omitted_whole_without_cutting_its_id_or_value():
    source = work_orders(2)
    source["orders"][0]["product_name"] = "长产品名称" * module.LIST_TOOL_CONTEXT_LIMIT
    payload = json.loads(_model_tool_content("tracking.list_real_work_orders", source))
    assert payload["rows"] == []
    assert payload["context_included_count"] == 0 and payload["context_omitted_count"] == 2
    assert source["orders"][0]["work_order_no"] == "WO-2026-00001"


@pytest.mark.parametrize("source", [work_orders(0), sales_orders(0)])
def test_empty_list_is_successful_zero_batch_not_a_remote_failure(source):
    payload = json.loads(_model_tool_content("tracking.list_real_work_orders", source))
    assert payload["count"] == 0 and payload["rows"] == []
    assert payload["context_included_count"] == payload["context_omitted_count"] == 0
    assert payload["possibly_truncated"] is False and "error" not in payload


def test_mixed_provenance_is_retained_and_missing_fields_are_not_guessed():
    source = work_orders(2)
    source["orders"][1]["authority"] = "Other MES source"
    del source["orders"][1]["completed_qty"]
    payload = json.loads(_model_tool_content("tracking.list_real_work_orders", source))
    rows = [dict(zip(payload["columns"], row)) for row in payload["rows"]]
    assert rows[0]["authority"] == "OpenMES" and rows[1]["authority"] == "Other MES source"
    assert rows[1]["completed_qty"] is None


def test_short_failures_and_other_skill_context_keep_existing_contract():
    error = {"error": "HTTP Error: 401 - project_login_required", "note": "query failed"}
    assert json.loads(_model_tool_content("tracking.list_real_work_orders", error)) == error
    result = {"quality_gate_passed": False, "evidence": ["record-1"]}
    assert _model_tool_content("quality.assess_quality_impact", result) == _summarize_result(result, limit=4000)


def test_partial_error_metadata_is_preserved_instead_of_reported_as_success():
    source = work_orders(1)
    source.update(error="second source unavailable", status="partial", note="verify source before using")
    payload = json.loads(_model_tool_content("tracking.list_real_work_orders", source))
    assert payload["error"] == source["error"]
    assert payload["status"] == "partial" and payload["note"] == source["note"]


def test_oversized_source_metadata_still_produces_bounded_valid_json():
    source = work_orders(1)
    source["authority"] = "oversized authority" * module.LIST_TOOL_CONTEXT_LIMIT
    text = _model_tool_content("tracking.list_real_work_orders", source)
    payload = json.loads(text)
    assert len(text) <= module.LIST_TOOL_CONTEXT_LIMIT
    assert payload["context_error"] == "list_metadata_exceeds_context_budget"
    assert payload["source_has_error"] is False
    assert payload["context_included_count"] == 0 and payload["context_omitted_count"] == 1
    assert payload["rows"] == []


@pytest.mark.asyncio
async def test_coordinator_passes_both_complete_18_row_batches_to_model_without_changing_trace():
    sources = {"tracking.list_real_work_orders": work_orders(), "quotation.list_real_sales_orders": sales_orders()}
    originals = copy.deepcopy(sources)

    class Model:
        async def chat_completion(self, messages, **kwargs):
            tool_messages = [message for message in messages if message["role"] == "tool"]
            if not tool_messages:
                return {"choices": [{"message": {"role": "assistant", "tool_calls": [{
                    "id": skill, "type": "function", "function": {
                        "name": skill.replace(".", "__"), "arguments": '{"limit":100}',
                    },
                } for skill in sources]}}]}
            assert len(tool_messages) == 2
            for message in tool_messages:
                payload = json.loads(message["content"])
                assert len(payload["rows"]) == payload["context_included_count"] == 18
                assert payload["context_omitted_count"] == 0
                assert payload["count"] == 18 and payload["limit"] == 100
                assert payload["possibly_truncated"] is False
            return {"choices": [{"message": {"role": "assistant", "content": "本次返回18条工单及18条销售订单，数据库总数未提供。"}}]}

        async def aclose(self):
            pass

    async def invoke(agent, skill, arguments):
        assert arguments == {"limit": 100}
        return sources[skill]

    coordinator = BusinessCoordinator(Model(), skill_invoker=invoke)
    try:
        result = await coordinator.ask("查一下数据库里现在有的工单")
    finally:
        await coordinator.aclose()
    assert all(step["status"] == "ok" for step in result["call_chain"])
    assert sources == originals
    for step in result["call_chain"]:
        assert step["result_summary"] == _summarize_result(sources[step["skill_id"]])
    with SessionLocal() as db:
        run = db.get(RealAgentRunRow, result["coordination_run_id"])
        assert run.result_json["call_chain"] == result["call_chain"]
