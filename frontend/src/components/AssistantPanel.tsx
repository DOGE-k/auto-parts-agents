// 智能协同问答面板（阶段五-八：协调智能体动态调用四个真实智能体 + 方案批准执行闭环；阶段十 10.5 从 RealBusinessPage 抽出；P0 会话上下文：多轮沿用+回显+追问+下游重确认标记）
// 2026-10-02 会话体验修复（交接文档 §3/§4/§7）：
//  ①连续聊天——历史轮次沉入消息列表（输入框固定底部），组件保持挂载、刷新后按
//    sessionStorage 的 session_id 从后端只读端点恢复历史；
//  ②新会话切断页面上下文——"新会话"后首轮不再自动带入页面当前工单/订单，
//    首问结束后恢复正常策略（用户明确输入的编号不受影响）；
//  ③回答分层——结论/回显/追问/重确认/方案卡保持首屏，调用链、质量影响明细、
//    数据缺失明细默认折叠，不截断任何事实。
import { useState, useCallback, useEffect, useRef } from "react";
import { formatAssistantAnswer } from "../lib/markdown";
import {
  agentRunTypeNames,
  askAssistant,
  askAssistantStream,
  AssistantStreamExecError,
  approveProcurementPlan,
  approveQuotation as approveQuotationApi,
  createPoFromPlan,
  getAssistantSessionMessages,
  getAssistantSessions,
} from "../api";
import type {
  AssistantAnswer,
  AssistantCallStep,
  AssistantHistoryMessage,
  AssistantJumpTarget,
  AssistantSessionSummary,
  ProposalSupplierOption,
  RealIdentity,
} from "../api";

const SESSION_STORAGE_KEY = "assistant_session_id";

type Props = {
  identity: RealIdentity | null;
  currentErpDraftId: string;
  currentWorkOrderId: string;
  notify: (message: string) => void;
  onError: (message: string) => void;
  /** 回答中的业务对象跳转：携带对象编号自动定位打开（评审意见①），不只切模块 */
  onNavigate?: (
    module: "sales" | "procurement" | "production" | "quality",
    target?: AssistantJumpTarget,
  ) => void;
  /** 业务总览"去提问"带过来的问题：填入输入框待用户确认发送 */
  initialQuestion?: string;
  onQuestionConsumed?: () => void;
};

// 常用问题芯片（填入输入框，不自动发送；没有订单号时协调者会主动追问）
const COMMON_QUESTIONS = [
  "订单什么时候能做完？",
  "这单为什么还不能发运？",
  "这单缺料怎么办？给我几套方案",
  "这个质量异常会影响交期吗？",
];

