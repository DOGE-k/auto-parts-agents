"""PPAP 一键生成案例（可重复执行、隔离数据库、TEST 模式）。

用法（在 backend/ 目录下）：
    ../.conda-env/python.exe ../scripts/ppap_demo_case.py            # 生成并导出
    ../.conda-env/python.exe ../scripts/ppap_demo_case.py --tasks    # 再加 pytest 用例

设计要点（对应 docs/PPAP阶段A确认清单.md 与设计说明 §13）：
- 使用临时 SQLite 数据库，绝不读写 backend/data/demo.db 或任何生产库。
- 任务是 TEST 模式：不关联真实 ERP/MES 业务对象，不冒充真实客户提交。
- 所有测量值/评价人都是显式测试标记（DEMO- 前缀 + synthetic_demo_only 来源）。
- 数据本身满足 22 条目契约，因此能一路走到 COMPILED 并导出可编辑 Excel；
  但 MSA/过程能力的验收判定仍是 NEEDS_CONFIRMATION（未配置业务规则），
  脚本不会给出"合格"结论——这是设计要求，不是缺陷。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

# 必须在导入 app.* 之前固定数据库与存储目录（app.persistence.database 导入即建引擎）。
_TMP = Path(tempfile.gettempdir()) / f"ppap_demo_{uuid.uuid4().hex[:8]}"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'demo.db').as_posix()}"
os.environ.setdefault("PPAP_STORAGE_DIR", str(_TMP / "store"))
# 演示用验收规则：模拟"业务方已人工确认阈值"的情形。真实部署必须由业务方
# 提供 acceptance_rules.json，脚本里这份只用于演示完整审批闭环。
_RULES = _TMP / "demo_acceptance_rules.json"
_RULES.write_text(json.dumps({
    "rule_version": "DEMO-RULES-2026.10.22",
    "source": "DEMO-ONLY：演示用阈值，非任何客户的正式接收准则",
    "confirmed_by": "DEMO-BUSINESS-OWNER",
    "confirmed_at": "2026-10-22T00:00:00Z",
    "notes": ["仅用于演示 PPAP 审批闭环；不得作为真实判定依据。"],
    "metrics": {
        "percent_ev": {"min": 0, "max": 10, "source": "DEMO-RULES §1"},
        "percent_av": {"min": 0, "max": 10, "source": "DEMO-RULES §1"},
        "percent_grr": {"min": 0, "max": 30, "source": "DEMO-RULES §1"},
        "percent_tolerance": {"min": 0, "max": 30, "source": "DEMO-RULES §1"},
        "ndc": {"min": 5, "source": "DEMO-RULES §1"},
        "cp": {"min": 1.33, "source": "DEMO-RULES §2"},
        "cpk": {"min": 1.33, "source": "DEMO-RULES §2"},
        "pp": {"min": 1.33, "source": "DEMO-RULES §2"},
        "ppk": {"min": 1.33, "source": "DEMO-RULES §2"},
    },
}, ensure_ascii=False), encoding="utf-8")
os.environ["PPAP_RULES_FILE"] = str(_RULES)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.persistence.database import engine  # noqa: E402
from app.persistence.models import Base  # noqa: E402
from app.services import ppap, ppap_storage, ppap_template  # noqa: E402

Base.metadata.create_all(bind=engine)

ACTOR = "demo-author"          # 编制人（测试账号标识，不是真实员工）
REVIEWER = "demo-reviewer"     # 审核人（与编制人不同，避免同人审核提示）
SOURCE_REF = "DEMO-INPUT-RECORD-2026-10-22"   # 人工确认来源引用

# ----------------------------------------------------------------------
# 案例数据：一个带特殊特性的左右件中的「左件」制动盘
# 1) base / 工序 / 特性 / 检验值 / 材料 / MSA / 过程研究 全部齐备
# 2) 特殊特性 CHAR-SPC-01 是 CTQ（seal 面直径），带双侧规格
# 3) 测点、单位、规格都显式给出；不写字符合格与否（判定由规则产生）
# ----------------------------------------------------------------------

CUSTOMER = "DEMO-CUSTOMER 演示客户（非真实客户）"
PART_NO = "DEMO-BD-2401-L"
PART_NAME = "演示用制动盘-左件（非真实零件）"

OPERATIONS = [
    {"op_id": "DEMO-OP-10", "op_name": "粗车外圆", "station": "CNC-01", "sequence": 10},
    {"op_id": "DEMO-OP-20", "op_name": "精车端面", "station": "CNC-02", "sequence": 20},
    {"op_id": "DEMO-OP-30", "op_name": "钻孔", "station": "DRL-01", "sequence": 30},
    {"op_id": "DEMO-OP-40", "op_name": "去毛刺", "station": "DEB-01", "sequence": 40},
    {"op_id": "DEMO-OP-50", "op_name": "终检", "station": "QC-01", "sequence": 50},
]

RELATIONS = [
    {"from_op": "DEMO-OP-10", "to_op": "DEMO-OP-20", "relation_type": "sequence"},
    {"from_op": "DEMO-OP-20", "to_op": "DEMO-OP-30", "relation_type": "sequence"},
    {"from_op": "DEMO-OP-30", "to_op": "DEMO-OP-40", "relation_type": "sequence"},
    {"from_op": "DEMO-OP-40", "to_op": "DEMO-OP-50", "relation_type": "sequence"},
]

# 特性：1 个特殊特性（CTQ）+ 2 个普通特性，全部带单位与双侧规格
CHARACTERISTICS = [
    {
        "characteristic_id": "DEMO-CHAR-SPC-01",
        "name": "密封面外径（CTQ）",
        "characteristic_type": "product",
        "special": True,
        "unit": "mm",
        "nominal": 120.00,
        "usl": 120.10,
        "lsl": 119.90,
        "drawing_location": "图纸视图 A-3",
        "note": "特殊特性；来源：演示用图纸标注",
    },
    {
        "characteristic_id": "DEMO-CHAR-02",
        "name": "盘体厚度",
        "characteristic_type": "product",
        "special": False,
        "unit": "mm",
        "nominal": 24.00,
        "usl": 24.20,
        "lsl": 23.80,
        "drawing_location": "图纸视图 B-1",
    },
    {
        "characteristic_id": "DEMO-CHAR-03",
        "name": "螺栓孔径",
        "characteristic_type": "product",
        "special": False,
        "unit": "mm",
        "nominal": 10.50,
        "usl": 10.60,
        "lsl": 10.40,
        "drawing_location": "图纸视图 C-2",
    },
]

# 检验实测值：每个特性 3 个测点。规格与单位逐条给出——检查器明确禁止从特性定义
# 隐式继承单位与规格（UNIT-001/CHAR-001），所以这里必须重复填写。
INSPECTION_ROWS = [
    ("DEMO-CHAR-SPC-01", "P1", 120.02, "S1"),
    ("DEMO-CHAR-SPC-01", "P2", 119.98, "S1"),
    ("DEMO-CHAR-SPC-01", "P3", 120.05, "S2"),
    ("DEMO-CHAR-02", "P1", 24.03, "S1"),
    ("DEMO-CHAR-02", "P2", 23.97, "S1"),
    ("DEMO-CHAR-02", "P3", 24.01, "S2"),
    ("DEMO-CHAR-03", "P1", 10.48, "S1"),
    ("DEMO-CHAR-03", "P2", 10.52, "S1"),
    ("DEMO-CHAR-03", "P3", 10.50, "S2"),
]

_CHAR_SPEC = {c["characteristic_id"]: c for c in CHARACTERISTICS}

MATERIAL_ROWS = [
    {
        "test_item": "抗拉强度",
        "supplier": "DEMO-SUPPLIER（演示供应商）",
        "lot_no": "DEMO-LOT-2026-10-01",
        "report_no": "DEMO-REPORT-MAT-001",
        "spec_ref": "图纸材料条款 4.2",
        "unit": "MPa",
        "value": 251.0,
    },
    {
        "test_item": "硬度",
        "supplier": "DEMO-SUPPLIER（演示供应商）",
        "lot_no": "DEMO-LOT-2026-10-01",
        "report_no": "DEMO-REPORT-MAT-002",
        "spec_ref": "图纸材料条款 4.3",
        "unit": "HB",
        "value": 187.0,
    },
]

# MSA 平均极差法原始数据：2 评价人 × 3 零件 × 3 次，单位与特性一致。
# 零件之间刻意拉开距离、同一零件重复读数贴近，这样重复性误差小、零件间差异大，
# ndc（可区分类别数）才可能达到演示阈值；这是测量系统的数学要求，不是美化数据。
MSA_MEASUREMENTS = [
    {"appraiser": "DEMO-APPR-A", "parts": [
        {"part": "DEMO-P1", "trials": [119.94, 119.95, 119.94]},
        {"part": "DEMO-P2", "trials": [120.00, 120.00, 120.01]},
        {"part": "DEMO-P3", "trials": [120.06, 120.05, 120.06]},
    ]},
    {"appraiser": "DEMO-APPR-B", "parts": [
        {"part": "DEMO-P1", "trials": [119.95, 119.94, 119.95]},
        {"part": "DEMO-P2", "trials": [120.01, 120.00, 120.00]},
        {"part": "DEMO-P3", "trials": [120.05, 120.06, 120.05]},
    ]},
]

# 过程研究原始样本：25 件连续测量，规格 120.00±0.10。
# 样本量/抽样规则本身属未确认业务规则，检查器会以 INCOMPLETE 提示；
# 这里给足 25 件以演示"数据齐备但规则仍需确认"的边界。
PROCESS_VALUES = [120.01, 120.03, 119.99, 120.00, 120.02,
                  119.98, 120.04, 120.01, 120.00, 120.02,
                  120.03, 119.99, 120.01, 120.00, 120.02,
                  120.01, 119.98, 120.03, 120.00, 120.01,
                  120.02, 120.00, 119.99, 120.03, 120.01]


def _step(title: str) -> None:
    print(f"\n=== {title} ===")


def build() -> dict:
    _step("1. 创建 TEST 模式任务")
    created = ppap.create_task(
        customer_name=CUSTOMER, part_no=PART_NO, part_name=PART_NAME,
        owner=ACTOR, actor=ACTOR, part_side="LEFT", drawing_version="DEMO-V1",
        engineering_change_level="DEMO-ECN-0001",
        submission_reason="演示：新零件首次提交（非真实提交）",
        submission_level="演示级别",
        reviewer=REVIEWER, due_date="2026-12-31",
        authorization_ref="DEMO-AUTH-2026-10-22（演示授权，非真实客户授权）",
        data_mode="TEST",
    )
    if not created.get("success"):
        raise SystemExit(f"创建任务失败：{json.dumps(created, ensure_ascii=False)}")
    task_id = created["task"]["task_id"]
    print(f"task_id = {task_id}")
    print(f"run_state = {created['task']['run_state']}（22 条目适用性待确认）")

    _step("2. 人工确认 22 条目适用性")
    # 三类取值在本案例中的使用理由：
    # - submit：所需证据全部可由本地原件满足，演示"可提交"。
    # - on_request：输入契约强制要求 openmes_*/erp_* 真实系统证据，而 TEST 任务
    #   禁止关联真实 ERP/MES（ppap_mode 的硬隔离）。这些条目仍会正常编制出内容，
    #   只是不主张"已具备可提交证据"；正式任务取证后再改为 submit。
    # - 不使用 not_applicable：本案例不给"客户不需要"的结论。
    submit_ids = {
        "submission_checklist", "engineering_change_documents", "msa",
        "material_performance_results", "initial_process_study",
        "sample_production_part", "customer_specific_requirements",
        "psw", "attachment_index", "equipment_packaging",
    }
    decisions = []
    for element_id in ppap_template.ELEMENT_IDS:
        if element_id in submit_ids:
            decisions.append({
                "element_id": element_id, "requirement": "submit",
                "basis": "演示案例：所需证据均可由本地登记原件满足",
            })
        else:
            decisions.append({
                "element_id": element_id, "requirement": "on_request",
                "basis": (
                    "演示：该条目契约要求 OpenMES/ERP 真实系统证据，而 TEST 任务按设计"
                    "禁止关联真实系统；正式任务取证后应改为 submit"
                ),
            })
    confirmed = ppap.confirm_applicability(task_id, decisions, actor=ACTOR)
    if not confirmed.get("success"):
        raise SystemExit(f"适用性确认失败：{json.dumps(confirmed, ensure_ascii=False)}")
    print(f"已确认 {len(decisions)} 条（submit {len(submit_ids)}，on_request "
          f"{len(ppap_template.ELEMENT_IDS) - len(submit_ids)}）")
    if confirmed.get("unconfirmed"):
        print(f"未确认剩余：{confirmed['unconfirmed']}")

    _step("3. 录入结构化输入（14 条，全部带来源引用）")
    def put(dataset: str, payload: dict, *, source: str = SOURCE_REF) -> None:
        result = ppap.upsert_input(
            task_id, dataset=dataset, payload=payload, actor=ACTOR,
            source_ref=source, source_type="user_confirmation",
        )
        if not result.get("success"):
            raise SystemExit(f"输入 {dataset} 失败：{json.dumps(result, ensure_ascii=False)}")
        key = result.get("record_key") or result.get("input", {}).get("record_key", "")
        print(f"  OK {dataset:22s} {key}")

    put("product_base", {
        "part_no": PART_NO, "part_name": PART_NAME, "customer_name": CUSTOMER,
        "drawing_version": "DEMO-V1", "engineering_change_level": "DEMO-ECN-0001",
        "revision_note": "演示版本；非真实图纸",
    })
    for op in OPERATIONS:
        put("process_operation", op)
    for rel in RELATIONS:
        put("process_relation", rel)
    for char in CHARACTERISTICS:
        put("characteristic", char)
    for char_id, point, value, sample in INSPECTION_ROWS:
        spec = _CHAR_SPEC[char_id]
        put("inspection_value", {
            "characteristic_id": char_id, "point": point, "value": value,
            "usl": spec["usl"], "lsl": spec["lsl"], "nominal": spec["nominal"],
            "unit": spec["unit"],
            "sample_no": sample, "measured_by": "DEMO-INSPECTOR",
            "measured_at": "2026-10-22T09:00:00Z",
        })
    for row in MATERIAL_ROWS:
        put("material_evidence", row)
    put("msa_study", {
        "study_id": "DEMO-MSA-01",
        "characteristic_id": "DEMO-CHAR-SPC-01",
        "unit": "mm",
        "tolerance": 0.20,
        "method": "average_range",
        "measurements": MSA_MEASUREMENTS,
        "note": "演示原始数据；2 评价人×3 零件×3 次",
    })
    put("process_study", {
        "study_id": "DEMO-STUDY-01",
        "characteristic_id": "DEMO-CHAR-SPC-01",
        "unit": "mm",
        "values": PROCESS_VALUES,
        "usl": 120.10,
        "lsl": 119.90,
        "sampling_note": "演示样本；连续 15 件",
    })
    put("physical_record", {
        "record_type": "sample_production_part",
        "storage_location": "DEMO-RACK-A1（演示库位）",
        "quantity": 5,
        "note": "演示：5 件送样样品登记",
    })
    put("packaging_equipment", {
        "item": "包装箱 DEMO-BOX-600x400",
        "quantity": 20,
        "owner": "DEMO-PACKAGING-ENGINEER",
        "note": "演示包装方案；每箱 20 件",
    })
    put("customer_requirement", {
        "requirement_text": "演示：客户特殊要求（非真实客户要求）",
        "source": "DEMO-CSR-001",
        "clause": "演示条款",
    })
    put("change_record", {
        "change_no": "DEMO-ECN-0001",
        "change_description": "演示：首次量产提交",
        "change_date": "2026-10-22",
        "level": "DEMO-E1",
        "reason": "演示：新零件",
    })

    _step("4. 登记来源证据（按条目证据契约逐条绑定）")
    # EVID-001 要求：每个非「不适用」条目的每种 evidence_type 至少有一条可用记录。
    # 四个条目要求 openmes_*（真实系统来源），TEST 任务不允许关联真实系统，
    # 因此这些条目在案例中保持缺证据——这是诚实结果，不是脚本失败。
    uploads = [
        ("DEMO-ORIG-DRAWING", "演示图纸原件（DEMO-V1）", ["design_records", "dimensional_layout"]),
        ("DEMO-ORIG-ECN", "演示工程变更通知", ["engineering_change_documents"]),
        ("DEMO-ORIG-CUST-APPROVAL", "演示客户工程批准（非真实批准）", ["customer_engineering_approval"]),
        ("DEMO-ORIG-DFMEA", "演示设计 FMEA", ["dfmea"]),
        ("DEMO-ORIG-MAT-REPORT", "演示材料试验报告", ["material_performance_results"]),
        ("DEMO-ORIG-LAB", "演示实验室资质文件", ["qualified_laboratory_documentation"]),
        ("DEMO-ORIG-AAR", "演示外观批准报告（非真实签署）", ["appearance_approval_report"]),
        ("DEMO-ORIG-SAMPLE", "演示送样样品登记单", ["sample_production_part"]),
        ("DEMO-ORIG-MASTER", "演示标准样件登记单", ["master_sample"]),
        ("DEMO-ORIG-GAUGE", "演示检具登记单", ["checking_aid"]),
        ("DEMO-ORIG-CSR", "演示客户特殊要求原件", ["customer_specific_requirements"]),
        ("DEMO-ORIG-DIM-RECORD", "演示尺寸检验原始记录", ["dimensional_results"]),
        ("DEMO-ORIG-DIM-LAYOUT", "演示全尺寸布局图", ["dimensional_layout"]),
    ]
    for source_id, summary, element_ids in uploads:
        # 原件必须真实存在于受控目录：只有此时系统才记录内容哈希与文档版次，
        # 也才满足 EVID-001 的「可用证据」判定（否则记为 READ_EMPTY）。
        rel_path = f"originals/{source_id}.txt"
        ppap_storage.write_bytes(
            rel_path,
            f"{summary}\n\nDEMO ONLY - NOT A REAL FACTORY RECORD\n"
            f"source_id={source_id}\npart_no={PART_NO}\n".encode("utf-8"),
        )
        evidence = ppap.register_human_evidence(
            task_id, source_type="user_upload", source_id=source_id,
            summary=summary, locator=rel_path,
            actor=ACTOR, element_ids=element_ids,
            document_version="DEMO-V1",
        )
        if evidence.get("success"):
            print(f"  OK {source_id:26s} → {element_ids}")
        else:
            print(f"  SKIP {source_id:24s} {evidence.get('code')}: {evidence.get('error')}")

    _step("5. 编译（编制 22 条目 + 一致性检查 + 导出 Excel）")
    compiled = ppap.compile_task(task_id, actor=ACTOR)
    if not compiled.get("success"):
        raise SystemExit(f"编译失败：{json.dumps(compiled, ensure_ascii=False)}")
    run = compiled["run"]
    artifact = compiled.get("artifact") or {}
    print(f"run_id   = {run['run_id']}")
    print(f"status   = {run['status']}")
    summary = run.get("summary") or {}
    print(f"findings = {summary.get('finding_count')}（阻断 {summary.get('blocker_count')}，"
          f"重要 {summary.get('major_count')}，提示 {summary.get('info_count')}）")
    print(f"artifact = {artifact.get('artifact_id')} v{artifact.get('version')} "
          f"{artifact.get('file_name')} {artifact.get('byte_size')} bytes")

    _step("6. 回读导出文件并核对内容")
    readback = ppap.read_artifact(task_id, artifact["artifact_id"])
    if not readback.get("ok"):
        raise SystemExit(f"回读失败：{json.dumps(readback, ensure_ascii=False)}")
    content = readback["content"]
    out = Path(tempfile.gettempdir()) / "ppap_demo_output.xlsx"
    out.write_bytes(content)
    print(f"已写出：{out}（{len(content)} bytes）")

    from openpyxl import load_workbook
    import io
    wb = load_workbook(io.BytesIO(content))
    print(f"工作表 {len(wb.sheetnames)} 个：{wb.sheetnames[:6]} …")
    print(f"打印方向：{wb[wb.sheetnames[0]].page_setup.orientation}，"
          f"纸张：{wb[wb.sheetnames[0]].page_setup.paperSize}")

    _step("7. 审核 → 批准 → 归档")
    submitted = ppap.submit_review(task_id, actor=ACTOR)
    if not submitted.get("success"):
        print(f"提交审核被拒绝（预期：仍有阻断项）：{submitted.get('code')}")
        for finding in (submitted.get("blockers") or [])[:6]:
            print(f"  阻断 {finding.get('check_id')} {finding.get('title')} → "
                  f"{finding.get('responsible_role')}")
        return {"task_id": task_id, "artifact_id": artifact.get("artifact_id", ""),
                "xlsx": out, "status": "BLOCKED",
                "blockers": submitted.get("blockers") or []}

    decided = ppap.decide_review(
        task_id, submitted["review"]["review_id"], actor=REVIEWER,
        decision="APPROVED", note="演示审核通过（非真实客户批准）",
    )
    print(f"审核决定：{decided.get('success')} 冻结时间={decided.get('artifact', {}).get('frozen_at')}")
    archived = ppap.archive_task(task_id, actor=REVIEWER)
    print(f"归档：{archived.get('success')}")
    return {
        "task_id": task_id,
        "artifact_id": (archived.get("artifact") or artifact).get("artifact_id", ""),
        "xlsx": out,
        "status": "COMPILED",
        "archived": bool(archived.get("success")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PPAP 一键生成演示案例")
    parser.add_argument("--json", action="store_true", help="仅输出结果 JSON")
    args = parser.parse_args()
    result = build()
    print("\n=== 结果 ===")
    printable = {k: (str(v) if isinstance(v, Path) else v) for k, v in result.items()}
    printable.pop("blockers", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    if result.get("blockers"):
        print(f"\n仍有 {len(result['blockers'])} 个阻断项：")
        for finding in result["blockers"][:10]:
            print(f"  - {finding.get('check_id')} {finding.get('element_id') or '-'} "
                  f"{finding.get('title')} → {finding.get('responsible_role')}")
    print(f"\n临时数据库与存储目录：{_TMP}（可安全删除）")


if __name__ == "__main__":
    main()
