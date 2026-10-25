# 问答 Harness 改造（DeepSeek/Codex 式）——设计与进度

> 用途：把「AI 协同问答」升级为 DeepSeek/Codex 式体验：思考过程展示 + 调用链交错时间轴 + 最终答案打字机。
> **交接必读**：本文档随每一步实施同步更新（§6 进度日志 + §5 勾选）。如果你接手时发现 §5 有未勾选项，从第一个未勾选项继续；每一步的「验收」都写在该步旁边。
> 创建：2026-10-25。关联文档：`docs/前端优化清单.md`（本任务超出该清单范围，单独立项）。

## 1. 目标形态（已与用户确认）

一轮问答的渲染序列 = 真实发生顺序的交错时间轴：

```
用户：SAL-ORD-2026-00001 什么时候能做完？
🧠 思考过程（第1轮）[流式追加 · 该轮完成后自动折叠]
🔗 跟单智能体 · 查订单关联 ✓ 301ms（可展开：实参+摘要+原始JSON）
🧠 思考过程（第2轮）[折叠]
🔗 跟单智能体 · 生产进度跟踪 ✓ 298ms
💬 最终答案（打字机逐字，Markdown 渲染）
```

历史轮次（切回旧会话/刷新后）同样展示当轮调用链与思考过程（折叠态）。

## 2. 关键事实（已实测/已核对代码，接手者不必重查）

1. **模型已是混合推理模型**：生产用 `deepseek-flash`（`/api/integrations/status` 可查）。实测（2026-10-25 在服务器直连 api.deepseek.com 探测）：
   - 非流式：message 含 `reasoning_content` 字段；
   - **带 tools 的轮次同样返回** reasoning_content；
   - `stream:true` 可用：delta 分片里 `reasoning_content` 与 `content` 分开推送，首块含 `delta.role`。
   - 结论：「思考过程」数据每次调用都在返回，当前 `coordinator.py` 丢弃了它。
2. **DeepSeek API 约束**：reasoning_content 不得回传进下一轮 messages（reasoner 系会 400；flash 目前容忍）。**当前代码有污染**：`coordinator.py` 循环里 `messages.append(message)` 把含 reasoning_content 的消息原样塞回对话——本改造步骤 A 顺手修复（剥离后再 append）。
3. **现状代码坐标**：
   - LLM 调用：`backend/app/services/coordinator.py` `ask()`（非流式，`self._deepseek.chat_completion`，MAX_TOOL_ROUNDS 轮循环）；
   - 传输层：`backend/app/integrations/deepseek.py`（仅非流式 `chat_completion`）；`http.py` 的 `JsonHttpClient` 持有 `httpx.AsyncClient`（`self._client`，可用来做流式）；
   - SSE 端点：`backend/app/main.py` `real_order_assistant_ask_stream`——**序列化是通用的**（`event: {kind}`），加新事件类型零成本；15s 心跳注释行已有；
   - 会话层：`backend/app/services/assistant_session.py` `handle_ask(..., on_step=)`；确定性路径（数量变更/方案选择/追问）不经过协调者；
   - **协调运行已落库**：`coordinator.ask()` 尾部 `_save_agent_run(run_id, "coordinator", "ask", ..., session_id=run_meta.session_id, expires_at=保留期)`，`result_json` 含完整 call_chain——**历史链路恢复走「按 session_id 关联查 real_agent_runs」，不改表**；
   - 消息表 `assistant_messages` 无元数据列；`alembic/versions` 为空（项目实际用 `Base.metadata.create_all`，改已有表的列在服务器不会自动生效，**禁止给已有表加列**）；
   - 前端：`frontend/src/components/AssistantPanel.tsx`（流式消费在 `api.ts` `askAssistantStream`，`AbortSignal` 已通、UI 无 Stop 按钮）；`AssistantCallChain.tsx` 调用链组件；`lib/markdown.ts` 自研渲染器（行式，增量重渲染可行）。
4. **流式 tool_calls 的复杂点**：流式下 `tool_calls` 的 name/arguments 是分片，需按 `index` 累积拼接。步骤 C 采用「全轮流式 + 按 index 累积 + answer_delta 带轮次号」方案（前端换轮即清缓冲），工具轮/最终轮统一处理。

