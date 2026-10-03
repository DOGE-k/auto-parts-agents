# 交接文档：真实报工与质量问题登记（2026-09-30）

> 交接对象：接手"通用能力补强"剩余两项写入能力的开发者（人或 AI）。
> 本文自包含：背景、已确认的 API 契约、必须遵守的铁律、实现步骤、验收标准。
> 全部历史细节见 `current_status_and_fix_plan.md` §3.38-§3.43；本项目的硬规则见 `real-data-hard-rules` 精神（下文第三节已摘录要点）。

## 一、任务定义

系统已实现"任意新订单全链路"（报价 → 审批 → ERP 草稿 → 采购分析 → **工单下达** → 跟单/质量/发运门禁，§3.43）。剩余两个写入类断点，补齐后"剧本外任意输入"覆盖全部故事线：

| # | 任务 | 现状 | 补齐后的效果 |
|---|------|------|------------|
| 1 | **真实报工**：对任意 OpenMES 工单创建批次并完成工序（带实际耗时） | 新工单无报工数据，速率 ETA 只能如实报 DATA_MISSING（依赖旧 seed 直写数据库的补录） | 页面上人工审批后真实报工 → 速率 ETA 现场算出（RATE_BASED） |
| 2 | **质量问题登记**：对任意工单创建质量问题（NCR） | 质量待办只有预置 issue，无法现场新增 | 页面上人工审批后创建真实质量问题 → 立即走既有 NCR 处置/关闭闭环 |

两项都是**写入类**：必须走项目既有审批门禁三步模式（§3.43 已有完整范本）。

## 二、已确认的 OpenMES API 契约（源码级调研，无需重复调研）

位置：`services/OpenMes/backend/routes/api.php`（v1 树，Sanctum 会话认证——就是现在登录拿的 Bearer token，admin 角色权限充分）。

### 任务 1：报工链路（三个端点按序调用）

1. **建批次** `POST /api/v1/work-orders/{workOrderId}/batches`（routes/api.php:611）
   - 请求体：`target_qty`（required，numeric）、`workstation_id`（nullable）、`lot_number`（nullable，max:50，批次号）
   - **约束**：批次 target_qty 总和不得超过工单 planned_qty（除非配置 `openmmes.allow_overproduction`）
   - 控制器 `BatchController::store`（backend/app/Http/Controllers/Api/V1/BatchController.php:62-105）
2. **开工** `POST /api/v1/batch-steps/{batchStepId}/start`（routes/api.php:620，无 body）
3. **报工/完工** `POST /api/v1/batch-steps/{batchStepId}/complete`（routes/api.php:621）
   - 请求体（`CompleteBatchStepRequest.php:23-31`）：`produced_qty`（nullable）、**`actual_elapsed_minutes` / `actual_setup_minutes` / `actual_run_minutes`**（正是速率 ETA 计算需要的真实耗时字段；约束 setup+run ≤ elapsed，BatchService.php:177-179 强制校验）
   - 完成逻辑 `BatchService.php:97-212`：批次全部步骤完成后 `finishBatchIfComplete`（576-595 行）回写 `batches.produced_qty`
   - **注意**：`completed_at` 服务端取 now()，无法回填历史时间点——演示时按"现在报工"设计话术
   - 建批次/开工/完工的响应里都带步骤 id，需要从建批次响应中取 `batchStepId`

补充：批次/步骤查询 `GET /api/v1/work-orders/{id}/batches`（已封装：`OpenMESAdapter.get_work_order_batches`，`backend/app/adapters/mes/openmes_adapter.py:390`）。

### 任务 2：质量问题创建

`POST /api/v1/issues`（routes/api.php:665）→ `IssueController::store`（IssueController.php:83-94，reported_by_id 自动取当前用户）。

- 请求体（`CreateIssueRequest.php:23-29`）：`work_order_id`（required）、`batch_step_id`（nullable）、**`issue_type_id`（required）**、`title`（required，max:255）、`description`（nullable，max:5000）
- **前置**：issue_type_id 必须真实存在。查列表端点 `GET /api/v1/issue-types`（routes/api.php:681 附近，只读）；演示前先确认库里有合适的 issue_type（如尺寸超差类），没有则需要先建（`POST /api/v1/issue-types` 标注 Admin only）——**接手者先 curl 看一眼现有 issue_types 再定 UI 形态**

## 三、铁律（违反即返工，摘自项目历史）

1. **写入必须走审批门禁三步**：① 建审批（只登记将执行内容，不写入）→ ② 人工批准（审批人 = 真实登录身份）→ ③ 审批校验通过后写入 + **回读验证** + **幂等**。完整范本：§3.43 的工单下达（`real_order.py` 的 `request_work_order_dispatch / approve_work_order_dispatch / dispatch_work_order_to_openmes`），照抄结构即可。
2. **禁止伪结论**：数据缺失如实报（参考 §3.42 的 EVALUATION_BLOCKED 模式）；报工完成必须回读工单/批次确认 produced_qty、速率数据真实变化后才报成功。
3. **幂等**：重复报工（同工单同批次号/同审批）不得重复写入；重复建 issue 需要防重键（建议 title+work_order_id 组合或审批 payload 里的唯一标记，接手者设计时想清楚边界并写测试）。
4. **不猜字段**：本文第二节契约来自 OpenMES 源码，若调用时响应与预期不符，回读源码确认，不要试错猜。
5. 每完成一步：后端 `pytest tests -q` 基线 **139 passed 不许下降**、前端 vitest 14 + build 通过；先更新 `current_status_and_fix_plan.md`（追加 §3.44+）再 git 提交（逐个 add）。
6. 不提交：`backend/ask_*.json`、`backend/uvicorn-9000*.log`、`.env`；不输出任何密钥。
7. 后端改动需重启：杀 9000 监听进程 → `cd backend && APP_ADAPTER_MODE=real ../.conda-env/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 9000`。前端 HMR 自动生效。

