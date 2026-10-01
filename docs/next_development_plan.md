# 下一阶段开发计划与验收标准

更新时间：2026-09-30

> 本文件保留为早期阶段计划和验收记录。后续 AI 接手时，请先阅读 `docs/AI_HANDOFF_PLAN.md`；当前路线、优先级和边界以该文件为准。本文件中未勾选的旧任务不代表仍然是当前待办，必须结合 `docs/current_status_and_fix_plan.md` 最新小节重新核对。

## 目标

让报价、采购、跟单、质量文档四个 Agent 基于本地 ERPNext 和 OpenMES 的真实接口完成一条可追溯业务链：

```text
ERP 销售订单 → MES 工单 → 报价 → 人工审批 → ERP 销售订单草稿
→ 缺料分析 → 采购方案审批 → ERP 采购订单草稿
→ MES 生产跟单 → 质量资料与质量门禁 → 发运判断
```

测试数据可以使用，但必须标记为测试数据；接口不存在、字段缺失或数据为空时必须明确阻断或转人工处理，不能使用固定数字冒充真实结果。

## 当前基线（2026-09-29 更新）

- ERPNext、OpenMES 和后端真实适配器已经连通。
- ERP↔MES 订单关联已完成：`WO-2026-001 → SAL-ORD-2026-00001`。
- 报价、采购、跟单主链已经接入真实数据并能生成 ERP 草稿。
- 报价、采购方案、审批和 Agent 运行记录已经持久化。
- Mock 页面已经标注并与真实业务入口区分。
- 质量门禁已完成两条真实路径验证：原工单在问题未解决/文档缺失时正确阻断；测试工单 `TEST_WO_QUALITY_003` 已通过问题、文档和官方批次工序完工路径，质量门禁与发运门禁均通过。
- 质量问题按工单过滤的映射修正已重启复验（见 current_status_and_fix_plan.md §3.13 前置复验）。
- **页面级双场景验收已完成（§3.13）**：场景 A 阻断/场景 B 放行均有页面截图；Agent 运行记录已可在页面查询（含证据链展开）。
- **测试与安全（阶段四）已完成**：pytest 独立测试库（42 passed，业务库不被触碰）、超时/网络/5xx/404 失败映射测试、无硬编码凭据。

## 开发顺序

### 阶段一：质量数据范围与阻断场景（✅ 已完成 2026-09-29）

1. ~~重启后端，验证 `GET /api/real-orders/quality/package/2`~~ —— 已验证：质量记录仅含 `WO-2026-001`，跨工单问题已过滤。
2. ~~确认每条质量记录都属于 `WO-2026-001`~~ —— 通过（`work_order_no` 匹配）。
3. ~~验证阻断原因显示~~ —— WO-2026-001：SOP/Control Plan 缺失 + 0% 进度；测试工单 8/9：0% 进度阻断。
4. ~~浏览器走查与截图~~ —— 场景 A 截图 `gui-test-screenshots/2026-09-29_scenarioA_ship_gate_blocked.png`。

### 阶段二：质量通过测试场景（✅ 核心路径已完成 2026-09-28/29）

已完成：resolve 审批写回、SOP/Control Plan 上传发布并验证继承（新工单冻结快照自动包含）、官方批次工序完工路径（TEST_WO_QUALITY_003 及页面走查工单 TEST_WO_PAGE_00023 补产 90%）。

页面级场景 B 已完成：同一工单补产后页面发运门禁"可以发运"（截图 `2026-09-29_scenarioB_ship_gate_passed.png`）。

剩余（不阻塞双场景验收）：

- close/disposition 及纠正措施校验接入；
- 真实用户/角色身份来源（已完成：ERPNext/OpenMES 当前用户 + 角色门禁；企业 SSO 接入仍可沿用同一 provider 接口）；
- 检验记录数据补录（当前 0 条，不阻断门禁）。

### 阶段三：四 Agent 页面和证据链（✅ 已完成 2026-09-29）

- ~~每个 Agent 页面显示数据来源、接口、记录编号和时间~~ —— 各面板证据链 + Agent 运行记录面板（输入/证据链/错误/时间，可按类型过滤，跨重启可查）。
- ~~写入操作必须有人工审批和回读结果~~ —— 审批门禁 + 回读验证显示（"✓ 已确认"）。
- ~~Agent 运行记录能从页面查询，服务重启后仍存在~~ —— 本轮新面板实现并实测。
- ~~真实页面不得出现 Mock 数据或 synthetic_demo_only~~ —— Mock 徽标+横幅隔离在 6 个 Mock 页面。
- ~~ERP 草稿进入跟单前必须精确关联~~ —— `customer_order_no` 精确匹配 + 无匹配禁用并提示（本轮走查同时补齐了关联建立后的工单列表刷新按钮）。

### 阶段四：稳定性与安全（✅ 已完成 2026-09-29）

