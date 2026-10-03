# 项目文档索引

## 推荐阅读顺序

1. [比赛接入与提交待办](比赛接入与提交待办.md)：AIC 比赛规则、梧桐平台注册、互联验证和作品材料的当前执行清单。
2. [同学电脑真实环境复现部署](真实环境复现部署.md)：每台电脑独立部署真实 ERPNext/OpenMES、配置本地凭据、执行迁移和导入数据；未在干净机重放前不宣称一键完成。
3. [同学电脑部署方案边界](同学电脑部署方案.md)：真实复现版与本地演示版的边界、数据安全和验收标准。
4. [功能介绍](功能介绍.md)：**面向使用者的功能总账**——系统有什么功能、在哪个页面、怎么用、编号规则与诚实性边界；想"回忆系统有什么"先看这份。
5. [后续开发总计划与 AI 接手说明](AI_HANDOFF_PLAN.md)：当前唯一的路线图、边界和验收标准。
6. [当前状态与整改计划](current_status_and_fix_plan.md)：真实代码、接口、测试、运行记录和遗留问题的证据账本；最新追加的小节优先。
7. [§3.52 去处置联动与工程收口执行记录](handoff_2026-10-01_event_disposal_and_stabilization.md)：事件导航、SQLite 回退、缺口回归和当前复核边界。
8. [能力目录运行时构建交接](handoff_2026-10-02_dynamic_capability_catalog.md)：专项任务的设计依据与验收标准（已于 §3.55 实施）。
9. [真实业务前端信息架构改版](handoff_2026-10-02_frontend_information_architecture.md)：左侧导航、业务模块和会话设置的改版范围。
10. [工单号自然语言识别修复交接](handoff_2026-10-02_work_order_intent_fix.md)：已于 §3.58 修复（中文紧贴编号边界同轮修复见 §3.59）。
11. [会话、审批身份与问答页面交接](handoff_2026-10-02_session_identity_chat_ux.md)：身份语义、连续聊天、回答层级和业务流程空状态（2026-10-02 已实施：身份区分、连续聊天、新会话上下文隔离、临期倒计时保留 15 分钟 TTL；30 分钟 TTL 按用户决定不改）。
12. [真实报工与质量问题登记交接](handoff_2026-09-30_report_and_issue.md)：两项写入能力的接口契约和实现背景。
13. [演示剧本](demo_script.md)：当前真实业务演示步骤、记录编号和已知限制。
14. [项目根目录 README](../README.md)：部署、配置、启动和排障。

## 文档职责

| 文档 | 用途 | 当前使用方式 |
|---|---|---|
| `比赛接入与提交待办.md` | AIC 比赛和梧桐平台接入待办 | 先按阻塞项确认资料，再按 P0/P1/P2 顺序执行；没有平台证据不得标为完成 |
| `真实环境复现部署.md` | 同学电脑的真实 ERPNext/OpenMES 部署和数据导入 | 先按前置条件与人工凭据步骤执行；安全种子与高风险旧种子分开验收 |
| `同学电脑部署方案.md` | 部署目标、数据边界和可复现验收 | 真实复现版为当前目标；本地演示版不能代替真实验收 |
| `功能介绍.md` | 使用者功能总账 | 回忆"系统有什么功能、怎么用"的第一入口；功能变化时同步更新 |
| `AI_HANDOFF_PLAN.md` | 后续 AI 的总计划 | 当前开发路线以此为准 |
| `current_status_and_fix_plan.md` | 阶段证据和历史状态 | 追加记录，不覆盖旧证据 |
| `handoff_2026-10-01_event_disposal_and_stabilization.md` | §3.52 执行记录 | 已完成任务、验收结果和剩余边界 |
| `handoff_2026-10-02_dynamic_capability_catalog.md` | P0-5 专项交接 | 能力目录动态化的设计依据、边界和验收（§3.55 已实施）|
| `handoff_2026-10-02_frontend_information_architecture.md` | 前端改版专项交接 | 左侧导航、业务模块、会话设置和比赛版验收标准 |
| `handoff_2026-10-02_work_order_intent_fix.md` | 工单号识别修复交接 | `WO-2026-*` 识别、冲突规则、问答定位和验收命令 |
| `handoff_2026-10-02_session_identity_chat_ux.md` | 会话、身份和问答页面交接 | 连续聊天、身份区分、OpenMES TTL、回答层级和销售/采购空状态 |
| `handoff_2026-09-30_report_and_issue.md` | 报工和质量登记交接 | 已完成部分用于核对，未完成部分按真实验证复查 |
| `demo_script.md` | 页面演示和验收复现 | 路径来自已验证真实系统；编号和数量属历史记录，运行前必须重新核对 |
| `next_development_plan.md` | 早期阶段计划 | 保留历史，未勾选任务不自动视为当前待办 |
| `field_mapping.md` | 早期字段映射 | 历史参考，字段冲突时以当前适配器和真实响应为准 |
| `project_summary.md` | 早期项目总结 | 历史参考，不作为完成证明 |
| `ai_handoff_real_agent_workflow.md` | 早期 AI 交接稿 | 历史参考，不作为当前状态 |
| `prompt_seed_test_data.md` | TEST 数据补录提示词 | 只有在用户明确批准并重新核对数据后才能执行 |

## 统一规则

- 真实 ERP/MES 事实、来源、记录编号、审批、回读和幂等优先于文档中的旧结论。
- Mock、fixture、`DEMO_*` 和 `synthetic_demo_only` 只能用于开发测试。
- 未知字段、接口、公式和业务规则必须先调查，不得补写猜测。
- 文档中没有测试或真实接口证据的内容只能写成“计划”“待验证”或“未知”。

历史资料说明见 [obsolete/README.md](obsolete/README.md)。
origin: https://github.com/AIP-PUB/ACPs-community.git (v2.2.0, .git 已移除转为 vendored 文件)
