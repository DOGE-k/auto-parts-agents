# 交接文档：协同事件去处置联动与工程收口

> 交接对象：下一位负责实现和复核的 AI 或开发者。  
> 项目路径：`E:\competition\汽车零部件工厂智能体开发`  
> 当前分支：`codex/real-integration-layer`  
> 文档日期：2026-10-01  
> 本文只定义下一阶段工作，不代表下列任务已经完成。

## 一、任务目标

当前项目已经具备三类协同事件：

- `quality_issue_raised`：质量异常；
- `material_shortage`：关键物料短缺；
- `production_overdue`：事实型生产延期。

事件面板现在可以查看结论、重试失败事件和人工接管，但人工接管后还不能直接进入对应业务面板。本阶段先补齐“去处置/去处理”联动，再处理文档、数据库回退和前端构建验证的一致性问题。

本阶段不新增业务规则，不新增预警阈值，不改真实 ERP/MES 写入流程，不做能力目录运行时重构。

## 二、接手前必须确认的事实

1. 只使用 `E:\competition\汽车零部件工厂智能体开发`，不要切换到 C: 目录，也不要删除 C: 目录。
2. 当前最新提交为 `425804e`，对应 §3.51。前序相关提交依次为：
   - `0bddd4c`：§3.47 OpenMES 认证自愈；
   - `3079a14`：§3.48 任务型协同问答；
   - `bec22c5`：§3.49 页面级走查与契约修复；
   - `8f9b3e5`：§3.50 质量异常事件；
   - `425804e`：§3.51 缺料与延期事件。
3. 当前历史验证显示 `/api/mes/work-orders` 可以返回 12 条工单，但工单数量属于运行时数据，不能写死到业务规则或页面话术中。
4. 当前 PostgreSQL 业务库已有 `collaboration_events`；默认 `backend/data/demo.db` 仍停在 `b5d9e6a41c77`，缺少 `collaboration_events`。必须先确认项目是否要继续支持 SQLite 回退，再决定补迁移还是明确要求 PostgreSQL。
5. 工作树中存在未跟踪临时文件：
   - `backend/ask_a6.json`
   - `backend/ask_q6.json`
   - `backend/dis_req_tmp.json`
   - `backend/uvicorn-9000.err.log`
   - `backend/uvicorn-9000.log`
   - `frontend/ana1.json`

这些文件不是本阶段代码交付物，不要提交，也不要把其中的凭据或令牌复制到文档、日志或回复中。

## 二点五、接手时必须关注的已知缺口

以下问题来自本次静态审计，不能因为现有单项测试通过就视为已经解决：

1. **数量变更后的旧方案状态**：§3.48 的数量变更会把旧报价、采购方案和工单标记为 `stale_downstream`，但连续执行“数量改成 N”再“选第二个方案”时，仍需确认 `active_plan` 是否被清除，以及选择分支是否拒绝已过期方案。必须补一条连续回归测试；不能让用户继续确认旧方案。
2. **方案快照完整性**：方案序号校验不能只比较 `option_id`、供应商和总价。若交期、覆盖范围、价格依据或物料行发生变化，应要求重新选择；具体比较字段必须依据现有类型和业务契约核对，不要自行发明字段。
3. **人工接管后的事件重触发**：当前去重查询会排除 `MANUAL_HANDLED`，但数据库仍对 `dedup_key` 加唯一约束。接管后同一事件再次触发可能产生唯一约束异常。需要补幂等处理和回归测试，不能让登记或采购主流程因事件重触发失败。

如果本阶段不修以上问题，必须在最终报告和 `current_status_and_fix_plan.md` 中明确列为遗留，不得写成“全部完成”。

## 三、任务 A：事件面板增加“去处置/去处理”

### 3.1 现有代码位置

先阅读，不要猜接口或字段：

- 事件服务：`backend/app/services/collaboration.py`
- 事件 API：`backend/app/main.py` 中 `/api/real-orders/collaboration/events/*`
- 事件类型和接口类型：`frontend/src/api.ts`
- 事件面板：`frontend/src/components/CollaborationEventsPanel.tsx`
- 主业务页和页签状态：`frontend/src/RealBusinessPage.tsx`
- 现有质量待办跳转：`RealBusinessPage.tsx` 中 `goToQualityDispose`
- 质量处置卡片：`frontend/src/components/NcrWorkflowCard.tsx`
- 采购、跟单流程面板：`frontend/src/components/flow/ProcurementFlow.tsx`、`TrackingFlow.tsx`