- ~~全量后端测试、语法检查和前端构建~~ —— 42 passed / compileall 通过 / npm build 通过。
- ~~缓存文件权限导致的编译检查阻塞~~ —— 旧进程退出后自行解除。
- ~~清理未跟踪脚本中的硬编码凭据~~ —— 扫描确认全部从 .env 读取，无暴露。
- ~~对 ERP/MES 连接失败、权限不足、空数据和超时分别做失败验证~~ —— 传播/403/503/DATA_MISSING/timeout/network_error/5xx/404 测试覆盖。
- ~~为 pytest 固定独立可写测试数据库~~ —— `tests/conftest.py` 临时 SQLite（实测业务库 mtime 不变）。

## 双场景最终验收（✅ 已完成 2026-09-29，页面级）

### 场景 A：应阻断 —— 已验证

SAL-ORD-2026-00023 / TEST_WO_PAGE_00023（id=9）0% 进度：发运门禁"禁止发运"，报价审批 ✓ / 质量门禁 ✓ / 生产进度 ✗ (0%<90%)，阻塞原因清晰。截图 `gui-test-screenshots/2026-09-29_scenarioA_ship_gate_blocked.png`。

### 场景 B：应通过 —— 已验证

同一工单按官方批次工序接口补产 1800/2000（90%）：三项门禁全 ✓，"可以发运"。API 交叉验证 `can_ship=true`。截图 `gui-test-screenshots/2026-09-29_scenarioB_ship_gate_passed.png`。该场景为 TEST_ 标记测试数据，不代表生产数据。

## 阶段八：质量异常协同 + 审批一致性（2026-09-30 已完成——进度记录见 current_status_and_fix_plan.md §3.17/§3.18/§3.20）

### 背景与目标

两件事合并为一个阶段：

**A. 审批一致性修复**（§3.16 验收发现的遗留）：问答链的报价 analyze 后未走报价审批，方案卡片却可批准方案并起草 PO——出现"报价 DRAFT vs 方案 APPROVED"的治理不一致（协调者自己都指出了）。修复：方案卡片批准时，若关联报价未审批，确认面板**同时包含两条审批线**（报价审批 + 采购方案审批，各自生成审批记录），一次人工确认、两笔留痕，不静默跳过。

**B. 例子三（质量异常协同）落地**（讨论稿最后未实现的例子）：问"这个工单有质量异常吗？影响交付吗？怎么办？"→ 协调者动态链：质量包（问题/文档/门禁）→ 跟单进度 → 发运门禁 → 质量异常影响分析 → 输出：异常清单（严重度/状态/批次关联）、对交期与发运的影响、可执行的处理选项（真实能力边界内：已接入的 resolve 审批写回通道指引 + 8 步流程），数据缺口如实标注（检验记录 0 条、NCR 完整处置 NOT_SUPPORTED）。

### 任务清单与当前状态（2026-09-30 收尾）

| # | 任务 | 状态 |
|---|------|------|
| 1 | 新真实技能 `quality.assess_quality_impact(work_order_id)`：质量问题清单 + 批次关联（MES 客户端/适配器新方法 `list_work_order_batches`/`get_work_order_batches`，base.py 协议与 mock.py 空实现同步）+ 门禁 + 生产进度交叉 → 影响结论；数据缺口如实列出（检验 0 条/SN 无 API/NCR NOT_SUPPORTED/批次读取失败不伪造为空） | ✅ 完成 |
| 2 | AIP 接线（quality_document_aip.py 注册 `quality.assess_quality_impact`）；协调者工具目录新增该技能；`_collect_proposal` 汇集 `quality_impacts` 与 `quotation_status` | ✅ 完成 |
| 3 | 前端：方案卡片双审批线确认面板（`quotation_status !== "APPROVED"` 时追加报价审批行与审批人输入，`approveQuotationApi`→`approveProcurementPlan`→`createPoFromPlan`，回显两笔审批号）；质量影响问答区块（异常表/批次/结论/处理选项/数据缺口）；api.ts 类型（QualityImpactResult 等） | ✅ 完成，build 通过 |
| 4 | （可选，**待用户批准**）seed 脚本补录 TEST_ 检验记录 | ⬜ 未做（用户未批准，跳过） |
| 5 | 测试：质量影响、编号→ID 桥接、协调者调用链、报价状态一致性、审批/草稿幂等、方案覆盖回归；全量 **83 passed**，compileall/build 通过 | ✅ 完成 |
| 6 | 真实验收 | ✅ DeepSeek 缺料问答、双审批、PO 草稿创建/回读、状态问答均已通过（运行记录见 §3.20） |
| 7 | 文档 §3.18/§3.20（已更新）+ 记忆 + git 提交 | ⚠️ 文档完成；**git 未提交**（阶段八改动全部在工作区） |

### 收尾记录

**已完成的只读验收**：AIP `tracking.find_real_by_no` 已将 `WO-2026-001` 精确转换为数字 `work_order_id=2`；随后直接调用 `quality.assess_quality_impact` 已返回真实质量/门禁/生产数据和缺口。协调者的假 LLM 调用链测试确认编号桥接先于质量查询。

**暴露的真实缺口**：用户自然语言说的是工单**编号**（WO-2026-001，实际数字 id=2），而全部 MES 查询工具只接受数字 id——缺"编号→id"桥接。