## 3. 明确不做（接手者不要跑偏）

- 不逐字流式「思考过程」（每轮结束整段推送；步骤 C 若实现了 reasoning_delta 则顺带，不单独做）。
- 不改只读通道语义：写操作仍停在审批门禁（方案卡双审批线是比赛卖点）。
- 不引入 UI/markdown/图表库（自研渲染器已有）。
- 不给 `assistant_messages` 等已有表加列（服务器 SQLite 不会自动 ALTER）。
- 不做全程 Codex 式 diff/文件编辑视图（业务不适用）。

## 4. 实施步骤（每步一个 commit，含测试与文档更新）

### ☑ 步骤 A：后端思考透传 + 修复回传污染（纯后端）——完成

- `coordinator.py`：
  - `ask()` 加参数 `on_reasoning: Callable[[dict], None] | None = None`；
  - 每轮响应后：`reasoning = message.get("reasoning_content")`，非空则 `on_reasoning({"round": rounds, "text": reasoning})` 并收集进 `result["reasoning_rounds"]`（list[{round,text}]，空轮不含）；
  - **剥离污染**：append 进 messages 前构造去掉 `reasoning_content` 键的副本；
  - `assistant_session.py` `_finish()`（确定性路径）加 `result.setdefault("reasoning_rounds", [])`。
- 测试：`tests/test_coordinator.py` 加用例——① 假 DeepSeek 客户端返回带 reasoning_content 的多轮响应 → on_reasoning 每轮被调、result.reasoning_rounds 正确；② 断言回传给 chat_completion 的 messages 不含 reasoning_content 键。
- 验收：`backend` pytest 全绿（基线 684+）；此步不部署也不影响现网（无人消费新字段）。

### ☑ 步骤 B：SSE reasoning 事件 + 前端思考块（最小可用）——完成

- `assistant_session.py` `handle_ask` 加 `on_reasoning` 参数透传给 coordinator；
- `main.py` 流式端点：`on_reasoning=lambda r: queue.put_nowait(("reasoning", r))`；
- `api.ts`：`askAssistantStream` options 加 `onReasoning`，解析 `event: reasoning`；`AssistantAnswer` 类型加 `reasoning_rounds?: {round:number; text:string}[]`；
- `AssistantPanel.tsx`：加载中在链路上方显示「思考过程」折叠块（逐轮追加）；done 后用 `assistantAnswer.reasoning_rounds` 渲染（默认折叠）。样式复用 assistant-fold，本期不做交错时间轴（步骤 E 做）。
- 测试：vitest（AssistantPanel 相关用例不回归）；`test_assistant_stream.py` 加 reasoning 事件透传用例。
- 验收：浏览器提问，等待期看到每轮思考滑出；服务器部署后截图。

### ☐ 步骤 C：历史轮次调用链/思考恢复

- `main.py` `sessions/{id}/messages` 端点：查 `real_agent_runs`（agent_type='coordinator'、session_id 匹配、按 started_at 排序），把 `result_json` 里的 `call_chain`/`reasoning_rounds`/`coordination_run_id` 附加到**第一条 created_at ≥ run.started_at 的助手消息**上（确定性回答无 run，自然跳过）；
- `api.ts` `AssistantHistoryMessage` 加可选 `call_chain`/`reasoning_rounds`；`AssistantPanel` 历史助手消息渲染折叠的调用链（复用 AssistantCallChain）与思考块。
- 测试：`test_assistant_history_endpoint.py` 加带 run 的会话用例。
- 验收：切回旧会话能看到当时的调用链。

### ☐ 步骤 D：最终答案逐 token 打字机（全轮流式方案）

- `deepseek.py`：加 `chat_completion_stream(messages, *, tools, max_tokens)` —— async iterator 产出解析后的 delta 事件（content 片段 / reasoning 片段 / tool_call 分片 / finish_reason / usage），用 `self._http._client.stream(...)`（或给 JsonHttpClient 加 `stream_lines` 方法，推荐后者，保持封装）；
- `coordinator.py`：
  - 客户端有 stream 方法且未被注入禁用时走流式，否则回退现有非流式（**测试里的假客户端没有 stream 方法 → 现有用例自动走老路径，不破坏**）；
  - 流式累积：按 delta.index 拼 tool_calls（id/name/arguments 字符串拼接）；reasoning 片段聚合；content 片段 → `on_answer_delta({"round": n, "text": chunk})`；
  - 轮结束时若有 tool_calls：照旧执行工具进入下一轮；若无非流式 message 等价物需要组装回 message dict 供 messages.append（剥 reasoning_content）；