### 3.2 联动要求

事件面板新增一个明确的“去处置/去处理”按钮。按钮文案可以按事件类型显示，但必须保留事件类型原名和事件编号。

| 事件类型 | 目标 | 最低要求 |
|---|---|---|
| `quality_issue_raised` | 对应工单的 NCR 处置区域 | 复用现有 `goToQualityDispose` 的读取和聚焦逻辑；定位 `work_order_id` 与 `issue_id`；不自动批准、不自动写回 |
| `material_shortage` | 对应订单的采购分析/方案审批区域 | 使用事件结果中的 `quotation_id`、`plan_id` 或现有业务状态反查；字段缺失时显示明确错误，不根据标题或序号猜测 |
| `production_overdue` | 对应工单的跟单/进度区域 | 使用 `work_order_id` 或 `work_order_no`；只展示事实进度和延期结论，不自动承诺客户或修改交期 |

实现时应优先复用现有页签切换、工单加载、NCR 聚焦和流程状态恢复逻辑，避免另写一套导航状态机。事件面板只负责导航和显示，事件协同仍保持只读。

### 3.3 缺字段和失败处理

- 缺少目标工单、问题或业务对象编号时，按钮应给出“缺少关联编号，无法跳转”的可见提示。
- 目标数据读取失败时，保留事件卡片和错误信息，不把失败渲染成空数据。
- 不能从 `title`、事件顺序、当前列表第一项等内容推断目标对象。
- 人工接管只改变事件状态和留痕，不代表 NCR 已处置，也不代表采购方案已批准。
- 事件面板的空状态和说明文字不能只描述质量事件，应覆盖质量、缺料、延期三类事件。

事件触发的幂等处理还必须覆盖“首次完成、重复触发、人工接管后再次触发”三种情况；任何唯一约束异常都应转换成可理解的业务结果，不得静默吞掉或阻断原始业务操作。

### 3.4 前端测试要求

在 `frontend/src/components/__tests__/CollaborationEventsPanel.test.tsx` 增加有意义的测试：

1. 三类事件显示正确类型和目标按钮；
2. 质量事件点击后调用质量处置回调，并传递 `work_order_id`、`issue_id`；
3. 缺料和延期事件分别传递对应的订单/工单上下文；
4. 关键编号缺失时显示错误，不调用错误的导航；
5. 人工接管后仍能看到事件状态和留痕，不能误显示“已处置”。

如果现有组件层级不允许直接传递导航回调，先调整最小的 Props 接口，并保持 `RealBusinessPage` 现有流程行为不变。

## 四、任务 B：文档状态同步

### 4.1 `AI_HANDOFF_PLAN.md`

更新当前状态段和事件协作段：

- §3.51 缺料事件和事实型延期事件标记为已完成；
- 事件“去处置/去处理”标记为本阶段待完成；
- 提前预警阈值继续标记为待业务规则确认；
- 保留“页面级报工/质量登记、干净机器部署和实时外部服务需要重新复核”的边界；
- 不要把“211/21 全绿”写成当前无条件成立，必须注明测试命令和 EPERM 构建限制。

### 4.2 `current_status_and_fix_plan.md`

不要改写历史章节。完成代码和验证后，在文件末尾追加一个新小节，记录：

- 修改文件；
- 事件类型和跳转目标；
- API、HTTP 状态码和数据 authority；
- 是否发生 ERPNext/OpenMES 写入；
- 页面验证结果和截图路径；
- 后端、前端测试命令和实际结果；
- SQLite/PostgreSQL 选择及迁移结果；
- 尚未解决的问题。

### 4.3 `demo_script.md`

只在页面实际验证通过后补充事件操作路径。不要把历史截图或历史事件编号写成当前固定数据。

## 五、任务 C：SQLite 回退一致性

先检查 `backend/app/persistence/database.py` 的初始化和迁移策略，再执行修改。目标是明确且可复现：