**已完成的修复**：`find_work_order_by_no` 已注册 AIP、加入协调者目录与提示，并补齐真实适配器假数据测试和协调链测试；采购分析已携带独立 `quotation_status`，审批/草稿重复执行会幂等回读。
- **已完成验收步骤**：DeepSeek 缺料问答 → 双审批 → 两笔 approval_id/PO 草稿回读 → 状态查询复核 → 全量回归；结果记录见 current_status_and_fix_plan.md §3.20。

**注意事项**：
- 前端双审批线逻辑依赖 `proposal_options.quotation_status`，由 `_collect_proposal` 从采购分析结果携带；该字段现在明确取自报价记录，和采购方案自身的 `status` 分离。
- 验收 Q6 期望值以真实 OpenMES 当时的数据为准（质量问题状态可能变化），不硬编码断言；
- 测试命令：`cd backend && ../.conda-env/python.exe -m pytest tests -q`（当前 83 passed）；前端 `cd frontend && npm run build`；后端重启命令见 §2。

### 数据前提（需用户决策）

- 检验记录 0 条：如需在质量异常分析中展示 IQC/IPQC 检验数据，需要用户批准 seed 脚本向 OpenMES 补录 TEST_ 标记检验数据（参照 seed_supplier_data 幂等模式）。**未批准前，检验维度如实显示"无数据"。**

### 验收标准（本阶段）

1. 质量异常问答输出的问题/门禁/批次信息全部可溯真实记录；无检验数据时如实说"无检验数据"，不编造；
2. 卡片批准后报价与方案两侧审批状态一致（各自有 approval_id），协调者状态查询不再报不一致；
3. 协调者不开放质量写回（resolve 指引到既有审批通道）；83 passed 不回归。

### 明确不做

- 不开放 NCR close/disposition（仍 NOT_SUPPORTED 如实标注）；
- 不做供应商质量表现聚合（问题记录无供应商字段，推断关联被禁；待 MES 数据模型支持）；
- 身份治理（approved_by 自报）另立任务。

## 阶段七：执行闭环——方案批准与草稿起草（2026-09-29 立项，已完成 ✅）

> 验收记录见 current_status_and_fix_plan.md §3.16。全部任务完成：assess_combination 确定性组合
> 评估（重复覆盖告警）；方案卡片批准→PO 草稿闭环（PUR-ORD-2026-00009/00010 回读确认）；
> 验收发现并修复状态联动断链（source_erp_order_id 声明式上下文 + find_real_plan_by_quotation）；
> 状态联动复验通过（协调者 2 步链查到审批号/PO 草稿号）；69 passed。
> 新遗留：问答链报价未走报价审批（与方案侧审批状态不一致），列为下一迭代。

### 背景与目标

讨论稿闭环的最后一步："形成几套可执行方案 → 关键节点交给人确认 → **再回写 ERP/MES 或继续跟踪**"。阶段六方案对比止步于"需人工确认"；本阶段把确认动作接进既有审批门禁，形成完整闭环：

```text
方案对比卡片 →「选择此方案并起草 PO（需人工确认）」→ 确认对话框（明示写入内容：
供应商/物料/金额/docstatus=0 草稿/审批人）→ 调用既有审批门禁端点（审批记录持久化）
→ 创建采购订单草稿（ERPNext docstatus=0，不提交）→ 回读确认 → 卡片回显草稿编号
→ 问协调者"方案批准了吗/PO 生成了吗"可查到最新状态（闭环联动）
```

硬规则不变：人工审批是真实点击确认，审批记录（approval_id/approved_by）持久化；草稿不提交；回读验证；协调者本身仍无写权限。

### 任务清单

| # | 任务 | 状态 |
|---|------|------|
| 1 | 新增真实技能 `procurement.assess_combination(plan_id, option_ids[])`：确定性计算分单采购组合（覆盖并集/组合成本=真实行项目求和/最长交期），供协调者与卡片引用，防止 LLM 自行拼数 | ✅ |
| 2 | 协调者：能力目录加 assess_combination；prompt 补"组合方案必须用该技能计算，不得自行相加" | ✅ |
| 3 | 前端方案卡片执行闭环：确认对话框（写入内容明示+审批人输入框）→ 顺序调用既有 `approve` 与 `po-from-plan` 端点 → 回读状态与草稿编号回显 → 已批准/已起草状态防重复提交 | ✅ |
| 4 | 后端辅助：`GET /api/real-orders/procurement/plans/{plan_id}` 已有（复用）；按 quotation 查最新 plan 查询已接入 | ✅ |
| 5 | 测试：assess_combination 确定性计算与覆盖并集（假数据）；重复审批防护；全量回归 | ✅ |
| 6 | 真实验收：问缺料方案 → 卡片批准 → PO 草稿编号回读（ERPNext docstatus=0）→ 问协调者"SAL-ORD-2026-00023 的方案批准了吗"验证状态联动；调用链留痕 | ✅ |
| 7 | 文档 §3.16 + 计划勾稽 + 记忆 + git 提交 | ⚠️ 文档已完成，git 提交待统一收尾 |

