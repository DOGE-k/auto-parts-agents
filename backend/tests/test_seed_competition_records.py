"""Offline checks for competition data import; no live system is contacted."""
from __future__ import annotations

import pytest
import httpx
import json
from datetime import date

import seed_competition_records as seed


CASE = seed.CASES[0]
CUSTOMER = "上汽集团"


class NoWriteApi:
    def call(self, *args, **kwargs):
        raise AssertionError("existing data must not be written")


def erp_order(**changes):
    return {
        "name": "SAL-ORD-2026-00091", "po_no": CASE.po_no,
        "customer": CUSTOMER, "delivery_date": CASE.delivery_date,
        "docstatus": 0,
        "items": [{"item_code": CASE.item_code, "qty": CASE.quantity}],
        **changes,
    }


def test_existing_erp_order_is_reused_without_write():
    erp = seed.Erp.__new__(seed.Erp)
    erp.api = NoWriteApi()
    erp.find_order = lambda case: erp_order()
    assert erp.ensure_order(CASE, CUSTOMER, "AutoParts Manufacturing", "Stores - APM",
                            apply=True) == "SAL-ORD-2026-00091"


def test_existing_active_fiscal_year_is_reused_without_write():
    erp = seed.Erp.__new__(seed.Erp)
    erp.api = NoWriteApi()
    year = date.today().year
    erp.list = lambda *args: [{"name": str(year), "year_start_date": f"{year}-01-01",
                               "year_end_date": f"{year}-12-31", "disabled": 0}]
    erp.get = lambda *args: {"companies": [{"company": "AutoParts Manufacturing"}]}
    assert erp.ensure_fiscal_year("AutoParts Manufacturing", apply=True) == str(year)


def test_missing_fiscal_year_is_created_for_target_company():
    erp = seed.Erp.__new__(seed.Erp)
    erp.list = lambda *args: []
    calls = []
    year = date.today().year

    class CreateApi:
        def call(self, method, path, *, body=None):
            calls.append((method, path, body))
            return {"data": {"name": str(year)}}

    erp.api = CreateApi()
    erp.get = lambda *args: {"disabled": 0, "year_start_date": f"{year}-01-01",
                             "year_end_date": f"{year}-12-31",
                             "companies": [{"company": "AutoParts Manufacturing"}]}
    assert erp.ensure_fiscal_year("AutoParts Manufacturing", apply=True) == str(year)
    assert calls[0][1] == "api/resource/Fiscal%20Year"
    assert calls[0][2]["companies"] == [{"company": "AutoParts Manufacturing"}]


def test_existing_erp_order_with_different_content_stops_without_write():
    erp = seed.Erp.__new__(seed.Erp)
    erp.api = NoWriteApi()
    erp.find_order = lambda case: erp_order(items=[{"item_code": CASE.item_code, "qty": 999}])
    with pytest.raises(seed.SeedError, match="不会覆盖"):
        erp.ensure_order(CASE, CUSTOMER, "AutoParts Manufacturing", "Stores - APM",
                         apply=True)


def test_new_erp_record_is_draft_and_read_back_before_use():
    calls = []
    erp = seed.Erp.__new__(seed.Erp)
    erp.find_order = lambda case: None
    erp.get = lambda doctype, name: erp_order()

    class CreateApi:
        def call(self, method, path, *, body=None):
            calls.append((method, path, body))
            return {"data": {"name": "SAL-ORD-2026-00091"}}

    erp.api = CreateApi()
    assert erp.ensure_order(CASE, CUSTOMER, "AutoParts Manufacturing", "Stores - APM",
                            apply=True) == "SAL-ORD-2026-00091"
    method, path, body = calls[0]
    assert (method, path) == ("POST", "api/resource/Sales%20Order")
    assert body["docstatus"] == 0
    assert body["po_no"] == CASE.po_no
    assert body["items"][0]["warehouse"] == "Stores - APM"


def test_existing_mes_order_requires_exact_erp_link_and_is_not_overwritten():
    mes = seed.Mes.__new__(seed.Mes)
    mes.api = NoWriteApi()
    mes.find_work_order = lambda case: {
        "id": 91, "order_no": CASE.work_order_no,
        "planned_qty": CASE.quantity, "customer_order_no": "OTHER-ORDER",
    }
    with pytest.raises(seed.SeedError, match="不会覆盖"):
        mes.ensure_work_order(CASE, "SAL-ORD-2026-00091", apply=True)


