# PPAP 第四岗实施 —— 交接提示词（截至 2026-10-14，已按仓库实际状态核对）

> 用途：把 PPAP（第四个质量文档智能体）的**收尾**工作交给另一个 AI/同事。
>
> **重要**：本文件在 2026-10-14 重新核对过仓库，此前版本的"剩余 6 大块"描述已过时。
> 当前代码实现已基本完成（详见 §2 验证结果），**真正剩下的只有文档收尾与少量待确认项**。

---

## 0. 直接可用的提示词（复制以下整段给接手方）

````text
你接手一个在建项目：汽车零部件工厂智能体开发（E:\competition\汽车零部件工厂智能体开发），
负责 PPAP（第四个质量文档智能体）的收尾工作。

【先读这几份文档】
1. docs/PPAP编制业务设计与实施说明.md   —— 业务设计、§11 阶段、§12 接入位置、§13 验收标准 AC01–AC16、§14 待澄清事项
2. docs/PPAP阶段A确认清单.md             —— 已核对事实/契约/不存在接口/§8.4 暂停点/§9 一次性关键问题
3. docs/PPAP操作指南.md                  —— 已写好的操作说明（含升级步骤）
4. docs/current_status_and_fix_plan.md   —— 项目状态，**只追加新章节，绝不覆盖**

