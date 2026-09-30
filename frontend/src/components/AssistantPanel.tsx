// 智能协同问答面板（阶段五-八：协调智能体动态调用四个真实智能体 + 方案批准执行闭环；阶段十 10.5 从 RealBusinessPage 抽出）
import { useState, useCallback } from "react";
import { formatAssistantAnswer } from "../lib/markdown";
import {
  agentRunTypeNames,
  askAssistant,
  approveProcurementPlan,
  approveQuotation as approveQuotationApi,
  createPoFromPlan,
} from "../api";
import type { AssistantAnswer, ProposalSupplierOption, RealIdentity } from "../api";

type Props = {
  identity: RealIdentity | null;
  currentErpDraftId: string;
  currentWorkOrderId: string;
  notify: (message: string) => void;
  onError: (message: string) => void;
};

export default function AssistantPanel({
  identity,
  currentErpDraftId,
  currentWorkOrderId,
  notify,
  onError,
}: Props) {
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState<AssistantAnswer | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(false);
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
    setAssistantAnswer(null);
    onError("");
    try {
      const context: Record<string, unknown> = {};
      if (currentErpDraftId) context.当前流程ERP订单号 = currentErpDraftId;
      if (currentWorkOrderId) context.当前流程MES工单id = currentWorkOrderId;
      const result = await askAssistant(q, context);
      setAssistantAnswer(result);
      notify("协调智能体已回答");
    } catch (e) {
      onError(e instanceof Error ? e.message : "协调智能体调用失败");
    } finally {
      setAssistantLoading(false);
    }
  }, [assistantQuestion, assistantLoading, currentErpDraftId, currentWorkOrderId, notify, onError]);

  return (
    <section className="panel assistant-panel">
      <div className="panel-heading">
        <div>
          <h2>智能协同问答</h2>
          <p>直接用一句话提问（如"SAL-ORD-2026-00023 什么时候能做完"），协调智能体会自己判断需要什么数据，动态调用报价/采购/跟单/质量四个智能体查询真实 ERP/MES 后回答，调用链全程留痕</p>
        </div>
        <span className="badge blue-badge">动态协同 · 只读</span>
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
      </div>
      {assistantAnswer && (
        <div className="assistant-result">
          <div
            className="assistant-answer md-body"
            dangerouslySetInnerHTML={{ __html: formatAssistantAnswer(assistantAnswer.answer) }}
          />
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
              {(assistantAnswer.proposal_options.quality_impacts ?? []).map((q, idx) => (
                <div key={idx} className="quality-impact-block">
                  <div className={`quality-impact-head ${q.quality_gate_passed ? "ok" : "bad"}`}>
                    质量异常影响分析 · 工单 {q.work_order_no}
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
              {(assistantAnswer.proposal_options.data_missing ?? []).map((m, idx) => (
                <div key={idx} className="proposal-missing">
                  数据缺失（{m.source_skill}）：{m.missing_fields.map((f) => f.detail).join("；")} → {m.need}
                </div>
              ))}
            </div>
          )}
          <div className="assistant-chain">
            <small>
              调用链（{assistantAnswer.tool_count} 次智能体调用 · {assistantAnswer.rounds} 轮推理 · 记录 {assistantAnswer.coordination_run_id}）
            </small>
            {assistantAnswer.call_chain.map((step) => (
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
          <p className="field-hint">数据来源：{assistantAnswer.authority}。回答由 DeepSeek 汇总真实工具结果生成；写入类操作不在本通道执行。</p>
        </div>
      )}
    </section>
  );
}