### 验收标准

1. 从"提问给方案"到"卡片批准 → PO 草稿（docstatus=0）回读确认"全程可在问答面板完成，写入记录（approval_id、PO 草稿编号）可追溯；
2. 重复点击/已批准方案不能二次审批或重复起草；
3. 协调者对组合方案的成本/交期引用全部来自 assess_combination 的确定性结果；
4. 全程 ERP 仅草稿级写入（docstatus=0），无提交/发运类动作；64 passed 不回归。

### 明确不做（本阶段边界）

- 不做订单提交（docstatus=1）、发运执行——竞品演示止于草稿级；
- 不做销售订单方向的起草闭环（8 步流程已覆盖，问答侧写入先只开采购方向）；
- 真实用户/角色体系（approved_by 仍为页面输入的身份声明，治理项另立）。

## 阶段六：方案化协同——从"回答问题"到"给出可执行方案"（2026-09-29 立项，已完成 ✅）

> 验收记录见 current_status_and_fix_plan.md §3.15。全部任务（0-7）已完成：git 提交 3a14196；
> 新增 3 个真实技能（成本影响/交期影响/订单反查报价）；协调者方案合成（11 个真实工具、
> proposal_options 结构化汇集）；前端方案对比卡片 + 回答 Markdown 渲染；64 passed；
> 真实验收：缺料 ≥3 套方案对比（含批判性口径审查）、加急场景诚实回答（物料不是瓶颈、
> ETA 缺排程数据不伪造）。

### 背景与目标

讨论稿第 5 节定义的协同套路：发现问题 → 找到相关智能体 → 补齐数据 → **形成几套可执行方案** → 关键节点交给人确认 → 回写或继续跟踪。阶段五已打通前三步（协调者动态调用真实智能体），本阶段补齐后三步：

- **例子二（缺料）**：跟单发现关键物料缺货 → 采购查备用供应商（B 供应商 2 天可到但成本 +6%）→ 报价评估成本影响 → 输出两套方案（等原供应商延期 X 天 / 换供应商加 Y 元按时交），由人选择；
- **例子四（加急）**：客户要求提前交货 → 跟单查产能排程 + 采购查物料提前到货 → 输出加急方案（最快几天/增加成本/风险点）；
- 每套方案的数量全部来自真实 ERP/MES 记录（供应商特定价、lead_time_days、工单排程），标注依据与"需人工确认"。

### 架构增量

```text
用户问题（"缺料了怎么办"/"能不能提前交"）
  → 协调智能体（新增方案合成模式）
      工具调用（同阶段五，动态链）
  → 新增真实技能：
      quotation.assess_cost_impact  报价智能体：成本影响评估
        （输入：基准报价 + 缺料方案增量；输出：订单毛利/总价影响，全真实价格记录）
      tracking.assess_delivery_impact  跟单智能体：交期影响评估
        （输入：工单排程 + 物料到货时间；输出：预计完工 vs 订单交期的差值天数）
  → 协调者合成方案集：
      {scenario, options: [{id, 摘要, 成本影响, 交期影响, 风险, 真实记录依据, needs_human_confirmation}], 推荐}
  → 前端方案对比卡片（每张卡片显示数据依据，"需要人工确认"标注）
```

### 任务清单

| # | 任务 | 状态 |
|---|------|------|
| 0 | git 提交当前全部工作（分支 codex/real-integration-layer，保护已完成成果）——**需用户确认** | ⬜ 待用户确认 |
| 1 | 新增真实技能 `quotation.assess_cost_impact`（真实价格记录计算缺料方案对订单总价/毛利的影响；无价格数据时 DATA_MISSING 明确拒绝） | ⬜ |
| 2 | 新增真实技能 `tracking.assess_delivery_impact`（物料 lead_time vs 工单 due_date/剩余工序，输出延期或提前天数；无排程数据时明确报缺失） | ⬜ |
| 3 | 协调者新增方案合成模式：能力目录补两个新技能的用途描述（"缺料/加急/需要给方案时使用"）；输出增加 `options` 结构；system prompt 补"方案必须给可执行选项+数据依据+人工确认点，不给单一报警" | ⬜ |
| 4 | 前端方案对比卡片：options 渲染（成本/交期/风险/依据/确认标注）；写入类动作仍不开放（只展示"去 8 步流程执行审批"指引） | ⬜ |
| 5 | 测试：两个新技能的接线与 DATA_MISSING 分支（假适配器）；方案合成（假 LLM 驱动 → options 结构断言）；全量回归 | ⬜ |
| 6 | 真实验收（DeepSeek 驱动）：问 3"TEST_WO_PAGE_00023 缺料了怎么办，给我几套方案"（期望：缺料数据 + ≥2 套供应商方案 + 成本/交期影响 + 推荐与依据）；问 4 加急场景；方案卡片截图；调用链留痕 | ⬜ |
| 7 | 文档 §3.15 记录 + 记忆更新 | ⬜ |

### 验收标准（本阶段）