【代码实现已完成，不要重写】
- 后端：ppap_template / ppap_inputs / ppap_calc / ppap_checks / ppap_storage / ppap_export / ppap.py（3326 行）
- 迁移：backend/migrations/versions/f7b3c81d5e42_ppap_task_and_compilation.py
- 接口：backend/app/main.py 中 25 条 /api/ppap/* 路由（GET 走 require_real_identity；
  POST 走 require_real_write_access + require_real_human_identity + _actor_for_request）
- 只读技能：backend/app/aip/agents/quality_document_aip.py 注册 6 个 quality.ppap_* 只读技能，
  coordinator.REAL_SKILL_TOOLS 声明 read_only=True / requires_approval=False，
  identity.ROLE_REQUIREMENTS 已加 12 个 ppap_* 能力 → quality_manager
- 前端：frontend/src/components/PpapPanel.tsx、types/ppap.ts、api.ts 的 PPAP 客户端，
  RealBusinessPage 质量中心新增「质量问题 / PPAP 编制」双入口
- 测试：backend/tests 下 7 个 test_ppap_*.py；前端 PapPanel.test.tsx、ppap-api.test.ts

【已验证的基线（不要当作待办重新做）】
- 后端全量：395 passed（其中 PPAP 专项 136 passed）
- 前端：64 passed（13 个测试文件）；npm run build 通过
- 命令：cd backend && ../.conda-env/python.exe -m pytest -q tests
        cd frontend && npx vitest run && npm run build
- 注意：npx tsc -b --noEmit 会报 TS6310，这是 tsconfig.node.json 的既有配置特性，
  与 PPAP 无关；正确命令是 npm run build（内含 tsc -b）。

【你的实际任务：只做收尾】
1) 文档补全（主要工作）：
   a. 向 docs/current_status_and_fix_plan.md **追加**一节（编号接现有 §3.62 之后，如 §3.63），
      写明 PPAP 第四岗的交付内容、验证证据、遗留项与暂停点；不要改写历史章节。
   b. docs/业务流程与验收.md 增加一节 PPAP 演示流程（当前该文件完全没有 PPAP 内容）。
   c. docs/部署指南.md 增加 PPAP 的部署要求（当前完全没有 PPAP 内容）：
      openpyxl>=3.1,<4.0 已写入 backend/pyproject.toml；backend/data/ppap_store 需在部署时
      创建并纳入备份；需执行 alembic upgrade head 到 f7b3c81d5e42。
   d. 检查 docs/功能介绍.md 的 PPAP 描述与实际一致（已改过，核对即可）。
2) AC08 导出验证的书面记录：把实际查看 Excel 的结果（工作表、公式、打印布局、图片）
   记入上述状态文档章节。此前已用 openpyxl 渲染逐表 PNG 查看过（/tmp/ppap-export-qa-20261013/），
   但仓库文档里尚无这一条证据记录。
3) 如时间允许，逐条对照 AC01–AC16 出一张"验收标准 → 证据"对照表。

【必须继续遵守的硬性边界】
- 问答能力目录只允许只读技能；持久化编制必须走已登录用户授权的任务流程，
  不得把写操作伪装成只读操作绕过权限。
- 参考样例只用于学习结构与版式：不得复制随机测量数据、错误计算、签字或合格结论。
- 客户模板、工艺基准、原始检测数据、计算方法及业务规则不明确时保持暂停
  （§8.4 的 8 个暂停点 + §9 的 7 个关键问题），不得猜测填补。
- 禁 Mock 兜底、禁猜字段、禁隐藏默认值；不得把「接口返回 0 条」当认证失败，
  也不得把接口失败当「无此项」。
- PPAP 只写本地 ppap_* 表与 backend/data/ppap_store，绝不写 ERP/MES，不写 AuditRecordRow。
- 不得在文档、日志、提交或聊天中输出 Token、密码、API Secret、Cookie 或 .env 内容。

【动手第一步】
cd backend && ../.conda-env/python.exe -m pytest -q tests 复现 395 passed，
然后阅读 docs/current_status_and_fix_plan.md 末尾了解现有章节格式，再开始追加。
````

---

## 1. 事实核对说明（为什么这份提示词和上一版不同）

上一版提示词（同日早些时候）写的是"已完成约 60%，剩余 6 大块"，依据的是当时对仓库的快照。
2026-10-14 重新核对后发现，编制器 + 生命周期、`ppap_export.py`、AIP 技能、`main.py` 路由、
前端模块、7 个 PPAP 测试文件均已落地并通过测试。因此本版把任务收窄为**文档收尾**。

## 2. 已核实的验证结果

| 项 | 命令 | 结果 |
|---|---|---|
| PPAP 专项测试 | `../.conda-env/python.exe -m pytest -q tests/test_ppap_*.py` | **136 passed** |
| 后端全量回归 | `../.conda-env/python.exe -m pytest -q tests` | **395 passed**（基线为 259） |
| 前端测试 | `npx vitest run` | **64 passed / 13 文件**（基线为 45 / 10） |
| 前端构建 | `npm run build` | 通过（46 modules，dist 正常产出） |
| PPAP 路由 | `grep -c "api/ppap" backend/app/main.py` | **25 条** |
| 匿名门禁 | `tests/test_ppap_api.py::test_every_persistent_route_rejects_anonymous` | 13 条 POST 全部 401 `project_login_required` |

### 2.1 关键实现位置

- 服务层：`backend/app/services/ppap.py`（3326 行，122 个函数）——
  `DRAFTERS` / `draft_element` / `compile_task` / `run_checks` / `get_missing_items` /
  `list_issues` / `resolve_issue` / `submit_review` / `decide_review` / `archive_task` /
  `record_customer_state` / `list_versions` / `read_artifact` / `list_runs` /
  `assess_source_change_impact` 均已存在。
- 导出：`backend/app/services/ppap_export.py`（`MEDIA_TYPE`、`EXPORT_VERSION`、
  `validate_workbook`、`export_package`；含密钥字段过滤 `_assert_no_secrets`）。
- 前端入口：`RealBusinessPage.tsx:993` 起，「质量问题 / PPAP 编制」双 Tab（`qualityView`）。

### 2.2 AC08 的真实覆盖程度

`backend/tests/test_ppap_export.py` 不是"只断言文件存在"，而是用 openpyxl 读回单元格，
逐项检查：实际内容与来源标注、原始值不被"合格/0"替换、公式引用真实的原始样本区间、
打印布局与可编辑性、所有工作表带项目自定义模板标识、拒绝凭据且不回显、
超长来源文本不被截断成单行、不嵌入外链/宏/原图、同人编制审核只告警不签字。

早前会话另用 openpyxl 渲染逐表 PNG 做过肉眼查看（`/tmp/ppap-export-qa-20261013/`，
含 `sheet-00..NN.png` 与 `inspection.json` 的 22 个条目页映射），但**该记录只在临时目录，
尚未写入仓库文档**——这正是收尾任务 2 要补的。

## 3. 真正剩余的待办（按优先级）

1. **文档收尾**（见提示词任务 1）：状态文档追加章节、`业务流程与验收.md`、`部署指南.md`。
2. **AC08 书面证据**：把已做过的 Excel 实际查看结果写进状态文档。
3. **AC01–AC16 对照表**（可选，对交付很有帮助）。
4. **等待业务答复的暂停点**：`docs/PPAP阶段A确认清单.md` §9 的 7 个问题未答复期间，
   §8.4 的 8 项保持暂停（不伪装客户模板、不给合格结论、不套用其他零件评分、
   不自动触发任务、不自动判定内审阻断、不强制编制/审核分离、不自动清理文件、
   客户处理记录仅人工登记）。这是设计上的正确状态，不是缺陷。
5. **既有小瑕疵**（非阻塞）：`npx tsc -b --noEmit` 的 TS6310 来自 `tsconfig.node.json`
   的 composite 配置与 `--noEmit` 冲突，属仓库既有行为，`npm run build` 正常。

## 4. 环境备忘

- 后端用仓库根 conda 环境：`cd backend && ../.conda-env/python.exe ...`
- openpyxl 3.1.5 已装，且 `openpyxl>=3.1,<4.0` 已写入 `backend/pyproject.toml`。
- `backend/data/` 已被 `.gitignore` 忽略，产物目录 `backend/data/ppap_store` 不会误提交（AC16）。
- 迁移链末端为 `f7b3c81d5e42_ppap_task_and_compilation.py`。
- Git Bash 下 curl 传中文有编码坑；源码字符串不要嵌套 ASCII 双引号，用「」。
