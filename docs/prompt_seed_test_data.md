# 执行提示词：为演示补录 TEST_ 标记的检验记录与报工数据

> 使用方式：把本文件全文作为任务交给执行者（人或 AI）。执行者应先完整阅读本文与列出的参考文件，再动手。
> 用户已于 2026-09-30 批准本任务；执行完成后按"验收与文档"一节收尾。

## 一、背景与目标

项目：`E:\competition\汽车零部件工厂智能体开发`（FastAPI 后端 + React 前端，对接本地 Docker 部署的 ERPNext 与 OpenMES）。

当前 OpenMES 的检验记录为 0 条、工单没有带实际耗时的报工数据，导致演示时：

1. 质量分析面板的检验维度只能如实显示"无数据"；
2. 工单 ETA 只能返回 `DATA_MISSING`（系统设计如此：没有速率数据就拒绝编造，这是本项目的行为准则）。

目标：向 OpenMES 补录一批 **`TEST_` 前缀标记、幂等可重跑** 的测试数据，使演示形成四条完整故事线（见下表）。**业务代码逻辑一律不改**，本任务只新增 seed 脚本 + 数据 + 文档。

## 二、四条演示故事线（数据设计）

| 线 | 工单 | 动作 | 演示效果 |
|---|---|---|---|
| A·阻断 | WO-2026-001（数字 id=2） | **不动**，保持现状：0% 进度、SOP/Control Plan 缺失 | 发运门禁正确拦截，理由清晰 |
| B·检验 | TEST_WO_PAGE_00023（id=9）等 TEST_ 工单 | 补 IQC/IPQC 检验记录（结论合格，数量 2-5 条） | 质量包检验维度有数据可展示 |
| C·ETA | TEST_WO_PAGE_00023（id=9，当前进度 90%） | 补带实际耗时与合格数量的报工/工序批次数据 | "这单什么时候能做完"→ 基于真实速率的估算 |
| D·缺失 | 不指定 | **刻意不补** SN 追溯数据（OpenMES 本来就没有对应 API） | 展示系统"缺数据时如实说"的诚实行为 |

注意：id=2 是"坏消息"演示线（阻断），id=9 是"好消息"演示线（检验齐、有速率、可估时），两者数据互不干扰。

## 三、必读的项目规则

1. 先读 `docs/current_status_and_fix_plan.md`（尤其 §3.25 速率 ETA 的字段依据）和 `docs/next_development_plan.md` 了解现状；
2. 所有命令在 `backend` 目录下执行，Python 用 `../.conda-env/python.exe`；测试基线 **117 passed**，完成后不得低于该数；
3. 凭据全部从仓库根目录 `.env` 读取（`OPENMES_BASE_URL`、`OPENMES_TOKEN` 等），**禁止在任何输出、日志、提交信息中打印**；
4. **不修改 ERPNext 的任何数据**；只向 OpenMES 补数据；
5. 以下 4 个文件保留在本地、**不提交**：`backend/ask_a6.json`、`backend/ask_q6.json`、`backend/uvicorn-9000.log`、`backend/uvicorn-9000.err.log`；
6. 遇到字段或接口不确定时，以 OpenMES 实际 API 响应或 vendored 源码（`services/OpenMes`）为准，**不得凭空猜测字段名**。

## 四、实现要求

1. 新建 `backend/seed_inspection_eta.py`（名字可调），参照 `backend/seed_supplier_data.py` 的幂等模式：先查后插、`TEST_` 前缀、重复执行不产生重复记录；
2. 优先通过 OpenMES 官方 API 写入；若某类数据没有 API，允许经 `docker exec openmes-backend php artisan tinker --execute=...` 或写 `openmes-postgres`（库名 `openmmes`，用户 `openmmes_user`，可用 `\d 表名` 查表结构），但必须在文档里记录用了哪种方式，且同样保证幂等；
3. 速率数据的字段以 `backend/app/adapters/mes/openmes_adapter.py` 和 `backend/tests/test_eta.py` 实际读取的字段为准（`completed_qty`/`passed_qty`、`started_at`/`completed_at`、`actual_elapsed_minutes`/`actual_run_minutes` 这一类）；
4. 数据量少而完整：每条线 2-5 条记录即可，够讲故事就行，不要灌水。

## 五、验收标准（全部满足才算完成）

1. `GET /api/real-orders/quality/package/9` 的检验维度出现 `TEST_` 检验记录；
2. `GET /api/real-orders/mes/track/9` 返回 `eta_status=RATE_BASED`、`eta_basis=observed_production_rate`、`observed_rate` 非空；
3. 用协调者问答接口问 "SAL-ORD-2026-00023 什么时候能做完"，回答含基于速率的估算与真实记录编号（需 `.env` 已配 `DEEPSEEK_API_KEY`）；
4. id=2 的阻断演示**不受影响**：进度仍 0%、质量包仍显示 SOP/Control Plan 缺失；
5. 后端全量测试通过、`python -m compileall -q app` 通过、前端 `npm run build` 通过；
6. seed 脚本重复执行一次，确认不产生重复数据；
7. 截图存证到 `gui-test-screenshots/`（文件名带日期）：质量面板检验数据、ETA 展示各一张。

## 六、收尾（先文档后提交）

1. 在 `docs/current_status_and_fix_plan.md` 追加 `§3.33`：写了哪些表/接口、字段依据、验收接口的实际返回、测试结果、截图路径、遗留问题；
2. 在 `docs/next_development_plan.md` 的"当前执行计划"表把该项勾掉；
3. `git add` 相关文件并提交（提交信息建议：`补录 TEST_ 检验与报工数据，打通检验展示与速率 ETA`），**不要**把第三条第 5 点列出的 4 个临时文件加进提交。

## 七、禁止事项

- 禁止修改 ERPNext 任何数据；
- 禁止改动业务代码逻辑（只允许新增 seed 脚本、数据、文档）；
- 禁止使用无 `TEST_` 标记的数据；
- 禁止在输出中打印 `.env` 凭据或令牌；
- 禁止删除或覆盖现有业务记录（尤其 id=2 的工单、问题、审批记录）。