1. 例子二场景能输出 ≥2 套方案，每套方案的成本/交期数字可追溯到真实价格记录与 lead_time 数据；
2. 数据不足时明确报缺失（如某物料无备选供应商→如实说"无可行替代方案，需补录"），不编造第三套方案凑数；
3. 方案卡每个选项标注"需人工确认"，协调者不执行任何写入；
4. 全量测试通过，既有问答能力不回归。

### 明确不做（本阶段边界）

- 协调者不做写操作执行器（方案→执行仍走 8 步流程的人工审批门禁；"协调者起草写入提案→人批准→执行"列为阶段七候选）；
- 质量异常场景（例子三）依赖批次/SN 追溯与供应商质量表现数据，数据补录后另立任务；
- A2A 直连调用（跟单直接调采购，不经协调者）——协调者中心拓扑已满足当前场景，直连拓扑列为后续架构增强。

## 阶段五：动态协同——协调智能体（2026-09-29 立项，已完成 ✅）

### 背景与目标

团队讨论稿《汽车零部件四智能体功能与流程_团队讨论稿_动态协作版》（用户提供于 2026-09-29）的核心要求：**Agent 调用拓扑与业务生命周期分开**——不写死"下一步必须找谁"，而是智能体根据当前任务判断缺少什么信息，通过 AIP 发现能提供该能力的智能体并按需调用。

用户 2026-09-29 明确的交互形态（原话归纳）："我输入这个订单什么时候能做完，智能体就会调用里面的四个智能体去进行处理，然后告诉我还要多久"——即：

- 用户只给自然语言问题，不按固定步骤操作；
- 协调智能体动态调用四个职能智能体（走 AIP 协议），全部读取真实 ERP/MES 数据；
- 回答带证据（真实记录编号），调用链全程留痕；
- **同一入口在不同问题/数据状态下形成不同的调用链**——这是"没写死"的验收证据。

### 架构

```text
用户问题（前端"智能协同问答"面板）
  → POST /api/real-orders/assistant/ask {question, context?}
  → 协调智能体 BusinessCoordinator（backend/app/services/coordinator.py）
      DeepSeek 工具调用循环（system prompt：禁编造数字/只读/数据缺失要如实说）
      能力目录 REAL_SKILL_TOOLS（8 个真实技能 + 用途描述 + 参数 schema，协调者按需选择）
  → 每个工具调用走 AIP RPC（acps_sdk start→poll→complete）
      POST /aip/{agent}/rpc → AipAgentService 技能处理器
  → 技能处理器调用 real_order 真实业务函数（真实 ERPNext/OpenMES，子调用各自留痕）
  → 汇总中文回答 + 调用链（caller→callee→技能→参数→结果摘要→耗时）
  → 协调运行记录持久化（real_agent_runs，agent_type=coordinator）
```

### 任务清单

| # | 任务 | 状态 |
|---|------|------|
| 1 | 四个 AIP 智能体新增**真实技能**（real_order 只读函数薄封装）：`quotation.analyze_real`/`quotation.get_real`、`procurement.analyze_real`/`procurement.get_real_plan`、`tracking.track_real`/`tracking.lookup_order_link`/`tracking.check_real_ship_gate`、`quality.get_real_package`（原 Mock 演示技能保留并注释区分，隔离另行处理） | ✅ 已完成 |
| 2 | 协调智能体 `BusinessCoordinator`：DeepSeek 工具循环、能力目录（含 aip_agent 路由键）、AIP RPC 调用、调用链留痕、协调运行持久化 | ✅ 已完成 |
| 3 | API `POST /api/real-orders/assistant/ask`；DEEPSEEK_API_KEY 未配置 → 503 明确报错（不伪造回答）——实测 503 行为正确 | ✅ 已完成 |
| 4 | 前端"智能协同问答"面板（真实业务页顶部）：输入框 + 回答 + 调用链可视化 + agent-runs 支持 coordinator 类型 + 结构化错误显示修复 | ✅ 已完成 |
| 5 | 测试 `tests/test_coordinator.py`（10 例，全量 52 passed）：假 LLM 循环/失败回喂/轮次上限/目录↔AIP 一致性/无 key 报错/真实技能接线 | ✅ 已完成 |
| 6 | 真实验收（DeepSeek 驱动两问动态性）：问 1"什么时候能做完"（2 步链：查关联→进度，回答含 90%/1800/2000/ETA 与记录编号，主动发现 PENDING vs 90% 数据不一致）；问 2"为什么还不能发运"（4 步链：+质量包+发运门禁，正确推断 quotation_approved=false，定位唯一阻塞为订单未审批）——**两条链不同，动态性达成** | ✅ 已完成（2026-09-29） |
| 7 | 文档记录 §3.14（真实记录编号、调用链、截图 `2026-09-29_assistant_dynamic_coordination_q2.png`） | ✅ 已完成 |

### 验收标准（本阶段）

1. 同一入口两个不同问题产生两条**不同**的真实调用链，留痕可查；
2. 回答中每个数字都能追溯到工具返回的真实记录（authority/evidence）；
3. 全程只读，不产生任何 ERP/MES 写操作；
4. LLM 未配置或调用失败时明确报错，不伪造回答。