export default function AssistantPanel({
  identity,
  currentErpDraftId,
  currentWorkOrderId,
  notify,
  onError,
  onNavigate,
  initialQuestion,
  onQuestionConsumed,
}: Props) {
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState<AssistantAnswer | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(false);
  // P0 会话上下文：session_id 保存在浏览器 sessionStorage（跨页签切换保持）
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem(SESSION_STORAGE_KEY));
  // 历史轮次消息（纯文本 Q/A）；最新一轮用富展示渲染。仅挂载时恢复一次，
  // 之后在本地追加，避免与后端往返产生重复渲染。
  const [history, setHistory] = useState<AssistantHistoryMessage[]>([]);
  const restoreSessionRef = useRef<string | null>(sessionId);
  // 新会话后首轮屏蔽页面级上下文注入（bug#7：新会话不得自动带入旧工单/旧订单）
  const [suppressPageContext, setSuppressPageContext] = useState(false);
  const [skippedPageContext, setSkippedPageContext] = useState(false);
  // 历史会话切换器：展开时才拉取列表，选中即恢复该会话完整消息
  const [sessions, setSessions] = useState<AssistantSessionSummary[] | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [switchingSession, setSwitchingSession] = useState(false);
  // 流式问答（阶段十 SSE）：等待期间逐条收到的调用链步骤（点亮"协调者正在查什么"）
  const [streamSteps, setStreamSteps] = useState<AssistantCallStep[]>([]);
  // 方案卡片执行闭环（阶段七+八）：报价未审批时走双审批线（报价+方案各留痕）
  const [proposalExec, setProposalExec] = useState<{
    option_id: string;
    stage: "confirming" | "executing";
    approver: string;
    quotationApprover: string;
  } | null>(null);
  const [proposalExecResult, setProposalExecResult] = useState<{
    option_id: string;
    approval_id: string;
    quotation_approval_id?: string;
    po_draft_id?: string;
    read_back_verified?: boolean;
    error?: string;
  } | null>(null);

  // 刷新后按 sessionStorage 的 session_id 恢复当前会话消息（后端只读端点，
  // 只含 role/content 原文，不含凭据与工具参数）；失败静默降级为空历史。
  useEffect(() => {
    const sid = restoreSessionRef.current;
    if (!sid) return;
    let cancelled = false;
    getAssistantSessionMessages(sid)
      .then((messages) => {
        if (!cancelled && messages.length) setHistory(messages);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const executeProposal = useCallback(async (
    planId: string,
    optionId: string,
    approver: string,
    quotation: { id: string; needsApproval: boolean; approver: string } | null,
  ) => {
    setProposalExec((prev) => (prev ? { ...prev, stage: "executing" } : prev));
    onError("");
    try {
      const actor = identity?.actor_id || approver;
      if (!actor) throw new Error("真实身份尚未解析，不能提交审批");
      let quotationApprovalId = "";
      if (quotation?.needsApproval) {
        const qApproval = await approveQuotationApi(quotation.id, actor);
        quotationApprovalId = qApproval.approval_id;
      }
      const approval = await approveProcurementPlan(planId, optionId, actor);
      const draftResult = await createPoFromPlan(planId, approval.approval_id, actor);
      setProposalExecResult({
        option_id: optionId,
        approval_id: approval.approval_id,
        quotation_approval_id: quotationApprovalId || undefined,
        po_draft_id: draftResult.draft?.draft_id,
        read_back_verified: draftResult.draft?.read_back_verified,
      });
      setProposalExec(null);
      notify(`方案已批准，PO 草稿 ${draftResult.draft?.draft_id ?? ""} 已创建`);
    } catch (e) {
      setProposalExecResult({
        option_id: optionId,
        approval_id: "",
        error: e instanceof Error ? e.message : "执行失败",
      });
      setProposalExec(null);
    }
  }, [identity, notify, onError]);

  const submitAssistantQuestion = useCallback(async () => {
    const q = assistantQuestion.trim();
    if (!q || assistantLoading) return;
    setAssistantLoading(true);
    // 上一轮问答对沉入历史消息列表（输入框保留在底部，不清空旧问题）
    setHistory((prev) => {
      const next = [...prev];
      if (assistantAnswer) {
        next.push({ id: -next.length - 1, role: "assistant", content: assistantAnswer.answer, created_at: "" });
      }
      next.push({ id: -next.length - 1, role: "user", content: q, created_at: "" });
      return next;
    });
    setAssistantAnswer(null);
    setSkippedPageContext(suppressPageContext);
    setStreamSteps([]);
    onError("");
    try {
      const context: Record<string, unknown> = {};
      if (!suppressPageContext) {
        if (currentErpDraftId) context.当前流程ERP订单号 = currentErpDraftId;
        if (currentWorkOrderId) context.当前流程MES工单id = currentWorkOrderId;
      }
      let result: AssistantAnswer;
      try {
        // 流式优先（SSE）：调用链逐步点亮；协调者执行失败不回退（同步重试必然同样失败），
        // 传输层失败（网络异常/端点不可用/流中断）回退同步端点。
        result = await askAssistantStream(q, context, {
          onStep: (step) => setStreamSteps((prev) => [...prev, step]),
          sessionId,
        });
      } catch (streamErr) {
        if (streamErr instanceof AssistantStreamExecError) throw streamErr;
        result = await askAssistant(q, context, sessionId);
      }
      if (result.session_id) {
        sessionStorage.setItem(SESSION_STORAGE_KEY, result.session_id);
        setSessionId(result.session_id);
      }
      setAssistantAnswer(result);
      notify("协调智能体已回答");
    } catch (e) {
      onError(e instanceof Error ? e.message : "协调智能体调用失败");
    } finally {
      // 新会话首问结束后恢复正常页面上下文策略（交接文档 §4.2）
      setSuppressPageContext(false);
      setAssistantLoading(false);
      setStreamSteps([]);
      setAssistantQuestion("");
    }
  }, [assistantQuestion, assistantAnswer, assistantLoading, currentErpDraftId, currentWorkOrderId, sessionId, suppressPageContext, notify, onError]);

  const startNewSession = useCallback(() => {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
    restoreSessionRef.current = null;
    setSessionId(null);
    setAssistantAnswer(null);
    setHistory([]);
    setAssistantQuestion("");
    // 新会话首轮屏蔽页面上下文（工单/订单不再自动带入，见 submitAssistantQuestion）
    setSuppressPageContext(true);
    notify("已开始新会话（页面当前工单/订单不再自动带入，业务对象重新积累）");
  }, [notify]);

  // 历史会话：展开时拉取列表；选中某个会话即恢复其完整消息（数据仍在业务库保留期内）
  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      setSessions(await getAssistantSessions(20));
    } catch {
      setSessions([]);
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  const switchToSession = useCallback(async (targetSessionId: string) => {
    if (targetSessionId === sessionId || switchingSession) return;
    setSwitchingSession(true);
    onError("");
    try {
      const messages = await getAssistantSessionMessages(targetSessionId);
      sessionStorage.setItem(SESSION_STORAGE_KEY, targetSessionId);
      restoreSessionRef.current = targetSessionId;
      setSessionId(targetSessionId);
      setHistory(messages);
      setAssistantAnswer(null);
      setAssistantQuestion("");
      setSuppressPageContext(false);
      notify(`已切回历史会话 ${targetSessionId}（${messages.length} 条消息）`);
    } catch (e) {
      onError(e instanceof Error ? e.message : "历史会话消息加载失败");
    } finally {
      setSwitchingSession(false);
    }
  }, [sessionId, switchingSession, notify, onError]);

  // 业务总览"去提问"预填：填入输入框待用户确认，不自动发送
  useEffect(() => {
    if (!initialQuestion) return;
    setAssistantQuestion(initialQuestion);
    onQuestionConsumed?.();
  }, [initialQuestion, onQuestionConsumed]);

  // 回答涉及的业务对象 → 携带编号的定位跳转（评审意见①）。
  // 来源按可靠度排序：本轮调用链实参（协调者真实触碰的对象）> 结构化方案字段
  // > 会话实体上下文（沿用_/页面_ 前缀键，键名以 assistant_context.py 字段集为准）。
  const jumpTarget: AssistantJumpTarget = {};
  if (assistantAnswer) {
    for (const step of (assistantAnswer.call_chain ?? []) as AssistantCallStep[]) {
      const args = (step.arguments ?? {}) as Record<string, unknown>;
      if (args.work_order_id) jumpTarget.work_order_id = String(args.work_order_id);
      if (args.work_order_no) jumpTarget.work_order_no = String(args.work_order_no);
      if (args.quotation_id) jumpTarget.quotation_id = String(args.quotation_id);
      if (args.erp_order_id) jumpTarget.erp_order_id = String(args.erp_order_id);
    }
    const proposal = assistantAnswer.proposal_options;
    if (proposal?.plan_id) jumpTarget.plan_id = proposal.plan_id;
    if (proposal?.quotation_id) jumpTarget.quotation_id = proposal.quotation_id;
    const ctx = (assistantAnswer.context ?? {}) as Record<string, unknown>;
    for (const [key, value] of Object.entries(ctx)) {
      if (typeof value !== "string" || !value.trim()) continue;
      if (key.endsWith("work_order_id")) jumpTarget.work_order_id = value;
      else if (key.endsWith("work_order_no")) jumpTarget.work_order_no = value;
      else if (key.endsWith("quotation_id")) jumpTarget.quotation_id = value;
      else if (key.endsWith("plan_id")) jumpTarget.plan_id = value;
    }
  }
  const jumpButtons: { label: string; module: "sales" | "procurement" | "production" | "quality"; target?: AssistantJumpTarget }[] = [];
  if (assistantAnswer && onNavigate) {
    if (jumpTarget.work_order_id || jumpTarget.work_order_no) {
      jumpButtons.push({ label: "查看该工单跟单", module: "production", target: { work_order_id: jumpTarget.work_order_id, work_order_no: jumpTarget.work_order_no } });
    }
    if (jumpTarget.plan_id) {
      jumpButtons.push({ label: "打开该方案审批", module: "procurement", target: { quotation_id: jumpTarget.quotation_id, plan_id: jumpTarget.plan_id } });
    } else if (jumpTarget.quotation_id) {
      jumpButtons.push({ label: "打开该报价", module: "sales", target: { quotation_id: jumpTarget.quotation_id } });
    }
  }

  // 输入框固定在消息列表上方（用户确认的使用习惯：长回答在下方内部滚动，
  // 打字不需要来回翻页）；消息区在新内容出现时自动滚到最新一条。
  const messagesBoxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const box = messagesBoxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [assistantAnswer, streamSteps.length, history.length, assistantLoading]);

  return (
    <section className="panel assistant-panel">
      <div className="panel-heading">
        <div>
          <h2>智能协同问答</h2>
          <p>直接用一句话提问（如"SAL-ORD-2026-00023 什么时候能做完"），协调智能体会自己判断需要什么数据，动态调用报价/采购/跟单/质量四个智能体查询真实 ERP/MES 后回答，调用链全程留痕</p>
        </div>
        <span className="badge blue-badge">动态协同 · 只读</span>
      </div>
      {!sessionId ? (
        <p className="field-hint">新会话，尚未关联业务对象；提问时请带上订单/工单/物料编号（如 SAL-ORD-2026-00023），或直接说明你想做什么。切到其他模块再回来，聊天记录会保留。</p>
      ) : (
        <p className="field-hint">当前会话 {sessionId} · 上一轮的客户/物料/订单/工单/数量/交期会自动沿用，回答中会回显；点"新会话"可重新开始（页面当前工单/订单不再自动带入）。</p>
      )}
      {/* 历史会话切换器：保留期内的旧对话可随时切回查看 */}
      <details className="assistant-sessions">
        <summary
          onClick={() => {
            // 展开时按需加载一次（sessions===null 为未加载标记；折叠再点不会重复请求）
            if (sessions === null) void loadSessions();
          }}
        >
          历史会话（点击展开切换回以前的对话）
        </summary>
        {sessionsLoading && <p className="field-hint">会话列表加载中…</p>}
        {!sessionsLoading && (sessions ?? []).length === 0 && <p className="field-hint">还没有历史会话记录。</p>}
        <div className="assistant-sessions-list">
          {(sessions ?? []).map((s) => (
            <button
              key={s.session_id}
              className={`assistant-session-row ${s.session_id === sessionId ? "current" : ""}`}
              disabled={s.session_id === sessionId || switchingSession}
              onClick={() => void switchToSession(s.session_id)}
              title={s.session_id}
            >
              <span className="assistant-session-title">{s.title || s.session_id}</span>
              <time>{s.last_active_at ? new Date(s.last_active_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}</time>
            </button>
          ))}
        </div>
        <p className="field-hint">对话原文默认保留 90 天（ASSISTANT_RETENTION_DAYS 可配），到期自动清理。</p>
      </details>
      {/* 常用问题芯片 + 输入行：固定在消息列表上方，不随长回答滚走 */}
      <div className="assistant-chip-row">
        <span className="chip-caption">常用问题：</span>
        {COMMON_QUESTIONS.map((q) => (
          <button key={q} className="assistant-chip" disabled={assistantLoading} onClick={() => setAssistantQuestion(q)}>
            {q}
          </button>
        ))}
      </div>
      <div className="assistant-input-row">
        <textarea
          value={assistantQuestion}
          onChange={(e) => setAssistantQuestion(e.target.value)}
          placeholder="例如：SAL-ORD-2026-00023 什么时候能做完？/ 这单为什么还不能发运？/ BD-2401 现在 2000 件多少钱？"
          rows={2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submitAssistantQuestion();
            }
          }}
        />
        <button
          className="button primary"
          onClick={() => void submitAssistantQuestion()}
          disabled={assistantLoading || !assistantQuestion.trim()}
        >
          {assistantLoading ? "协调智能体处理中..." : "提问 →"}
        </button>
        <button className="button ghost" onClick={startNewSession} disabled={assistantLoading} title="清空当前会话上下文，重新开始一个业务任务（页面当前工单/订单不再自动带入）">
          新会话
        </button>
      </div>
      {/* 消息列表：历史轮次（纯文本）+ 最新一轮（富展示），内部滚动区域 */}
      <div className="assistant-messages" ref={messagesBoxRef}>
        {history.map((m, i) => (
          m.role === "user" ? (
            <div key={`u-${m.id}-${i}`} className="assistant-msg user">
              <span className="assistant-msg-role">我</span>
              <div className="assistant-msg-body">{m.content}</div>
            </div>
          ) : (
            <div key={`a-${m.id}-${i}`} className="assistant-msg assistant">
              <span className="assistant-msg-role">协调智能体</span>
              <div
                className="assistant-msg-body md-body"
                dangerouslySetInnerHTML={{ __html: formatAssistantAnswer(m.content) }}
              />
            </div>
          )
        ))}
        {/* 流式进行中（阶段十 SSE）：调用链逐步点亮，替代原来的长时间无反馈等待 */}
        {assistantLoading && streamSteps.length > 0 && (
          <div className="assistant-result">
            <div className="assistant-chain">
              <small>协调智能体工作中，已完成 {streamSteps.length} 次智能体调用…</small>
              {streamSteps.map((step) => (
                <div key={step.seq} className={`assistant-step ${step.status}`}>
                  <span className="assistant-step-seq">{step.seq}</span>
                  <span className="assistant-step-callee">{agentRunTypeNames[step.callee] ?? step.callee}</span>
                  <code className="assistant-step-skill">{step.skill_id}</code>
                  <code className="assistant-step-args">{JSON.stringify(step.arguments)}</code>
                  <span className="assistant-step-status">
                    {step.status === "ok" ? "✓" : "✗"} {step.elapsed_ms}ms
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {assistantAnswer && (
          <div className="assistant-result">
            {/* 新会话首轮：明确说明未带入页面上下文（交接文档 §4.5） */}
            {skippedPageContext && (
              <div className="assistant-context-echo">本轮未自动带入页面当前工单/订单（新会话首问）；如需关联，请直接输入业务编号。</div>
            )}
            {/* P0：上下文沿用回显（用户可纠正） */}
            {assistantAnswer.applied_context && (
              <div className="assistant-context-echo">{assistantAnswer.applied_context}</div>
            )}
            {/* P0：槽位追问（缺什么/为什么/补充后调用哪个智能体） */}
            {assistantAnswer.needs_input && (assistantAnswer.missing_slots?.length ?? 0) > 0 && (
              <div className="assistant-clarify">
                <strong>需要补充信息（不猜测）</strong>
                <ul>
                  {assistantAnswer.missing_slots!.map((m) => (
                    <li key={m.slot}>
                      <code>{m.slot}</code> —— {m.why}；补充后将调用 {m.agent}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {/* P0：数量变更后下游需重新确认标记 */}
            {(assistantAnswer.stale_downstream?.length ?? 0) > 0 && (
              <div className="assistant-stale">
                <strong>以下已有结果需要重新确认（不能继续沿用旧结果）：</strong>
                <ul>
                  {assistantAnswer.stale_downstream!.map((s, i) => (
                    <li key={`${s.type}-${s.id}-${i}`}>
                      {s.type === "quotation" ? "报价" : s.type === "procurement_plan" ? "采购方案" : s.type === "erp_so_draft" ? "ERP 销售订单草稿" : s.type === "erp_po_draft" ? "ERP 采购订单草稿" : s.type === "mes_work_order" ? "MES 工单" : s.type}
                      {" "}
                      <code>{s.id}</code>：{s.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {/* P0：方案选择确认（第 N 个方案 → 稳定 option_id + 快照） */}
            {assistantAnswer.pending_selection && (
              <div className="assistant-pending-selection">
                已选 <code>{assistantAnswer.pending_selection.plan_id}</code> 的选项{" "}
                <code>{assistantAnswer.pending_selection.option_id}</code>（快照已保存）。
                执行请到方案卡片点击"选择此方案并起草 PO"，经人工审批门禁后写入。
              </div>
            )}
            <div
              className="assistant-answer md-body"
              dangerouslySetInnerHTML={{ __html: formatAssistantAnswer(assistantAnswer.answer) }}
            />
            {jumpButtons.length > 0 && (
              <div className="assistant-jump-row">
                <span>在业务模块中继续处理：</span>
                {jumpButtons.map((b) => (
                  <button key={b.label} className="button ghost" onClick={() => onNavigate?.(b.module, b.target)}>
                    {b.label} →
                  </button>
                ))}
              </div>
            )}
            {assistantAnswer.proposal_options && (
              <div className="proposal-block">
                <small>可执行方案（真实工具结果汇集；最终由人工确认，执行请走 8 步审批流程）</small>
                {assistantAnswer.proposal_options.shortage && (
                  <div className="proposal-shortage">
                    缺料 {assistantAnswer.proposal_options.shortage.shortage_count} 项：
                    {assistantAnswer.proposal_options.shortage.shortage_items
                      .map((s) => `${s.item_id}(缺 ${s.net_requirement ?? "?"})`)
                      .join("、")}
                  </div>
                )}
                <div className="proposal-cards">
                  {(assistantAnswer.proposal_options.supplier_options ?? []).map((opt: ProposalSupplierOption) => {
                    const cost = (assistantAnswer.proposal_options?.cost_assessments ?? []).find((c) => c.option_id === opt.option_id);
                    const execResult = proposalExecResult?.option_id === opt.option_id ? proposalExecResult : null;
                    const confirming = proposalExec?.option_id === opt.option_id && proposalExec.stage === "confirming";
                    const executing = proposalExec?.option_id === opt.option_id && proposalExec.stage === "executing";
                    const planId = assistantAnswer.proposal_options?.plan_id ?? "";
                    return (
                      <div key={opt.option_id} className={`proposal-card ${opt.is_recommended ? "recommended" : ""}`}>
                        <div className="proposal-card-head">
                          <strong>{opt.supplier_name}</strong>
                          {opt.is_recommended && <span className="badge green">推荐</span>}
                          <span className="proposal-coverage">{opt.coverage} 项覆盖</span>
                        </div>
                        <div className="proposal-card-row"><span>采购总价</span><strong>{opt.total_cost ? `${opt.total_cost} ${opt.currency}` : "价格数据不完整"}</strong></div>
                        <div className="proposal-card-row"><span>交期(天)</span><strong>{opt.lead_time_days ?? "缺数据"}</strong></div>
                        {cost && (
                          <>
                            <div className="proposal-card-row">
                              <span>成本影响</span>
                              <strong className={cost.material_cost_delta.startsWith("-") ? "text-green" : "text-red"}>
                                {cost.material_cost_delta.startsWith("-") ? "" : "+"}{cost.material_cost_delta} {cost.currency}
                              </strong>
                            </div>
                            <div className="proposal-card-row"><span>单件加价</span><strong>{cost.per_unit_surcharge ? `${cost.per_unit_surcharge} ${cost.currency}` : "—"}</strong></div>
                            <div className="proposal-card-row"><span>材料毛利</span><strong>{cost.material_margin_before} → {cost.material_margin_after}</strong></div>
                          </>
                        )}
                        <div className="proposal-card-note">{opt.recommendation_reason}</div>
                        {/* 阶段七：方案批准 → PO 草稿（审批门禁内，草稿级写入） */}
                        {planId && opt.total_cost_complete && (execResult ? (
                          <div className={`proposal-exec-result ${execResult.error ? "error" : "ok"}`}>
                            {execResult.error
                              ? `执行失败：${execResult.error}`
                              : `✓ 已批准方案（${execResult.approval_id}）${execResult.quotation_approval_id ? `，报价审批（${execResult.quotation_approval_id}）` : ""}，PO 草稿 ${execResult.po_draft_id ?? "?"} ${execResult.read_back_verified ? "回读确认" : "待回读"}`}
                          </div>
                        ) : executing ? (
                          <div className="proposal-executing">执行中：审批 → 起草 PO → 回读...</div>
                        ) : confirming ? (
                          <div className="proposal-confirm">
                            <small>将执行（草稿级写入，不提交）：</small>
                            {(() => {
                              const needsQuotationApproval = (assistantAnswer.proposal_options?.quotation_status ?? "DRAFT") !== "APPROVED";
                              const qid = assistantAnswer.proposal_options?.quotation_id ?? "";
                              return (
                                <>
                                  {needsQuotationApproval && qid && (
                                    <code>① 审批报价 {qid}（当前状态 {assistantAnswer.proposal_options?.quotation_status}，审批人 {proposalExec?.quotationApprover}）</code>
                                  )}
                                  <code>{needsQuotationApproval ? "②" : "①"} 审批方案 {planId} 选 {opt.option_id}（{opt.supplier_name}，{opt.total_cost} {opt.currency}）→ 创建采购订单草稿</code>
                                  <div className="proposal-confirm-row">
                                    {needsQuotationApproval && qid && (
                                      <input
                                        value={identity?.actor_id ?? proposalExec?.quotationApprover ?? ""}
                                        readOnly
                                        placeholder="等待真实身份"
                                      />
                                    )}
                                    <input
                                      value={identity?.actor_id ?? proposalExec?.approver ?? ""}
                                      readOnly
                                      placeholder="等待真实身份"
                                    />
                                  </div>
                                  <div className="proposal-confirm-row">
                                    <button
                                      className="button primary"
                                      disabled={!identity}
                                      onClick={() => void executeProposal(
                                        planId,
                                        opt.option_id,
                                        proposalExec?.approver ?? "",
                                        needsQuotationApproval && qid
                                          ? { id: qid, needsApproval: true, approver: proposalExec?.quotationApprover ?? "" }
                                          : null,
                                      )}
                                    >
                                      确认批准并起草{needsQuotationApproval ? "（含报价审批）" : ""}
                                    </button>
                                    <button className="button ghost" onClick={() => setProposalExec(null)}>取消</button>
                                  </div>
                                </>
                              );
                            })()}
                          </div>
                        ) : (
                          <button className="button ghost proposal-exec-btn" disabled={!identity} onClick={() => setProposalExec({ option_id: opt.option_id, stage: "confirming", approver: identity?.actor_id ?? "", quotationApprover: identity?.actor_id ?? "" })}>
                            选择此方案并起草 PO（需人工确认）→
                          </button>
                        ))}
                      </div>
                    );
                  })}
                </div>
                {(assistantAnswer.proposal_options.delivery_assessments ?? []).map((d, idx) => (
                  <div key={idx} className={`proposal-delivery ${d.verdict === "arrival_after_due" ? "late" : "ok"}`}>
                    交期影响：{d.conclusion}（到货 {d.material_ready_date} vs 交期 {d.due_date}）
                  </div>
                ))}
                {/* 质量影响明细默认折叠（首屏只留结论行，展开看记录表/批次/处理选项/数据缺口） */}
                {(assistantAnswer.proposal_options.quality_impacts?.length ?? 0) > 0 && (
                  <details className="assistant-fold quality-impact-details">
                    <summary>质量异常影响分析（{assistantAnswer.proposal_options.quality_impacts!.length} 个工单，点击展开明细）</summary>
                    {(assistantAnswer.proposal_options.quality_impacts ?? []).map((q, idx) => (
                      <div key={idx} className="quality-impact-block">
                        <div className={`quality-impact-head ${q.quality_gate_passed ? "ok" : "bad"}`}>
                          工单 {q.work_order_no}
                          （未关闭问题 {q.open_issues_count} · 质量门禁{q.quality_gate_passed ? "通过" : "未通过"}）
                        </div>
                        {q.quality_records.length > 0 && (
                          <table className="md-table">
                            <tbody>
                              {q.quality_records.map((r, i) => (
                                <tr key={i}>
                                  <td>{r.severity}</td>
                                  <td>{r.title}</td>
                                  <td>{r.status}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                        {q.batches.length > 0 && (
                          <div className="quality-impact-batches">
                            批次关联：{q.batches.map((b) => `${b.lot_number || b.batch_id}（${b.status}，目标 ${b.target_qty}）`).join("、")}
                          </div>
                        )}
                        <ul className="quality-impact-conclusions">
                          {q.impact_conclusions.map((c, i) => <li key={i}>{c}</li>)}
                        </ul>
                        {q.handling_options.length > 0 && (
                          <div className="quality-impact-options">
                            <small>可执行处理选项（均需人工确认）：</small>
                            {q.handling_options.map((o, i) => (
                              <div key={i} className="quality-impact-option">{o.option} — {o.how}</div>
                            ))}
                          </div>
                        )}
                        {q.data_gaps.length > 0 && (
                          <div className="quality-impact-gaps">
                            数据缺口（如实标注）：{q.data_gaps.map((g) => g.detail).join("；")}
                          </div>
                        )}
                      </div>
                    ))}
                  </details>
                )}
                {/* 数据缺失明细默认折叠（交接文档 §7：证据按需展开，不丢失事实） */}
                {(assistantAnswer.proposal_options.data_missing?.length ?? 0) > 0 && (
                  <details className="assistant-fold proposal-missing-details">
                    <summary>数据缺失明细（{assistantAnswer.proposal_options.data_missing!.length} 项，点击展开）</summary>
                    {(assistantAnswer.proposal_options.data_missing ?? []).map((m, idx) => (
                      <div key={idx} className="proposal-missing">
                        数据缺失（{m.source_skill}）：{m.missing_fields.map((f) => f.detail).join("；")} → {m.need}
                      </div>
                    ))}
                  </details>
                )}
              </div>
            )}
            {/* 调用链默认折叠：首屏留给结论与关键事实（交接文档 §7） */}
            <details className="assistant-fold assistant-chain-details">
              <summary>
                调用链（{assistantAnswer.tool_count ?? 0} 次智能体调用 · {assistantAnswer.rounds ?? 0} 轮推理 · 记录 {assistantAnswer.coordination_run_id || "—"}
                {assistantAnswer.handled_by && assistantAnswer.handled_by !== "coordinator" && (
                  <>
                    {" "}· <strong>确定性处理</strong>
                    {assistantAnswer.handled_by === "clarify" ? "（信息不足，未调用大模型）" : "（未调用大模型）"}
                  </>
                )}
                ，点击展开）
              </summary>
              {(assistantAnswer.call_chain ?? []).map((step) => (
                <div key={step.seq} className={`assistant-step ${step.status}`}>
                  <span className="assistant-step-seq">{step.seq}</span>
                  <span className="assistant-step-callee">{agentRunTypeNames[step.callee] ?? step.callee}</span>
                  <code className="assistant-step-skill">{step.skill_id}</code>
                  <code className="assistant-step-args">{JSON.stringify(step.arguments)}</code>
                  <span className="assistant-step-status">
                    {step.status === "ok" ? "✓" : "✗"} {step.elapsed_ms}ms
                  </span>
                </div>
              ))}
            </details>
            <p className="field-hint">数据来源：{assistantAnswer.authority}。回答由 DeepSeek 汇总真实工具结果生成；写入类操作不在本通道执行。</p>
          </div>
        )}
      </div>
    </section>
  );
}
