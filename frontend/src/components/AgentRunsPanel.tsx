// Agent 运行记录面板（阶段三：页面可查，持久化；阶段十 10.5 从 RealBusinessPage 抽出，状态自包含）
// 前端优化清单任务 8：详情区改为结构化摘要卡（输入参数逐项 + 结果关键字段一句中文），
// 完整 JSON 收进次级 <details> 折叠保留；提取复用 lib/result-summary（与任务 7 调用链同一套逻辑）。
import { useState, useCallback } from "react";
import { api, agentRunOperationNames, agentRunTypeNames } from "../api";
import type { AgentRunSummary, AgentRunDetail, AgentRunEvidenceItem } from "../api";
import { displaySystemText } from "../lib/system-labels";
import { summarizeRunInput, summarizeRunResult } from "../lib/result-summary";

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
          {agentRunsError && <p className="field-hint">{displaySystemText(agentRunsError)}</p>}
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
                    <span className="agent-run-type">{displaySystemText(agentRunTypeNames[run.agent_type] ?? run.agent_type)}</span>
                    <span className="agent-run-op">{agentRunOperationNames[`${run.agent_type}:${run.operation}`] ?? agentRunOperationNames[run.operation] ?? displaySystemText(run.operation)}</span>
                    <span className="agent-run-summary">{displaySystemText(run.result_summary || "—")}</span>
                    <span className="agent-run-time">{run.started_at?.replace("T", " ").slice(0, 19) ?? ""}</span>
                  </button>
                  {expandedRunId === run.run_id && (
                    <div className="agent-run-detail">
                      {!agentRunDetail && <small>加载详情中...</small>}
                      {agentRunDetail && (
                        <>
                          {(() => {
                            const inputItems = summarizeRunInput(agentRunDetail.input);
                            const resultBrief = summarizeRunResult(
                              agentRunDetail.agent_type,
                              agentRunDetail.operation,
                              agentRunDetail.result,
                            );
                            return (
                              <>
                                <div className="agent-run-brief">
                                  <small>输入参数</small>
                                  {inputItems.length ? (
                                    <ul className="run-arg-list">
                                      {inputItems.map((item, idx) => (
                                        <li key={idx}>
                                          <span className="run-arg-name">{item.name}</span>
                                          <span className="run-arg-value">{item.value}</span>
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="agent-run-brief-result">—</p>
                                  )}
                                </div>
                                {resultBrief && (
                                  <div className="agent-run-brief">
                                    <small>结果摘要</small>
                                    <p className="agent-run-brief-result">{resultBrief}</p>
                                  </div>
                                )}
                              </>
                            );
                          })()}
                          {agentRunDetail.error && (
                            <div className="agent-run-error">
                              <small>错误</small>
                              <code>{displaySystemText(agentRunDetail.error.type)}: {displaySystemText(agentRunDetail.error.message)}</code>
                            </div>
                          )}
                          {(agentRunDetail.result?.evidence ?? []).length > 0 && (
                            <div className="agent-run-evidence">
                              <small>证据链（真实记录编号，可追溯）</small>
                              {(agentRunDetail.result?.evidence as AgentRunEvidenceItem[]).map((ev, idx) => (
                                <div key={idx} className="evidence-item">
                                  <span className="evidence-source">{displaySystemText(ev.source ?? "—")}</span>
                                  <span className="evidence-type">{displaySystemText(ev.record_type ?? "—")}</span>
                                  <code className="evidence-id">{ev.record_id ?? "—"}</code>
                                  <span className="evidence-summary">{displaySystemText(ev.summary ?? "")}</span>
                                </div>
                              ))}
                            </div>
                          )}
                          {((agentRunDetail.result?.data_limitations as Record<string, string>[]) ?? []).length > 0 && (
                            <div className="agent-run-limitations">
                              <small>数据限制（如实标注）</small>
                              {(agentRunDetail.result?.data_limitations as Record<string, string>[]).map((lim, idx) => (
                                <div key={idx} className="limitation-item">{displaySystemText(lim.field ?? "")} {displaySystemText(lim.reason ?? JSON.stringify(lim))}</div>
                              ))}
                            </div>
                          )}
                          <div className="agent-run-times">
                            <small>开始 {agentRunDetail.started_at?.replace("T", " ").slice(0, 19) ?? "—"} · 结束 {agentRunDetail.finished_at?.replace("T", " ").slice(0, 19) ?? "—"}</small>
                          </div>
                          <details className="agent-run-raw">
                            <summary>完整 JSON 原文（输入 / 结果 / 错误）</summary>
                            <pre>
                              {JSON.stringify(
                                { input: agentRunDetail.input, result: agentRunDetail.result, error: agentRunDetail.error },
                                null,
                                2,
                              )}
                            </pre>
                          </details>
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
