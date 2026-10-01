# 项目文档索引

## 推荐阅读顺序

1. [后续开发总计划与 AI 接手说明](AI_HANDOFF_PLAN.md)：当前唯一的路线图、边界和验收标准。
2. [当前状态与整改计划](current_status_and_fix_plan.md)：真实代码、接口、测试、运行记录和遗留问题的证据账本；最新追加的小节优先。
3. [真实报工与质量问题登记交接](handoff_2026-09-30_report_and_issue.md)：两项写入能力的接口契约和实现背景。
4. [演示剧本](demo_script.md)：当前真实业务演示步骤、记录编号和已知限制。
5. [项目根目录 README](../README.md)：部署、配置、启动和排障。

## 文档职责

| 文档 | 用途 | 当前使用方式 |
|---|---|---|
| `AI_HANDOFF_PLAN.md` | 后续 AI 的总计划 | 当前开发路线以此为准 |
| `current_status_and_fix_plan.md` | 阶段证据和历史状态 | 追加记录，不覆盖旧证据 |
| `handoff_2026-09-30_report_and_issue.md` | 报工和质量登记交接 | 已完成部分用于核对，未完成部分按真实验证复查 |
| `demo_script.md` | 页面演示和验收复现 | 记录必须来自当前真实系统 |
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
