// Agent 运行记录面板（阶段三：页面可查，持久化；阶段十 10.5 从 RealBusinessPage 抽出，状态自包含）
import { useState, useCallback } from "react";
import { api, agentRunTypeNames } from "../api";
import type { AgentRunSummary, AgentRunDetail, AgentRunEvidenceItem } from "../api";

export default function AgentRunsPanel() {
  const [agentRunsOpen, setAgentRunsOpen] = useState(false);
  const [agentRuns, setAgentRuns] = useState<AgentRunSummary[]>([]);
  const [agentRunsLoading, setAgentRunsLoading] = useState(false);
  const [agentRunsError, setAgentRunsError] = useState("");
  const [agentTypeFilter, setAgentTypeFilter] = useState("");
  const [expandedRunId, setExpandedRunId] = useState("");
  const [agentRunDetail, setAgentRunDetail] = useState<AgentRunDetail | null>(null);

  // 查询 Agent 运行记录（持久化在数据库中，服务重启后仍可查）
  const loadAgentRuns = useCallback(async (agentType: string) => {
    setAgentRunsLoading(true);
    setAgentRunsError("");
    try {
      const query = agentType ? `?agent_type=${encodeURIComponent(agentType)}&limit=30` : "?limit=30";
      const runs = await api<AgentRunSummary[]>(`/real-orders/agent-runs${query}`);
      setAgentRuns(runs);
    } catch (e) {
      setAgentRunsError(e instanceof Error ? e.message : "Agent 运行记录查询失败");
    } finally {
      setAgentRunsLoading(false);
    }
  }, []);

  const toggleRunDetail = useCallback(async (runId: string) => {
    if (expandedRunId === runId) {
      setExpandedRunId("");
      setAgentRunDetail(null);
      return;
    }
    setExpandedRunId(runId);
    setAgentRunDetail(null);
    try {
      const detail = await api<AgentRunDetail>(`/real-orders/agent-runs/${encodeURIComponent(runId)}`);
      setAgentRunDetail(detail);
    } catch (e) {
      setAgentRunsError(e instanceof Error ? e.message : "运行记录详情加载失败");
    }
  }, [expandedRunId]);

  return (
    <section className="panel agent-runs-panel">
      <div className="panel-heading">
        <div>
          <h2>Agent 运行记录</h2>
          <p>四个 Agent 与审批/写入通道每次真实运行的输入、结果与错误，持久化存储，服务重启后仍可查询</p>
        </div>
        <button
          className="button ghost"
          onClick={() => {
            const next = !agentRunsOpen;
            setAgentRunsOpen(next);
            if (next) void loadAgentRuns(agentTypeFilter);
          }}
        >
          {agentRunsOpen ? "收起 ▲" : "查询运行记录 ▼"}
        </button>
      </div>
      {agentRunsOpen && (
        <div className="agent-runs-body">
          <div className="agent-runs-toolbar">
            <select
              value={agentTypeFilter}
              onChange={(e) => {
                setAgentTypeFilter(e.target.value);
                setExpandedRunId("");
                setAgentRunDetail(null);
                void loadAgentRuns(e.target.value);
              }}
            >
              <option value="">全部 Agent</option>
              {Object.entries(agentRunTypeNames).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            <button className="button ghost" onClick={() => void loadAgentRuns(agentTypeFilter)}>
              刷新
            </button>
            {agentRunsLoading && <small>加载中...</small>}
          </div>
          {agentRunsError && <p className="field-hint">{agentRunsError}</p>}
          {!agentRunsLoading && !agentRunsError && agentRuns.length === 0 && (
            <p className="field-hint">暂无运行记录（执行一次报价/采购/跟单操作后这里会出现记录）</p>
          )}
          {agentRuns.length > 0 && (
            <div className="agent-run-list">
              {agentRuns.map((run) => (
                <div key={run.run_id} className={`agent-run-item ${expandedRunId === run.run_id ? "expanded" : ""}`}>
                  <button className="agent-run-row" onClick={() => void toggleRunDetail(run.run_id)}>
                    <span className={`agent-run-status ${run.result_status === "ok" ? "ok" : "err"}`}>
                      {run.result_status === "ok" ? "✓" : "✗"}
                    </span>
                    <span className="agent-run-id">{run.run_id}</span>
                    <span className="agent-run-type">{agentRunTypeNames[run.agent_type] ?? run.agent_type}</span>
                    <span className="agent-run-op">{run.operation}</span>
                    <span className="agent-run-summary">{run.result_summary || "—"}</span>
                    <span className="agent-run-time">{run.started_at?.replace("T", " ").slice(0, 19) ?? ""}</span>
                  </button>
                  {expandedRunId === run.run_id && (
                    <div className="agent-run-detail">
                      {!agentRunDetail && <small>加载详情中...</small>}
                      {agentRunDetail && (
                        <>
                          <div className="agent-run-input">
                            <small>输入</small>
                            <code>{JSON.stringify(agentRunDetail.input?.args ?? [])}{JSON.stringify(agentRunDetail.input?.kwargs ?? {})}</code>
                          </div>
                          {agentRunDetail.error && (
                            <div className="agent-run-error">
                              <small>错误</small>
                              <code>{agentRunDetail.error.type}: {agentRunDetail.error.message}</code>
                            </div>
                          )}
                          {(agentRunDetail.result?.evidence ?? []).length > 0 && (
                            <div className="agent-run-evidence">
                              <small>证据链（真实记录编号，可追溯）</small>
                              {(agentRunDetail.result?.evidence as AgentRunEvidenceItem[]).map((ev, idx) => (
                                <div key={idx} className="evidence-item">
                                  <span className="evidence-source">{ev.source ?? "—"}</span>
                                  <span className="evidence-type">{ev.record_type ?? "—"}</span>
                                  <code className="evidence-id">{ev.record_id ?? "—"}</code>
                                  <span className="evidence-summary">{ev.summary ?? ""}</span>
                                </div>
                              ))}
                            </div>
                          )}
                          {((agentRunDetail.result?.data_limitations as Record<string, string>[]) ?? []).length > 0 && (
                            <div className="agent-run-limitations">
                              <small>数据限制（如实标注）</small>
                              {(agentRunDetail.result?.data_limitations as Record<string, string>[]).map((lim, idx) => (
                                <div key={idx} className="limitation-item">{lim.field ?? ""} {lim.reason ?? JSON.stringify(lim)}</div>
                              ))}
                            </div>
                          )}
                          <div className="agent-run-times">
                            <small>开始 {agentRunDetail.started_at?.replace("T", " ").slice(0, 19) ?? "—"} · 结束 {agentRunDetail.finished_at?.replace("T", " ").slice(0, 19) ?? "—"}</small>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