- `assistant_session.py`/`main.py`：透传 `on_answer_delta` → SSE 事件 `answer_delta`；`done` 结构不变；
- `api.ts`/`AssistantPanel.tsx`：`onAnswerDelta` 回调 → 状态里维护 `{round, buffer}`，换轮清空；done 后用权威 answer 覆盖本地缓冲（以 done 为准，防丢字）。节流 50-100ms 重渲染 markdown。
- 测试：`test_coordinator.py` 假流式客户端用例（分片 tool_calls 拼装正确性 + answer_delta 序列）；`test_assistant_stream.py` answer_delta 事件用例。
- 验收：服务器实测打字机效果；网络中断回退同步端点仍可用。

### ☐ 步骤 E：交错时间轴 UI + 调用卡展开 + Stop 按钮（纯前端）

- 等待区/完成区重构为统一时间轴组件：思考块（完成后自动折叠）→ 调用卡 → …；调用卡点击展开实参逐项 + 结果摘要 + 原始 JSON（次级 details，复用任务 8 模式）；
- 完成后调用链不再沉底 `<details>`，保留在时间轴（思考折叠、调用卡常显）；
- Stop 按钮：AbortController 接 `askAssistantStream` 的 signal（已支持），中断后保留已收到的步骤；
- 样式全部走现有 token（机加工青体系），动画遵守 prefers-reduced-motion。
- 验收：vitest + build；浏览器全流程验证并截图；总览页无回归。

## 5. 交付规程（每步硬性执行，与前端优化清单一致）

1. `cd frontend && npx vitest run`（不得低于 163）+ `npm run build`；动了后端另跑 `cd backend && ../.conda-env/python.exe -m pytest tests -q`（基线 684）；
2. `git add -A && git commit`（中文信息）→ `git push origin main`（代理 127.0.0.1:7890）；
3. 服务器 bundle 更新（SSH 配置 `wlog` 已免密；后端 uvicorn --reload 自动生效，前端 Vite dev 自动生效）；
4. 浏览器 `http://129.204.33.138:5173/` 实测截图；
5. 更新本文档 §4 勾选与 §6 日志，随该步提交。

## 6. 进度日志

- 2026-10-25 创建文档；完成全部前置探测（模型 reasoning_content / 流式格式 / 存储层路径），设计已与用户确认（交错时间轴、工具轮非逐字、思考整段、最终轮打字机、历史链路恢复）。下一步：步骤 A。
- 2026-10-25 步骤 A 完成：`coordinator.ask()` 加 `on_reasoning` 回调与 `result["reasoning_rounds"]`（每轮 + 空回答恢复路径的思考都采集）；回传 messages 前剥离 `reasoning_content`（修复既有 API 约定污染）；`assistant_session._finish()` 确定性路径补 `reasoning_rounds` 默认值。新增 `ReasoningPassthroughTests` 2 例（透传+剥离验证、无思考字段时空数组）。后端 686 passed（基线 684+2）。
- 2026-10-25 步骤 B 完成：`handle_ask` 透传 `on_reasoning`；SSE 端点新增 `reasoning` 事件；`coordinator_extras` 补带 `reasoning_rounds`（done 事件含完整思考）；前端 `api.ts` 解析 reasoning 事件 + `AssistantAnswer.reasoning_rounds` 类型；新增 `AssistantThinking.tsx`（live 态逐轮滑出+呼吸点动画、完成态折叠保留），AssistantPanel 等待区与完成区接线；顺手修复 `test_lists_sessions_most_recent_first_without_messages` 的 limit=20 脆弱性（组合运行遗留会话挤掉新建会话，改用端点上限 50）。后端 687 passed（+1 reasoning 事件用例）、前端 vitest 163 passed、build 通过。