def test_new_mes_order_uses_actual_erp_id_and_skip_existing():
    mes = seed.Mes.__new__(seed.Mes)
    calls = []
    found = iter([None, {"id": 91, "order_no": CASE.work_order_no,
                        "customer_order_no": "SAL-ORD-2026-00091", "planned_qty": CASE.quantity}])
    mes.find_work_order = lambda case: next(found)

    class ImportApi:
        def call(self, method, path, *, body=None, headers=None):
            calls.append((method, path, body))
            return {"data": {"imported": 1, "updated": 0, "errors": []}}

    mes.api = ImportApi()
    mes.erp_key = "FIXTURE-NOT-A-REAL-KEY"
    assert mes.ensure_work_order(CASE, "SAL-ORD-2026-00091", apply=True) == 91
    assert calls[0][2]["strategy"] == "skip_existing"
    assert calls[0][2]["orders"][0]["customer_order_no"] == "SAL-ORD-2026-00091"


def test_mes_line_is_created_once_and_read_back():
    mes = seed.Mes.__new__(seed.Mes)
    mes.token = "FIXTURE-NOT-A-REAL-TOKEN"
    rows = []
    methods = []

    class LineApi:
        def call(self, method, path, *, body=None, headers=None):
            methods.append(method)
            if method == "POST":
                assert body == {"code": seed.LINE_CODE, "name": "汽车零部件装配线", "is_active": True}
                rows.append({"code": seed.LINE_CODE, "is_active": True})
            return {"data": list(rows)}

    mes.api = LineApi()
    assert mes.ensure_line(apply=True) is True
    assert mes.ensure_line(apply=True) is True
    assert methods == ["GET", "POST", "GET", "GET"]


def test_existing_ppap_task_with_conflicting_order_is_not_overwritten():
    ppap = seed.Ppap.__new__(seed.Ppap)
    ppap.find_task = lambda case, customer: {"task_id": "PPAP-91"}

    class ReadApi:
        def call(self, method, path, **kwargs):
            assert method == "GET"
            return {"task": {"authorization_ref": CASE.authorization_ref,
                             "data_mode": "REAL", "customer_id": CUSTOMER,
                             "business_object": {"erp_order_id": "OTHER-ORDER"}}}

    ppap.api = ReadApi()
    with pytest.raises(seed.SeedError, match="不会覆盖"):
        ppap.ensure_task(CASE, CUSTOMER, "SAL-ORD-2026-00091", 91, apply=True)


def test_new_ppap_task_is_linked_without_approving_or_compiling():
    ppap = seed.Ppap.__new__(seed.Ppap)
    ppap.find_task = lambda case, customer: None
    calls = []
    business_object = {}

    class WriteApi:
        def call(self, method, path, *, body=None):
            calls.append((method, path, body))
            if path == "api/ppap/tasks" and method == "POST":
                return {"task": {"task_id": "PPAP-91"}}
            if path == "api/ppap/tasks/PPAP-91" and method == "POST":
                business_object.update(body["patch"])
                return {"task": {"task_id": "PPAP-91"}}
            return {"task": {"task_id": "PPAP-91", "authorization_ref": CASE.authorization_ref,
                             "data_mode": "REAL", "customer_id": CUSTOMER,
                             "business_object": dict(business_object)}}

    ppap.api = WriteApi()
    assert ppap.ensure_task(CASE, CUSTOMER, "SAL-ORD-2026-00091", 91, apply=True) == "PPAP-91"
    assert calls[0][2]["mode"] == "REAL"
    assert business_object == {"erp_order_id": "SAL-ORD-2026-00091",
                               "work_order_no": CASE.work_order_no, "work_order_id": "91"}
    assert not any("approve" in path or "compile" in path for _, path, _ in calls)


@pytest.mark.parametrize("url", ["https://example.com", "http://129.204.33.138:8080",
                                 "http://user:pass@127.0.0.1:8080", "http://127.0.0.1:8080/api"])
def test_import_only_targets_local_services(url):
    with pytest.raises(seed.SeedError):
        seed.local_base(url, "fixture")


def test_api_requests_json_and_redacts_redirect_query():
    seen = []

    def handler(request):
        seen.append(request.headers.get("Accept"))
        return httpx.Response(302, headers={"Location": "/login?token=do-not-log"})

    api = seed.Api("http://127.0.0.1", "OpenMES")
    default_headers = dict(api.client.headers)
    api.client.close()
    api.client = httpx.Client(base_url="http://127.0.0.1/", headers=default_headers,
                              transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        with pytest.raises(seed.SeedError) as error:
            api.call("GET", "api/v1/work-orders")
        assert seen == ["application/json"]
        assert "/login" in str(error.value)
        assert "do-not-log" not in str(error.value)
    finally:
        api.close()


def test_erp_validation_hint_extracts_message_without_secret(monkeypatch):
    monkeypatch.setenv("ERPNEXT_API_SECRET", "private-value")
    response = httpx.Response(417, json={"_server_messages": json.dumps([
        json.dumps({"message": "<b>缺少价格表</b> private-value"})
    ])})
    assert seed.erp_validation_hint(response) == "缺少价格表 [隐藏]"
