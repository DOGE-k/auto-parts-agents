"""Create explicitly synthetic PPAP tasks through the authenticated local API.

No database writes, ERP/MES calls, fabricated original documents, rule changes,
approvals or customer decisions. Existing seed tasks are never overwritten.
Credentials and session cookies stay in memory and are never included in output.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx


BACKEND_ROOT = Path(__file__).resolve().parent
TEST_NOTICE = "合成测试数据；非真实工厂测量、非客户原件，不用于客户提交"
SOURCE_TYPE = "synthetic_demo_only"
_ID = r"[A-Za-z0-9_-]+"
_ROUTES = {
    "GET": re.compile(rf"/api/ppap/(?:templates|tasks|tasks/{_ID}(?:/(?:versions|issues|runs|inputs|missing-items)|/artifacts/{_ID}/download)?)"),
    "POST": re.compile(rf"/api/ppap/tasks(?:/{_ID}/(?:applicability|inputs|compile|checks))?"),
}


class SeedError(RuntimeError):
    """A safe error which never includes request bodies, headers or credentials."""


def checked_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise SeedError("种子脚本只允许本机 HTTP 后端地址，例如 http://127.0.0.1:9000")
    return value.rstrip("/")


def checked_label(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", value):
        raise SeedError("批次标签只能包含字母、数字、下划线和短横线，最长40字符")
    return value.upper()


def task_fields(case: str, label: str) -> dict:
    side, suffix, name = {
        "left": ("LEFT", "LH", "左侧A柱饰板：超差与缺值"),
        "right": ("RIGHT", "RH", "右侧A柱饰板：编制与版本"),
        "missing": ("LEFT", "MISSING", "资料缺项与适用性检查"),
    }[case]
    return {
        "mode": "TEST",
        "customer_id": f"TEST-PPAP-CUSTOMER-{label}",
        "customer_name": "[合成测试] 非真实客户",
        "part_no": f"TEST-PPAP-{label}-{suffix}",
        "part_name": f"[合成测试] {name}",
        "part_side": side,
        "drawing_version": "TEST-A",
        "engineering_change_level": "TEST-E1",
        "submission_reason": TEST_NOTICE,
        "submission_level": "TEST-ONLY（非客户提交等级）",
        "authorization_ref": f"TEST-PPAP-SEED-V1:{label}；用户授权功能测试；{SOURCE_TYPE}",
    }


def synthetic_inputs(fields: dict, label: str) -> list[dict]:
    """Deterministic, independently identified records for all 13 input datasets."""
    side = fields["part_side"]
    nominal = 100.0 if side == "LEFT" else 102.0
    gap = 5.0 if side == "LEFT" else 6.0
    records: list[dict] = []

    def add(dataset: str, key: str, payload: dict) -> None:
        records.append({
            "dataset": dataset, "record_key": f"TEST-{side}-{key}", "part_side": side,
            "source_type": SOURCE_TYPE,
            "source_ref": f"TEST-PPAP-SEED-V1:{label}/{side}/{key}；{TEST_NOTICE}",
            "payload": payload,
        })

    add("product_base", "BASE", {key: fields[key] for key in (
        "part_no", "part_name", "customer_name", "drawing_version", "engineering_change_level")}
        | {"revision_note": TEST_NOTICE})
    for sequence, name in ((10, "成型"), (20, "修边"), (30, "尺寸检测")):
        add("process_operation", f"OP-{sequence}", {
            "op_id": f"TEST-OP-{sequence}", "op_name": f"[合成测试] {name}",
            "sequence": sequence, "station": f"TEST-STATION-{sequence}", "description": TEST_NOTICE})
    for first, second in ((10, 20), (20, 30)):
        add("process_relation", f"REL-{first}-{second}", {
            "from_op": f"TEST-OP-{first}", "to_op": f"TEST-OP-{second}",
            "relation_type": "sequence", "note": TEST_NOTICE})
    for identifier, name, center, half_range in (
            ("TEST-CHAR-LENGTH", "安装长度", nominal, 0.3),
            ("TEST-CHAR-GAP", "装配间隙", gap, 0.2)):
        add("characteristic", identifier, {
            "characteristic_id": identifier, "name": f"[合成测试] {name}",
            "characteristic_type": "product", "special": True, "unit": "mm",
            "lsl": round(center - half_range, 3), "usl": round(center + half_range, 3),
            "nominal": center, "drawing_location": "TEST-ONLY", "note": TEST_NOTICE})
        values = [center - 0.04, center + 0.08, center + 0.05]
        if side == "LEFT":
            values[2] = center + 0.65 if identifier.endswith("LENGTH") else None
        for sample, value in enumerate(values, start=1):
            payload = {"characteristic_id": identifier, "point": f"TEST-{side}-{identifier}-{sample}",
                       "value": None if value is None else round(value, 3), "unit": "mm",
                       "sample_no": f"TEST-{side}-S{sample}", "measured_by": "SYNTHETIC-NOT-A-PERSON"}
            if value is None:
                payload["missing_reason"] = "故意保留的合成空值，用于测试缺项提示"
            add("inspection_value", f"DIM-{identifier}-{sample}", payload)
    measurements = [{"appraiser": f"TEST-APPRAISER-{operator + 1}", "parts": [
        {"part": f"TEST-SAMPLE-{part + 1}", "trials": [
            round(nominal - 0.18 + part * 0.04 + operator * 0.003 + repeat * 0.002, 4)
            for repeat in range(3)]} for part in range(10)]} for operator in range(3)]
    add("msa_study", "MSA-01", {"study_id": f"TEST-MSA-{side}",
        "characteristic_id": "TEST-CHAR-LENGTH", "unit": "mm", "method": "average_range",
        "tolerance": 0.6, "measurements": measurements, "note": TEST_NOTICE})
    subgroups = [[round(nominal + drift + offset, 3) for offset in (-0.08, -0.04, 0.0, 0.04, 0.08)]
                 for drift in (0.0, 0.01, -0.01, 0.02, -0.02)]
    add("process_study", "PROCESS-01", {"study_id": f"TEST-PROCESS-{side}",
        "characteristic_id": "TEST-CHAR-LENGTH", "unit": "mm", "lsl": nominal - 0.3,
        "usl": nominal + 0.3, "target": nominal,
        "values": [value for group in subgroups for value in group], "subgroups": subgroups,
        "sampling_note": "25个合成样本、5个子组；仅测试计算，不证明真实过程稳定性", "note": TEST_NOTICE})
    add("material_evidence", "MATERIAL-01", {"test_item": "[合成测试] 材料记录汇总",
        "supplier": "[合成测试] 非真实供应商", "lot_no": f"TEST-LOT-{side}",
        "report_no": f"TEST-REPORT-{side}", "spec_ref": "TEST-ONLY-NO-CUSTOMER-SPEC", "note": TEST_NOTICE})
    add("packaging_equipment", "PACKAGE-01", {"item": "[合成测试] 周转箱", "quantity": 2,
        "owner": "SYNTHETIC-NOT-A-PERSON", "note": TEST_NOTICE})
    add("customer_requirement", "REQUIREMENT-01", {"requirement_text":
        "所有表格保留合成测试标识，不填供方签字与客户批准，不作为正式提交资料",
        "source": "TEST-ONLY", "clause": "TEST-CLAUSE-01", "note": TEST_NOTICE})
    add("change_record", "CHANGE-01", {"change_description": "[合成测试] 测试图纸版本登记",
        "change_no": "TEST-CHANGE-01", "level": "TEST-E1", "reason": TEST_NOTICE})
    add("physical_record", "SAMPLE-01", {"record_type": "sample_production_part", "quantity": 3,
        "storage_location": "TEST-ONLY（无真实实物）", "note": TEST_NOTICE})
    add("customer_handling", "HANDLING-01", {"instruction": TEST_NOTICE,
        "stage": "TEST-LOCAL-COMPILATION", "note": "TEST-VERSION-1"})
    return records


def applicability(case: str, element_ids: list[str]) -> list[dict]:
    decisions = []
    for element_id in element_ids:
        if case == "left":
            requirement = "submit"
            basis = f"合成测试约定：观察全部条目的缺项与阻断；{TEST_NOTICE}"
        else:
            requirement = "on_request"
            if element_id in ("submission_checklist", "psw", "attachment_index"):
                requirement = "submit"
            elif element_id in ("msa", "initial_process_study"):
                requirement = "retain"
            basis = ("合成测试范围：只验证本地编制，不模拟客户索取或真实原件；"
                     f"此设置不代表客户PPAP适用性规则；{TEST_NOTICE}")
        decisions.append({"element_id": element_id, "requirement": requirement, "basis": basis})
    return decisions


class SeedApi:
    def __init__(self, base_url: str, *, transport=None):
        self.base_url = checked_base_url(base_url)
        self.client = httpx.Client(base_url=self.base_url, timeout=120, trust_env=False,
                                   follow_redirects=False, transport=transport)
        self.logged_in = False

    def request(self, method: str, path: str, *, body=None, params=None) -> dict:
        # The script cannot accidentally call any ERP/MES or approval endpoint.
        if method not in _ROUTES or not _ROUTES[method].fullmatch(path):
            raise SeedError("请求超出PPAP种子范围")
        response = self.client.request(method, path, json=body, params=params)
        if response.is_error or response.is_redirect:
            try:
                detail = response.json().get("detail", {})
                code = detail.get("code", "request_failed") if isinstance(detail, dict) else "request_failed"
            except (ValueError, AttributeError):
                code = "request_failed"
            code = re.sub(r"[^a-zA-Z0-9_-]", "", str(code))[:60]
            raise SeedError(f"PPAP请求失败 HTTP {response.status_code} ({code})")
        result = response.json()
        if not isinstance(result, dict) or result.get("success") is False or result.get("ok") is False:
            raise SeedError("PPAP接口返回业务失败")
        return result

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise SeedError("项目登录配置缺失；请在本机配置项目账号，脚本不接收命令行密码")
        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        if response.status_code != 200:
            raise SeedError(f"项目登录失败 HTTP {response.status_code}；请确认本机项目账号配置")
        self.logged_in = True
        identity = response.json().get("identity", {})
        if identity.get("provider") != "project" or "quality_manager" not in identity.get("roles", []):
            raise SeedError("当前项目账号没有quality_manager权限")

    def close(self) -> None:
        try:
            if self.logged_in:
                self.client.post("/api/auth/logout")
        finally:
            self.client.close()


def download_verified(api: SeedApi, task_id: str, artifact: dict, output_dir: Path) -> Path:
    artifact_id = artifact["artifact_id"]
    route = f"/api/ppap/tasks/{task_id}/artifacts/{artifact_id}/download"
    if not _ROUTES["GET"].fullmatch(route) or not isinstance(artifact["version"], int):
        raise SeedError("下载任务或产物标识不合法")
    response = api.client.get(route)
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        raise SeedError("草稿下载失败或返回内容不是工作簿")
    if hashlib.sha256(response.content).hexdigest() != artifact["content_hash"]:
        raise SeedError("下载文件与产物指纹不一致")
    name = f"{task_id}-v{artifact['version']}.xlsx"
    path = output_dir / name
    path.write_bytes(response.content)
    return path


def verify_detail(detail: dict, case: str) -> dict:
    task = detail["task"]
    if task["package_state"] != "NOT_SUBMITTED" or task["customer_state"] != "NOT_SUBMITTED":
        raise SeedError("测试任务已进入审核或客户流程，停止修改")
    if case == "missing":
        return {"input_count": len(detail.get("inputs", []))}
    records = detail["inputs"]
    if {record["dataset"] for record in records} != {
            "product_base", "process_operation", "process_relation", "characteristic", "inspection_value",
            "msa_study", "process_study", "material_evidence", "packaging_equipment", "customer_requirement",
            "change_record", "physical_record", "customer_handling"}:
        raise SeedError("回读输入没有覆盖13类数据集")
    if any(record["part_side"] != task["part_side"] or record["source_type"] != SOURCE_TYPE for record in records):
        raise SeedError("回读发现侧别或测试来源不一致")
    elements = {item["element_id"]: item["content"] for item in detail["elements"]}
    if elements["psw"]["signature"] is not None or elements["psw"]["customer_approval"] is not None:
        raise SeedError("测试草稿不应包含签字或客户批准")
    counts = Counter(row["result"] for row in elements["dimensional_results"]["rows"])
    if case == "left" and (counts["NG"] != 1 or counts["MISSING"] != 1):
        raise SeedError("尺寸超差/空值未按预期保留")
    if case == "right" and (counts["NG"] or counts["MISSING"]):
        raise SeedError("右件独立尺寸出现非预期超差或空值")
    calculations = {name: elements[name]["studies"][0]["result"] for name in ("msa", "initial_process_study")}
    if (calculations["msa"]["state"] != "OK"
            or calculations["initial_process_study"]["state"] not in ("OK", "INCOMPLETE")
            or any(not isinstance(calculations["initial_process_study"]["value"].get(metric), (int, float))
                   for metric in ("cp", "cpk", "pp", "ppk"))):
        raise SeedError("合成MSA或过程研究未生成预期计算结果")
    return {"input_count": len(records), "dimensional_counts": dict(counts),
            "msa": calculations["msa"]["value"], "process_capability": calculations["initial_process_study"]["value"],
            "calculation_states": {key: value["state"] for key, value in calculations.items()}}


def summarize_case(api: SeedApi, case: str, fields: dict, task_id: str, output_dir: Path,
                   *, reused_existing: bool = False) -> dict:
    detail = api.request("GET", f"/api/ppap/tasks/{task_id}")
    versions = api.request("GET", f"/api/ppap/tasks/{task_id}/versions")
    issues = api.request("GET", f"/api/ppap/tasks/{task_id}/issues", params={"limit": 200})
    runs = api.request("GET", f"/api/ppap/tasks/{task_id}/runs")
    downloaded = [str(download_verified(api, task_id, artifact, output_dir)) for artifact in versions["items"]]
    verified = ({"input_count": len(detail.get("inputs", []))} if reused_existing
                else verify_detail(detail, case))
    return {"case": case, "task_id": task_id, "part_no": fields["part_no"], "part_name": fields["part_name"],
            "state": detail["task"]["run_state"], "versions": versions["total"], "issues": issues["total"],
            "blocking_issues": sum(bool(item["blocks_approval"]) for item in issues["items"]),
            "runs": len(runs["items"]), "idempotence_verified": not reused_existing,
            "reused_existing": reused_existing, "downloaded_files": downloaded, **verified}


def seed_case(api: SeedApi, case: str, label: str, elements: list[str], output_dir: Path) -> dict:
    fields = task_fields(case, label)
    matches = api.request("GET", "/api/ppap/tasks", params={"part_no": fields["part_no"], "limit": 100})
    if matches["total"]:
        rows = matches["items"]
        if len(rows) != 1 or rows[0].get("authorization_ref") != fields["authorization_ref"]:
            raise SeedError("同号任务不是本批种子，停止避免修改已有资料")
        return summarize_case(api, case, fields, rows[0]["task_id"], output_dir, reused_existing=True)
    created = api.request("POST", "/api/ppap/tasks", body=fields)
    task_id = created["task"]["task_id"]
    if case != "missing":
        api.request("POST", f"/api/ppap/tasks/{task_id}/applicability", body={
            "decisions": applicability(case, elements), "note": TEST_NOTICE})
        for record in synthetic_inputs(fields, label):
            api.request("POST", f"/api/ppap/tasks/{task_id}/inputs", body=record)
    compiled = api.request("POST", f"/api/ppap/tasks/{task_id}/compile", body={"note": TEST_NOTICE})
    expected = "COMPILED" if case == "right" else "BLOCKED"
    if compiled["run"]["status"] != expected:
        raise SeedError(f"{case}任务编制状态不符合测试预期；保留实际任务便于排查")
    first = compiled["artifact"]
    downloaded = [str(download_verified(api, task_id, first, output_dir))]
    repeated = api.request("POST", f"/api/ppap/tasks/{task_id}/compile", body={"note": TEST_NOTICE})
    if not repeated.get("idempotent_replay") or repeated["artifact"]["artifact_id"] != first["artifact_id"]:
        raise SeedError("相同输入重复编制产生了新产物")
    initial_detail = api.request("GET", f"/api/ppap/tasks/{task_id}")
    if case == "left":
        before = len(initial_detail["inputs"])
        invalid = {"dataset": "inspection_value", "part_side": "RIGHT", "record_key": "TEST-WRONG-SIDE",
            "source_type": SOURCE_TYPE, "source_ref": TEST_NOTICE,
            "payload": {"characteristic_id": "TEST-CHAR-LENGTH", "point": "TEST-WRONG-SIDE", "value": 999, "unit": "mm"}}
        response = api.client.post(f"/api/ppap/tasks/{task_id}/inputs", json=invalid)
        if response.status_code != 422 or response.json().get("detail", {}).get("code") != "side_conflict":
            raise SeedError("左右件冲突未被API拒绝")
        if len(api.request("GET", f"/api/ppap/tasks/{task_id}")["inputs"]) != before:
            raise SeedError("被拒绝的跨侧输入仍然落库")
    if case == "right":
        handling = next(record for record in synthetic_inputs(fields, label) if record["dataset"] == "customer_handling")
        handling["payload"]["note"] = "TEST-VERSION-2：测试输入变化产生新版本；并非客户要求变更"
        api.request("POST", f"/api/ppap/tasks/{task_id}/inputs", body=handling)
        second = api.request("POST", f"/api/ppap/tasks/{task_id}/compile", body={"note": TEST_NOTICE})
        if second["artifact"]["version"] != first["version"] + 1:
            raise SeedError("修改输入后没有产生下一版本")
        downloaded.append(str(download_verified(api, task_id, second["artifact"], output_dir)))
        download_verified(api, task_id, first, output_dir)  # Old draft bytes must still match its hash.
        compiled = second
    return summarize_case(api, case, fields, task_id, output_dir)


def default_label() -> str:
    """Return the default seed label, never failing when tzdata is unavailable.

    The IANA zone database is an OS or pip extra: minimal images and some CI
    runners ship without it. A missing zone must not abort argument parsing, so
    fall back to UTC while keeping the same YYYYMMDD shape callers expect.
    """
    try:
        zone = ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.now(zone).strftime("%Y%m%d")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="通过正常项目登录创建PPAP合成测试种子（不会覆盖已有任务）")
    parser.add_argument("--api-base", default="http://127.0.0.1:9000")
    parser.add_argument("--label", default=default_label())
    args = parser.parse_args(argv)
    api = None
    try:
        label = checked_label(args.label)
        base = checked_base_url(args.api_base)
        from dotenv import load_dotenv
        load_dotenv(BACKEND_ROOT.parent / ".env", override=False)
        load_dotenv(BACKEND_ROOT / ".env", override=False)
        output_dir = BACKEND_ROOT / "data" / "ppap_seed" / label
        output_dir.mkdir(parents=True, exist_ok=True)
        api = SeedApi(base)
        api.login(os.getenv("PROJECT_AUTH_USERNAME", "admin").strip(),
                  os.getenv("PROJECT_AUTH_PASSWORD", "").strip())
        template = api.request("GET", "/api/ppap/templates")
        element_ids = [item["element_id"] for item in template["template"]["elements"]]
        if len(element_ids) != 22:
            raise SeedError("模板已变化，请先核对种子范围")
        # Probe the real anonymous gate; this request must never persist a task.
        response = httpx.post(base + "/api/ppap/tasks", json=task_fields("missing", label), timeout=20, trust_env=False)
        if response.status_code != 401:
            raise SeedError("匿名创建没有被拒绝，停止种子写入")
        report = {"label": label, "notice": TEST_NOTICE, "source_type": SOURCE_TYPE,
                  "anonymous_rejected": True, "tasks": []}
        for case in ("left", "right", "missing"):
            result = seed_case(api, case, label, element_ids, output_dir)
            report["tasks"].append(result)
            (output_dir / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{result['part_no']} | {result['task_id']} | {result.get('state', '已有任务，未覆盖')}")
        print(f"测试记录：{output_dir / 'manifest.json'}")
        print("完成：只创建本地PPAP合成测试资料，未调用ERP/MES、批准或客户状态接口。")
        return 0
    except (SeedError, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        # Never echo arbitrary exceptions (HTTP errors may include request data).
        print(str(exc) if isinstance(exc, SeedError) else f"种子执行失败：{type(exc).__name__}；保留已创建的测试任务", file=sys.stderr)
        return 1
    finally:
        if api is not None:
            try:
                api.close()
            except httpx.HTTPError:
                print("测试会话退出请求未完成；不会影响浏览器已有会话", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