### 明确不做（本阶段边界）

- 不接 Wutong 外部注册发现（WUTONG_* 环境变量已备，列为后续任务）；
- 不开放写操作通道（人工审批门禁仍只在 8 步流程）；
- 不删除 Mock 演示技能（旧 Mock 后端隔离是独立任务）；
- ACS 能力描述文件已由 `generate_acs.py` 按真实能力目录同步，机器可读 ACS 与 AIP 注册表一致（见 current_status_and_fix_plan.md §3.21）。

## 阶段九：质量待办面板（2026-09-30 立项，待开发）

### 背景与目标

NCR 处置/关闭的操作链（审批 → 写回 → 回读）与页面操作面板均已完成（§3.29、§3.32），但操作入口藏在"8 步流程 → 跟单质量"步骤内，必须先进入具体工单才能看到。真实工厂（IATF 16949/MRB 流程）的标准形态是：质检角色打开系统先看到**跨工单的质量待办队列**，点单条进入处置。本阶段把这一形态补齐。

### 真实契约依据（已核实）

- OpenMES `GET /api/v1/issues`（vendored 源码 `routes/api.php:663`，`Api/V1/IssueController::index`）：原生支持 `status` 过滤（`OPEN/ACKNOWLEDGED/RESOLVED/CLOSED`）、`work_order_id`、`line_id`，返回含 `issueType/reportedBy/assignedTo/workOrder/batchStep` 关联。
- 现有后端能力：`quality_package(work_order_id)`、`assess_quality_issue_closure`、disposition/close 全链路端点（§3.29 列表）。
- 现有 OpenMES 适配器只有按工单取质量记录的 `get_quality_records(batch_scope)`；**没有**跨工单列表方法，需要新增。

### 任务清单

| # | 任务 | 状态 |
|---|------|------|
| 1 | OpenMES 客户端/适配器新增 `list_open_issues(status?)`：调用 `GET /api/v1/issues`（建议一次取 `OPEN,ACKNOWLEDGED,RESOLVED` 三态，CLOSED 不进待办）；字段映射为统一 issue 结构（issue_id、work_order_id、work_order_no、title、severity、status、disposition、reported_at、assigned_to）；读取失败如实报错，不回退空列表冒充"没有待办" | ✅ 完成（契约按 2026-09-30 实测 payload 映射；附带修复 OpenMES 客户端 Accept 头） |
| 2 | `real_order.py` 新增 `quality_todo_list()` 聚合函数：列表 + 每条附 `closure_ready` 捷径信息（复用 closure-check 逻辑可选，避免 N+1 可先只给状态）+ 数据缺口如实标注 | ✅ 完成（先只给状态与 disposition，reported_days 按天计算；避免 N+1） |
| 3 | 新端点 `GET /api/real-orders/quality/todo`：只读，走 `require_real_identity`，返回 `{items, authority, data_source}` | ✅ 完成（含 authenticated_identity 审计字段） |
| 4 | 前端"质量待办"面板：真实业务页新增区块（或侧栏入口），表格列：工单号、问题标题、严重度、状态、已报告天数（超 3 天标红）、操作按钮"去处置"→ 跳转/展开既有 NCR 操作面板并预选该 issue；仅登录用户可见该入口（无会话时显示"登录后查看质量待办"） | ✅ 完成（面板位于智能协同问答下方；去处置按真实报价状态加载工单视图并高亮 NCR 卡片） |
| 5 | 测试：适配器假数据测试（三态过滤、失败传播）、聚合函数测试、端点接线测试；全量回归不下降 | ✅ 完成（test_quality_todo.py 8 例；全量 125 passed，117 基线未下降） |
| 6 | 真实验收：`GET /api/real-orders/quality/todo` 返回 OpenMES 真实 issue（当前已知 issue 1 为 RESOLVED+disposition=pending，必须出现在待办里）；页面截图存证 `gui-test-screenshots/` | ✅ 完成（真实返回 issue 1 + issue 2 两条；截图 2026-09-30_phase9_quality_todo_panel.png 与 2026-09-30_phase9_todo_go_dispose_ncr_focused.png） |
| 7 | 文档：`current_status_and_fix_plan.md` 追加验收记录（接口、状态码、authority、是否写入、回读）+ 本表勾稽 | ✅ 完成（§3.33） |

### 验收标准

1. 待办列表数据全部来自 OpenMES `GET /api/v1/issues` 真实记录，带 `authority=OpenMES`；接口失败明确报错，不用空数组冒充；
2. 无登录会话时不显示待办内容（或明确提示需登录），有会话时正常显示——与请求级身份治理一致；
3. 点击"去处置"能到达既有 NCR 操作面板，处置流程（审批→写回→回读）不回归；
4. 全量后端测试 + `npm run build` + `compileall` 通过。

### 明确不做（本阶段边界）

- 不做待办分派/指派改派（assignedTo 编辑）——OpenMES 契约里改指派是 `PATCH /issues/{id}`，涉及写权限设计，另立任务；
- 不做推送/消息通知；
- 不在本阶段执行真实 disposition 写入（仍等业务决策，见执行计划第 2 项）。

