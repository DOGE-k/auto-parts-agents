// 业务总览（信息架构改版 §4.4：第二入口，按优先级给"需要处理"，不在首页展开长流程）。
// 数据全部来自真实端点：报价/方案审批状态、协同事件、质量待办、Agent 运行记录。
import { useCallback, useEffect, useState } from "react";
import { agentRunTypeNames, api, getCollaborationEvents } from "../api";
import type { CollaborationEvent } from "../api";
import type { ProcurementPlan, Quotation } from "../types/realBusiness";

type Props = {
  hasOpenmesSession: boolean;
  qualityTodoCount: number | null;
  onNavigateAssistant: (question: string) => void;
  onNavigate: (module: "sales" | "procurement" | "production" | "quality" | "audit") => void;
  /** 评审复检：需要处理的行携带事件编号定位打开对应对象，而不是只切模块 */
  onOpenEvent?: (event: CollaborationEvent) => void;
};

type RunRow = {
  run_id: string;
  agent_type: string;
  operation: string;
  result_status: string;
  result_summary?: string;
  started_at?: string;
};

export default function BusinessOverviewPanel({ hasOpenmesSession, qualityTodoCount, onNavigateAssistant, onNavigate, onOpenEvent }: Props) {
  const [quotationPending, setQuotationPending] = useState<number | null>(null);
  const [planPending, setPlanPending] = useState<number | null>(null);
  const [events, setEvents] = useState<CollaborationEvent[] | null>(null);
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [quotations, plans, eventList, runList] = await Promise.all([
        api<Quotation[]>("/real-orders/quotations"),
        api<ProcurementPlan[]>("/real-orders/procurement/plans"),
        getCollaborationEvents(50),
        api<RunRow[]>("/real-orders/agent-runs?limit=5"),
      ]);
      setQuotationPending(quotations.filter((q) => q.status === "PENDING_APPROVAL").length);
      setPlanPending(plans.filter((p) => p.status === "PENDING_APPROVAL").length);
      setEvents(eventList.items ?? []);
      setRuns(Array.isArray(runList) ? runList : []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "总览数据加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const allEvents = events ?? [];
  const shortageEvents = allEvents.filter((e) => e.event_type === "material_shortage");
  const riskEvents = allEvents.filter((e) => e.event_type === "production_overdue" || e.event_type === "production_at_risk");
  const qualityEvents = allEvents.filter((e) => e.event_type === "quality_issue_raised");
  // 需要处理：未完成且未接管的事件按创建时间倒序
  const attention = allEvents
    .filter((e) => e.status !== "COMPLETED" && e.status !== "MANUAL_HANDLED")
    .slice(0, 5);
  const attentionHint: Record<string, string> = {
    material_shortage: "去采购与缺料处理方案",
    production_overdue: "查看生产跟单",
    production_at_risk: "查看生产跟单",
    quality_issue_raised: "去质量中心处置",
  };
  const attentionTarget: Record<string, "procurement" | "production" | "quality"> = {
    material_shortage: "procurement",
    production_overdue: "production",
    production_at_risk: "production",
    quality_issue_raised: "quality",
  };

  return (
    <div className="overview-panel">
      <section className="overview-kpis">
        <button className="kpi-card clickable" onClick={() => onNavigate("sales")}>
          <span className="kpi-icon amber">✓</span><span className="kpi-label">报价待审批</span>
          <strong>{quotationPending ?? "—"}</strong>
        </button>
        <button className="kpi-card clickable" onClick={() => onNavigate("procurement")}>
          <span className="kpi-icon amber">⇄</span><span className="kpi-label">方案待审批</span>
          <strong>{planPending ?? "—"}</strong>
        </button>
        <button className="kpi-card clickable" onClick={() => onNavigate("quality")}>
          <span className="kpi-icon red">⚠</span><span className="kpi-label">质量待办</span>
          <strong>{hasOpenmesSession ? (qualityTodoCount ?? "—") : "未登录"}</strong>
        </button>
        <button className="kpi-card clickable" onClick={() => onNavigate("quality")}>
          <span className="kpi-icon purple">⌁</span><span className="kpi-label">缺料/风险事件</span>
          <strong>{shortageEvents.length + riskEvents.length}</strong>
        </button>
      </section>

      {error && <div className="error-banner"><span>总览加载失败</span> {error}<button onClick={() => setError("")}>×</button></div>}

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>需要处理</h2>
            <p>未完成、未接管的协同事件按时间排列；处置仍在对应业务模块完成。</p>
          </div>
          <button className="button ghost" onClick={() => void load()}>↻ 刷新</button>
        </div>
        {attention.length === 0 ? (
          <div className="empty-state compact">
            当前没有待处理的协同事件。质量异常、缺料、延期/临期发生后会自动出现在这里。
          </div>
        ) : (
          <div className="attention-list">
            {attention.map((e) => (
              <div className="attention-row" key={e.event_id}>
                <code>{e.event_id}</code>
                <span className="badge type-badge">{e.event_type}</span>
                <span className="attention-meta">{e.status} · {e.payload?.work_order_no ? `工单 ${e.payload.work_order_no}` : e.payload?.quotation_id ? `报价 ${e.payload.quotation_id}` : "—"}</span>
                <button
                  className="button ghost"
                  onClick={() => (onOpenEvent ? onOpenEvent(e) : onNavigate(attentionTarget[e.event_type] ?? "quality"))}
                >
                  {attentionHint[e.event_type] ?? "去查看"}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="overview-columns">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>最近业务链</h2>
              <p>智能体最近的真实调用（谁、干了什么、结果如何）。</p>
            </div>
            <button className="button ghost" onClick={() => onNavigate("audit")}>全部记录</button>
          </div>
          {(runs ?? []).length === 0 ? (
            <div className="empty-state compact">还没有调用记录。去协同问答提一个问题试试。</div>
          ) : (
            <div className="run-list">
              {(runs ?? []).map((r) => (
                <div className="run-row" key={r.run_id}>
                  <span className="badge blue-badge">{r.agent_type}</span>
                  <span>{agentRunTypeNames[r.operation] ?? r.operation}</span>
                  <code>{(r.result_summary ?? "").slice(0, 40)}</code>
                  <b className={r.result_status === "ok" ? "text-green" : "text-red"}>{r.result_status}</b>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="panel overview-ask">
          <div className="panel-heading">
            <div>
              <h2>AI 协同入口</h2>
              <p>一句话查订单、缺料、交期、质量；写操作仍停在人工审批。</p>
            </div>
          </div>
          <div className="overview-ask-row">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="例如：SAL-ORD-2026-00023 什么时候能做完？"
              onKeyDown={(e) => {
                if (e.key === "Enter" && question.trim()) onNavigateAssistant(question.trim());
              }}
            />
            <button className="button primary" disabled={!question.trim()} onClick={() => onNavigateAssistant(question.trim())}>
              去提问 →
            </button>
          </div>
          <p className="field-hint">问题会带到「AI 协同问答」，确认后再发送。</p>
        </section>
      </div>
    </div>
  );
}
