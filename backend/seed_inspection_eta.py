"""补录 TEST_ 标记的检验记录与批次实际耗时（演示数据 seed）。

依据 docs/prompt_seed_test_data.md（用户 2026-09-30 已批准）：

- B·检验线：向 OpenMES 补 3 条 TEST_ 前缀检验记录（结论 pass/accept），
  让质量包检验维度从"无数据"变为可展示；
- C·ETA 线：为工单 9（TEST_WO_PAGE_00023）的 TEST_ 批次步骤补真实起止
  时间，使 ETA 从 DATA_MISSING 切换为基于真实速率的估算（速率 =
  1800 件 / 150 分钟 = 720 件/小时，剩余 200 件约 17 分钟）；
- A·阻断线（WO-2026-001 / id=2）与 D·缺失线（SN 追溯）刻意不动。

写入方式说明：OpenMES `materials` 表当前为空，官方 `POST /api/v1/inspections`
要求已存在的 material_id，因此本脚本统一通过 `docker exec openmes-postgres
psql` 直接写库（任务规则允许，此处显式记录）。全部数据带 TEST_ 前缀，
幂等可重跑：固定主键语义（按 code / lot_number 查存在再插，固定时间戳
覆盖写入），重复执行不产生重复记录。

铁律：不修改业务代码；不触碰 ERPNext；不改动 id=2（WO-2026-001）的任何
记录；凭据只从环境变量读取，不打印。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PSQL = ["docker", "exec", "openmes-postgres", "psql", "-U", "openmmes_user",
        "-d", "openmmes", "-v", "ON_ERROR_STOP=1", "-qtA"]

# 批次 3：工单 9（TEST_WO_PAGE_00023）的 TEST_LOT_PAGE_9，步骤 1 已有
# passed_qty=1800 但 started_at == completed_at（0 耗时）。补 150 分钟真实
# 执行窗口（2026-09-29 12:00 → 14:30 UTC），速率 720 件/小时。
BATCH_LOT = "TEST_LOT_PAGE_9"
STEP_NAME_PREFIX = "TEST_"
BATCH_STARTED = "2026-09-29 12:00:00"
BATCH_COMPLETED = "2026-09-29 14:30:00"
BATCH_ELAPSED_MINUTES = 150  # 与起止窗口一致：1800 件 / 150 分钟 = 720 件/小时

# 检验线：1 个 TEST_ 物料 + 3 条合格检验（IQC 来料 1 条 + IPQC 过程 2 条）。
SEED_MATERIAL_CODE = "TEST-BD-2401-SEED"
SEED_MATERIAL_NAME = "TEST_ 补录物料-制动盘毛坯（seed_inspection_eta）"
INSPECTIONS = [
    {
        "lot": "TEST-IQC-20260930-01",
        "qty": "2000",
        "started": "2026-09-30 01:00:00",
        "completed": "2026-09-30 01:25:00",
        "notes": "TEST_ 补录（seed_inspection_eta.py）：IQC 来料检验合格",
    },
    {
        "lot": "TEST-IPQC-20260930-01",
        "qty": "500",
        "started": "2026-09-30 02:00:00",
        "completed": "2026-09-30 02:10:00",
        "notes": "TEST_ 补录（seed_inspection_eta.py）：IPQC 首件检验合格",
    },
    {
        "lot": "TEST-IPQC-20260930-02",
        "qty": "1500",
        "started": "2026-09-30 03:00:00",
        "completed": "2026-09-30 03:15:00",
        "notes": "TEST_ 补录（seed_inspection_eta.py）：IPQC 巡检合格",
    },
]


def psql(sql: str) -> str:
    result = subprocess.run(PSQL + ["-c", sql], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"SQL 执行失败：{result.stderr.strip()[:300]}\nSQL: {sql[:160]}")
    return result.stdout.strip()


def scalar(sql: str) -> str:
    out = psql(sql).strip()
    return out.splitlines()[0] if out else ""


def ensure_material() -> str:
    material_id = scalar(
        f"SELECT id FROM materials WHERE code = '{SEED_MATERIAL_CODE}' LIMIT 1;"
    )
    if material_id:
        return material_id
    return scalar(
        "INSERT INTO materials (code, name, description, unit_of_measure, "
        "tracking_type, is_active, created_at, updated_at) VALUES ("
        f"'{SEED_MATERIAL_CODE}', '{SEED_MATERIAL_NAME}', "
        "'TEST_ seed 数据：供检验记录引用，非真实物料', 'pcs', 'lot', true, "
        "NOW(), NOW()) RETURNING id;"
    )


def ensure_inspection(material_id: str, spec: dict) -> str:
    existing = scalar(
        f"SELECT id FROM inspections WHERE lot_number = '{spec['lot']}' LIMIT 1;"
    )
    if existing:
        return existing
    return scalar(
        "INSERT INTO inspections (material_id, lot_number, quantity_received, "
        "inspector_id, started_at, completed_at, status, disposition, "
        "disposition_notes, notes, created_at, updated_at) VALUES ("
        f"{material_id}, '{spec['lot']}', {spec['qty']}, 1, "
        f"'{spec['started']}', '{spec['completed']}', 'pass', 'accept', "
        f"'TEST_ 合格（seed）', '{spec['notes']}', NOW(), NOW()) RETURNING id;"
    )


def fix_batch_timings() -> None:
    # 只允许触碰 lot_number 带 TEST_ 前缀的批次，硬性隔离 id=2 的业务数据。
    batch_id = scalar(
        f"SELECT id FROM batches WHERE lot_number = '{BATCH_LOT}' AND "
        "lot_number LIKE 'TEST%' LIMIT 1;"
    )
    if not batch_id:
        raise RuntimeError(f"未找到 TEST_ 批次 {BATCH_LOT}，ETA 线无法补录")
    psql(
        f"UPDATE batches SET started_at = '{BATCH_STARTED}', "
        f"completed_at = '{BATCH_COMPLETED}', updated_at = NOW() "
        f"WHERE id = {batch_id} AND lot_number LIKE 'TEST%';"
    )
    psql(
        f"UPDATE batch_steps SET started_at = '{BATCH_STARTED}', "
        f"completed_at = '{BATCH_COMPLETED}', "
        f"duration_minutes = {BATCH_ELAPSED_MINUTES}, "
        f"actual_elapsed_minutes = {BATCH_ELAPSED_MINUTES}, "
        f"actual_run_minutes = {BATCH_ELAPSED_MINUTES}, updated_at = NOW() "
        f"WHERE batch_id = {batch_id} AND name LIKE '{STEP_NAME_PREFIX}%';"
    )


def report() -> None:
    material_total = scalar(
        f"SELECT count(*) FROM materials WHERE code = '{SEED_MATERIAL_CODE}';"
    )
    inspection_rows = scalar(
        "SELECT count(*) FROM inspections WHERE lot_number LIKE 'TEST-%';"
    )
    timings = psql(
        f"SELECT bs.name, bs.passed_qty, bs.started_at, bs.completed_at "
        f"FROM batch_steps bs JOIN batches b ON b.id = bs.batch_id "
        f"WHERE b.lot_number = '{BATCH_LOT}';"
    )
    print(f"materials(1 expected)   : {material_total}")
    print(f"inspections TEST-(3)    : {inspection_rows}")
    print("batch step timings      :")
    for line in timings.splitlines():
        print(f"  {line}")


def main() -> int:
    print("== seed_inspection_eta：TEST_ 检验记录 + 批次实际耗时 ==")
    material_id = ensure_material()
    print(f"seed material id        : {material_id}")
    for spec in INSPECTIONS:
        inspection_id = ensure_inspection(material_id, spec)
        print(f"inspection id           : {inspection_id} ({spec['lot']})")
    fix_batch_timings()
    print("batch timings           : updated (idempotent overwrite)")
    print("-- 幂等状态核查 --")
    report()
    print("完成：重复执行本脚本不会产生重复记录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