## 阶段十：工厂级工程质感（2026-09-30 立项，比赛定位）

> 定位：仅为比赛——目标是让系统在评委审视下站得住"工厂级"标准，不做真实试点部署（HTTPS/真实账号/监控告警等生产部署项跳过）。

### 任务清单

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 1 | NCR 工作流状态刷新后恢复：后端新增按 issue 查询在途处置/关闭审批的只读端点，前端加载质量面板时合并恢复（消除"处置到一半刷新页面状态丢失"） | ✅ | `GET /api/real-orders/quality/workflow-states`；前端 loadTracking/去处置后自动恢复审批号、批准态与表单处置值 |
| 2 | 会话过期提醒：登录时记录 issued-at，过期前 90 秒横幅提醒；会话过期后身份区明确提示"会话已过期请重新登录" | ✅ | 13.5 分钟阈值 + 20s 轮询；历史无签发时间的会话保守立即提醒 |
| 3 | 错误码统一（OpenMES 停机时 quality/package 500 → 502）+ track 不可达措辞修正 | ✅ | package 走 `_integration_status_error`；track 新增 `get_work_orders_strict` 可达性探测，不可达返回 `MES_UNREACHABLE` + 如实缺口 |
| 4 | 检验维度按工单批次 lot 关联过滤（消除"全局检验计数"失真）+ seed 补录对齐批次 lot | ✅ | 精确相等或前缀扩展均算关联；`gate_details` 新增 inspections_total/scope；seed lot 改为批次前缀；回归测试锁定 |
| 5 | 前端组件拆分：RealBusinessPage（2300+ 行）按面板抽出组件 | ✅ 全部完成：共享类型 `types/realBusiness.ts`；面板 `AgentRunsPanel` / `AssistantPanel` / `NcrWorkflowCard`；NCR 状态机 `hooks/useNcrWorkflows.ts`；8 步流程 `components/flow/{Quotation,Procurement,Tracking}Flow.tsx`；主文件 2368 → 772 行，每步 vitest+build 验证 + 页面冒烟 | 可维护性 |
| 6 | 前端关键行为 vitest 测试（会话注入/markdown 渲染/写入头） | ✅ 6 passed | 工程信号 |
| 7 | 业务库迁移 PostgreSQL（生产级数据库信号，最后做，保留 SQLite 回退） | ✅ 17 表 2765 行迁入 autoparts-db，alembic stamp，应用全链验证 | 工程信号 |

### 验收标准

1. 处置到一半刷新页面，既有审批与进度在面板中恢复，不产生重复审批（数据层幂等 + 状态层恢复）；
2. 会话过期前有提醒、过期后提示明确，演示不中断；
3. 全量后端测试不下降、前端 build 通过、两份文档同步更新。

## 后续候选任务（优先级从高到低）

1. ~~**阶段八质量异常协同 + 审批一致性**~~ 已完成（见上节和 `current_status_and_fix_plan.md` §3.20）；
2. ~~速率 ETA~~ 已完成代码与缺数分支（见 `current_status_and_fix_plan.md` §3.25）；真实工单当前无实际报工速率时明确返回 `DATA_MISSING`，积累数据后自动计算；
3. **请求级身份会话治理**：后端已支持 OpenMES/OIDC 类 Bearer 会话并禁止失败降级（见 `current_status_and_fix_plan.md` §3.28）；待企业 SSO 登录页提供短期会话并由前端附加请求头；
4. **NCR 写入真实验收**：在真实角色、令牌和审批对象绑定完善后，对 disposition/close 做最小范围回读验收（代码门禁、对象绑定和幂等已完成）；
5. **Wutong 外部注册发现**：只读 ACPs discovery 查询通道已完成（见 `current_status_and_fix_plan.md` §3.27）；待部署提供 Registry 注册与鉴权契约后，继续做跨实例注册和 AIP 调用闭环。Mock WebSocket 隔离和静态 ACS/运行注册表同步已完成。

## 交接执行计划（2026-09-30 更新，交由下一任 AI）

> 详细交接状态见 `current_status_and_fix_plan.md` §3.38；剩余两项写入补强的完整交接文档（API 契约/铁律/步骤/验收）见 `docs/handoff_2026-09-30_report_and_issue.md`。

| 优先级 | 任务 | 状态与交接点 |
|---|---|---|
| 1 | 完成 10.5 剩余前端组件拆分 | ✅ 已完成（2026-09-30，见 §3.39）：类型/面板/NCR hook/8 步流程三环节全部抽出，主文件 2368 → 772 行；顺带修复身份解析死循环 |
| 1.5 | 前端信息架构：RealBusinessPage 页签化 | ✅ 已完成（2026-09-30，见 §3.40）：协同问答/订单流程/质量中心/运行记录四页签 + 待办徽标 + 全局错误横幅；去处置跨页签联动；用户确认方案后实施 |
| 2 | CI 配置（pytest/vitest/build） | ✅ 已完成（2026-09-30，见 §3.39）：`.github/workflows/ci.yml` 双 job——backend（pip install -e backend + pytest + compileall）、frontend（npm ci + vitest + build） |
| 3 | 问答流式输出（SSE，可选） | ✅ 已完成（2026-09-30，见 §3.41）：`POST /assistant/ask/stream` 步骤级事件流（step/done/error + 心跳），前端调用链逐步点亮、传输层失败自动回退同步端点；面板级 ErrorBoundary 一并完成 |
| 4 | Wutong 写路径 / OIDC / close 真实执行 | ⏸️ 均有外部依赖或有意保留，勿擅自推进（见 §3.38 第三节第 4 条） |