- 如果项目继续支持 SQLite 回退：让全新的 SQLite 数据库和现有 `backend/data/demo.db` 都能升级到 `c7e2f9a84d15`，并验证事件列表、详情、重试、接管至少能读写本地业务库；
- 如果项目正式要求 PostgreSQL：不要伪装 SQLite 可用，更新启动检查、README 和交接文档，明确缺少 `collaboration_events` 时如何提示。

不允许只在测试夹具中用 `Base.metadata.create_all` 掩盖迁移缺失。测试夹具通过不等于真实回退库可启动。

## 六、任务 D：构建和测试收口

### 6.1 后端

```powershell
cd E:\competition\汽车零部件工厂智能体开发\backend
..\.conda-env\python.exe -m pytest tests -q
..\.conda-env\python.exe -m compileall -q app
```

当前基线：`backend/tests` 应为 211 passed。仓库根目录旧的 `test_erp_mes_integration.py` 依赖 8001 端口的旧运行服务，不要把它和隔离测试混为一谈；如果要修复它，单独记录范围，不要为了让数字变绿而关闭测试。

### 6.2 前端

```powershell
cd E:\competition\汽车零部件工厂智能体开发\frontend
npm run test -- --run
npx tsc --noEmit --incremental false --project tsconfig.json
npm run build
```

当前已知状态：Vitest 21 passed；源代码类型检查可以通过；`npm run build` 曾因 `tsconfig.tsbuildinfo` EPERM 失败。先定位项目相关进程或文件锁，再重跑 build，不要删除不明文件，也不要停止无关服务。

### 6.3 本地只读冒烟

如果本地 9000 服务已经运行，只做以下读取检查：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/api/health
Invoke-RestMethod http://127.0.0.1:9000/api/runtime/surface
Invoke-RestMethod http://127.0.0.1:9000/api/mes/work-orders
Invoke-RestMethod http://127.0.0.1:9000/api/real-orders/collaboration/events
```

核对 `real_business_enabled=true`、真实 authority、错误不被转换为空列表，以及事件详情含 `result`。不要在本阶段为造数据而调用真实写入接口。

## 七、严格边界

1. 不联网搜索，不调用外部数据库或外部应用。
2. 不猜 OpenMES 字段、接口、业务公式或预警阈值；源码和真实响应不一致时停下调查。
3. 不新增第五个业务 Agent；Leader/Partner 只是动态协作角色。
4. 不修改 ERPNext 订单、不创建 OpenMES 质量问题、不报工、不补录 TEST 数据，除非用户另行明确批准并且交接者先报告具体写入对象。
5. 事件分析和导航必须保持只读；任何批准、写回、回读仍走既有审批门禁。
6. 不提交 `.env`、令牌、密码、临时 JSON、uvicorn 日志或包含凭据的截图。
7. 不把历史运行编号、历史工单数量或历史截图当作当前保证。

## 八、完成定义

本阶段只有同时满足以下条件才算完成：

1. 三类事件都能从事件面板进入正确的业务区域，且缺字段、读取失败都有明确反馈；
2. 事件面板不自动执行审批或写入，人工接管与“已处置”状态没有混淆；
3. 前端事件组件回归测试覆盖质量、缺料、延期和错误路径；
4. `backend/tests`、前端 Vitest、源代码类型检查、`npm run build` 的实际结果均已记录；
5. SQLite 回退或 PostgreSQL 强依赖的选择已实现并写入文档；
6. `AI_HANDOFF_PLAN.md`、`current_status_and_fix_plan.md` 和需要时的 `demo_script.md` 已同步；
7. Git 状态中没有把临时文件误加入提交；
8. §3.48 连续“数量变更 → 重新选方案”回归通过，或已明确记录遗留；
9. 事件“人工接管 → 再次触发”幂等回归通过，或已明确记录遗留；
10. 最终报告明确区分：隔离测试、页面走查、本地真实只读检查和真实写入验收。

## 九、明确不做

- 不做提前 N 天延期预警；
- 不做完成率阈值设计；
- 不做 AIP/ACS 能力目录运行时重构；
- 不做异步队列改造；
- 不做 PPAP、IMDS、质量报告归档；
- 不做生产部署、SSO 或外部注册发现；
- 不为通过测试而关闭旧集成测试或伪造服务响应。