## 四、建议实现步骤（每步跑测试）

### 任务 1：真实报工

1. **适配器**（`backend/app/adapters/mes/openmes.py` client 层 + `openmes_adapter.py` 适配层）：透传三个端点——`create_batch(work_order_id, payload)`、`start_batch_step(step_id)`、`complete_batch_step(step_id, payload)`（照 `create_work_order` 的透传写法，§3.43）。
2. **服务层**（`real_order.py`）：三步审批（建议审批前缀 `RPT-`），审批 payload 记录 work_order_id / target_qty / lot_number（建议 `TEST_LOT_` 前缀或用户输入）/ produced_qty / actual_elapsed_minutes。写入步按序调三端点；回读 `get_work_order_batches` + 工单 produced_qty 验证；幂等：同 lot_number 批次已存在即返回既有（参照 dispatch 的幂等检索）。
3. **API**（`main.py`）：三个端点挂 `require_real_write_access + require_real_identity`（照 dispatch 端点抄，注意 async 函数要 `await`——§3.43 踩过漏 await 的坑）。
4. **前端**：Step 8 跟单面板（`TrackingFlow.tsx` 的 TrackingQualityGatePanels）加"报工（需人工审批）"区块：填数量+实际耗时 → 确认面板 → 串行执行留痕（照 WorkOrderSelectPanel 的 dispatch 区块抄）；成功后自动 `onReload()` 刷新进度/ETA。
5. **测试**（`tests/test_real_order_realdata.py`）：FakeMES 加三个方法，测：审批前拒绝、成功路径回读验证、幂等、setup+run>elapsed 之类服务端约束的前置校验（可选）。

### 任务 2：质量问题登记

1. 先 `curl -H "Authorization: Bearer <token>" http://localhost/api/v1/issue-types` 看现有类型，确定 UI 是下拉选择还是需要先建类型。
2. 适配器透传 `POST /api/v1/issues`；服务层三步审批（前缀 `QISS-`），payload 含 work_order_id / issue_type_id / title / description；写入后回读（`list_open_issues` 或工单质量包）确认新 issue 可见；幂等设计见铁律 3。
3. API + 前端（质量中心页签加"登记质量问题"入口，或 Step 8 质量面板）+ 测试，同上模式。
4. **演示联动建议**：登记成功后立即引导进入既有 NCR 处置面板（复用 §3.40 的跨页签联动 goToQualityDispose 模式）。

### 收尾

- 真实验证：对 §3.43 创建的工单 id=10（WO-SO-2026-00024，BD-2402 · 800 件）做一次真实验收（报工后 ETA 变 RATE_BASED；建 issue 后质量待办出现新条目），记录编号进文档。
- 更新 `demo_script.md`：故事线 B（速率 ETA）补"现场报工"路径；故事线 D 补"现场登记质量问题"路径。
- 小待办顺带处理（可选）：工单 product_name 为空的 product_type external_code 映射（§3.43 第三节末尾）。

## 五、环境速查（2026-09-30 实测状态）

- 分支 `codex/real-integration-layer`，最新提交 `4352e61`；测试基线后端 139 / 前端 14 全绿。
- 后端 9000 运行中（业务库 PostgreSQL `autoparts-db` 容器）；前端 5173 dev server；OpenMES 80（admin 登录拿 Bearer，15 分钟 TTL，**密码问用户，勿写入任何文件**）；ERPNext 8080。
- 写入令牌 `.env` 的 `REAL_WRITE_API_TOKEN`（请求头 `X-Real-Write-Token`），页面"会话设置"里填。
- 演示入口 `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1`。
- 通用链路验证数据：报价 `QUO-3DD73A37B674` → ERP 草稿 `SAL-ORD-2026-00024` → 工单 id=10（`WO-SO-2026-00024`，审批 `WOD-AA9E1833517A`）。
- Git Bash 坑：python 脚本内联中文 JSON 用文件 + `--data-binary @file`；反斜杠路径用正斜杠。

## 六、验收标准（完成定义）

1. 任意工单（含新下达的）页面报工后：完成率/produced_qty 变化真实可溯、ETA 口径 RATE_BASED（有速率数据时）；重复报工幂等。
2. 任意工单页面登记质量问题后：质量待办出现新条目（真实 OpenMES 记录），可直接走处置三步闭环。
3. 全程审批留痕（审批号可复述）、回读验证、无未审批写入。
4. 测试基线不下降；文档（current_status_and_fix_plan.md 新 § 节 + demo_script.md）与提交同步。