## 每次开发后必须记录

修改文件、真实记录编号、接口和状态码、数据 authority、是否写入、回读结果、测试命令、页面验证结果、遗留问题和下一步任务，统一更新 `current_status_and_fix_plan.md`。

## 当前执行计划（2026-09-30 更新）

| 优先级 | 任务 | 状态与边界 |
|---|---|---|
| 1 | 前端请求级身份会话与 NCR 人工操作面板 | ✅ 已完成：短期 Bearer/写入令牌仅存 `sessionStorage`；NCR 处置与关闭严格按审批、写回、回读分步操作；没有伪造 SSO，也没有自动选择处置 |
| 1.5 | OpenMES 短时会话登录页 | ✅ 已完成（见 current_status_and_fix_plan.md §3.32）：真实 `/api/auth/login` 契约（Sanctum 15 分钟 TTL）、登录令牌回读身份、ERPNext 随机角色 docname 噪音过滤；成功路径待部署方提供业务账号后做端到端验收；ERPNext 密码登录/OIDC 仍待部署契约 |
| 2 | NCR 真实写入最小范围验收 | ✅ 已完成（2026-09-30，见 current_status_and_fix_plan.md §3.35）：用户批准 rework，审批 `QDISP-1BC2B3498AAE` 写回并回读验证通过、closure_ready=true；顺带修复回读数量格式比对 bug；close 链路就绪未执行（演示保留待办） |
| 3 | Wutong Registry 注册与跨实例 AIP 调用 | 🟡 只读 Registry health/recent 已完成；注册、更新、提交和跨实例调用仍等待部署方鉴权/租户契约，当前禁止外部注册写入 |
| 4 | 订单级质量放行 | 保持 `NOT_SUPPORTED`，OpenMES 没有对应真实 API 时不新增伪造端点 |
| 5 | 检验/报工数据补录 | ✅ 已完成（2026-09-30，见 current_status_and_fix_plan.md §3.34）：`backend/seed_inspection_eta.py` 幂等补录 TEST_ 检验 3 条 + 批次实际耗时 150 分钟；track/9 已 RATE_BASED（720 件/小时）、quality/package/9 检验维度有数据、id=2 阻断线不受影响、125 passed |
| 6 | 质量待办面板（阶段九） | ✅ 已完成（见 current_status_and_fix_plan.md §3.33）：真实三态待办 + 去处置直达既有 NCR 面板，125 passed；真实 disposition 写入验收仍等业务决策 |

## 当前执行计划（2026-10-01 更新）：通用能力补强两项写入

> 接手 `docs/handoff_2026-09-30_report_and_issue.md`；用户已批准开工。计划与前置调研见 `current_status_and_fix_plan.md` §3.44。

| 优先级 | 任务 | 状态与边界 |
|---|---|---|
| 1 | 真实报工（RPT- 三步审批：建批次→开工→完工带实际耗时） | ✅ 已完成（2026-10-01，见 §3.44）：官方报工链路真实验证通过——部分报工 ETA 转 RATE_BASED（300 件/h）、全额报工 COMPLETED、幂等、无快照工单写前如实拒绝；含工单下达补 product_type 映射（§3.43 小待办一并完成） |
| 2 | 质量问题登记（QISS- 三步审批：POST /api/v1/issues） | ✅ 已完成（2026-10-01，见 §3.44）：issue id=3 真实创建回读验证，质量包/全厂待办可见，幂等；保留 OPEN 供现场处置演示 |
| 3 | 演示剧本更新（故事线 B 补现场报工 / D 补现场登记质量问题） | ✅ 已完成（2026-10-01）：两故事线各补现场操作路径 + 记录编号速查 |
| 4 | 页面级冒烟（两个新区块） | ⬜ 待下次登录演示时顺带复核（密码仅用户掌握；API 级已覆盖同一调用路径） |
| 5 | 可部署性整改（别人 clone 后能部署） | ✅ 已完成（2026-10-01，见 §3.45）：根 README 从零部署指南 + deploy_services 双 project 脚本（规避 include 同名服务合并坑）+ .env.example 重写 + start_demo python 探测与 real 模式预检 + get_openmes_token.py 入库；全新机器端到端重放为外部待办 |

测试基线：后端 **154 passed** / 前端 14 passed + build（§3.44 完成后）。

每次进入下一项前，先在 `current_status_and_fix_plan.md` 追加真实接口、状态码、authority、是否写入与回读结果，再运行后端隔离测试、前端构建和 `compileall`。
