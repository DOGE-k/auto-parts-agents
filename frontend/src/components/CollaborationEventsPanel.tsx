// 跨智能体协同事件面板（P1：质量异常事件自动协同；只读结论 + 失败重试 + 人工接管）
import { useCallback, useEffect, useState } from "react";
import {
  collaborationEventStatusLabels,
  collaborationEventTypeLabels,
  getCollaborationEvents,
  retryCollaborationEvent,
  takeoverCollaborationEvent,
} from "../api";
import type { CollaborationEvent } from "../api";

type Props = {
  notify: (message: string) => void;
  onError: (message: string) => void;
};

export default function CollaborationEventsPanel({ notify, onError }: Props) {
  const [events, setEvents] = useState<CollaborationEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string>("");
  const [busyId, setBusyId] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getCollaborationEvents();
      setEvents(result.items ?? []);
      onError("");
    } catch (e) {
      onError(e instanceof Error ? e.message : "协同事件加载失败");
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const doRetry = useCallback(async (eventId: string) => {
    setBusyId(eventId);
    try {
      await retryCollaborationEvent(eventId);
      notify(`事件 ${eventId} 已重试`);
      await load();
    } catch (e) {
      onError(e instanceof Error ? e.message : "重试失败");
    } finally {
      setBusyId("");
    }
  }, [load, notify, onError]);

  const doTakeover = useCallback(async (eventId: string) => {
    setBusyId(eventId);
    try {
      await takeoverCollaborationEvent(eventId);
      notify(`事件 ${eventId} 已人工接管（处置请走 NCR 审批门禁）`);
      await load();
    } catch (e) {
      onError(e instanceof Error ? e.message : "接管失败");
    } finally {
      setBusyId("");
    }
  }, [load, notify, onError]);

  return (
    <section className="panel collab-events-panel">
      <div className="panel-heading">
        <div>
          <h2>协同事件（自动协作）</h2>
          <p>质量问题经人工审批登记后自动触发只读协同：质量影响 → 生产交期 → 供应商风险。结论仅供参考，处置与写入仍走审批门禁。</p>
        </div>
        <button className="button ghost" onClick={() => void load()} disabled={loading}>
          {loading ? "加载中…" : "↻ 刷新"}
        </button>
      </div>
      {events !== null && events.length === 0 && (
        <p className="field-hint">当前没有协同事件。在第 8 步"登记质量问题"后会自动产生一条。</p>
      )}
      {(events ?? []).map((ev) => {
        const detail = expanded === ev.event_id ? ev.result : null;
        const canRetry = ev.status === "FAILED" && ev.failure_count < ev.max_retries;
        const canTakeover = ev.status !== "MANUAL_HANDLED";
        return (
          <div key={ev.event_id} className={`collab-event-card status-${ev.status.toLowerCase()}`}>
            <div className="collab-event-head">
              <code className="collab-event-id">{ev.event_id}</code>
              <span className="badge type-badge">{collaborationEventTypeLabels[ev.event_type] ?? ev.event_type}</span>
              <span className={`badge status-badge-${ev.status.toLowerCase()}`}>
                {collaborationEventStatusLabels[ev.status] ?? ev.status}
              </span>
              <span className="collab-event-meta">
                {ev.payload?.work_order_no ? `工单 ${ev.payload.work_order_no}` : `工单 id ${ev.payload?.work_order_id ?? "?"}`}
                {ev.payload?.issue_id ? ` · 问题 #${ev.payload.issue_id}` : ""}
                {ev.payload?.title ? ` · ${ev.payload.title}` : ""}
              </span>
              <span className="collab-event-meta">
                {ev.created_at ? new Date(ev.created_at).toLocaleString("zh-CN", { hour12: false }) : ""}
              </span>
            </div>
            {ev.status === "FAILED" && ev.error?.message && (
              <div className="collab-event-error">失败原因：{ev.error.message}（失败 {ev.failure_count}/{ev.max_retries} 次）</div>
            )}
            {ev.status === "MANUAL_HANDLED" && (
              <div className="collab-event-takenover">
                已由 {ev.taken_over_by || "人工"} 接管（{ev.taken_over_at ? new Date(ev.taken_over_at).toLocaleString("zh-CN", { hour12: false }) : ""}）
              </div>
            )}
            <div className="collab-event-actions">
              <button
                className="button ghost"
                onClick={() => setExpanded(expanded === ev.event_id ? "" : ev.event_id)}
              >
                {expanded === ev.event_id ? "收起协同结论" : "查看协同结论"}
              </button>
              {canRetry && (
                <button className="button ghost" disabled={busyId === ev.event_id} onClick={() => void doRetry(ev.event_id)}>
                  重试协同
                </button>
              )}
              {canTakeover && (
                <button className="button ghost" disabled={busyId === ev.event_id} onClick={() => void doTakeover(ev.event_id)}>
                  人工接管
                </button>
              )}
            </div>
            {detail && (
              <div className="collab-event-detail">
                {(detail.conclusions ?? []).map((c: string, i: number) => (
                  <div key={i} className="collab-conclusion">{c}</div>
                ))}
                {detail.quality_impact && (
                  <div className="collab-section">
                    <strong>质量维度</strong>
                    <span>
                      未关闭问题 {detail.quality_impact.open_issues_count ?? "?"} 项 ·
                      质量门禁{detail.quality_impact.quality_gate_passed ? "通过" : "未通过"}
                      {((detail.quality_impact.missing_documents ?? []) as string[]).length > 0 &&
                        `（缺文档：${(detail.quality_impact.missing_documents ?? []).join("、")}）`}
                    </span>
                  </div>
                )}
                {detail.tracking && (
                  <div className="collab-section">
                    <strong>生产维度</strong>
                    <span>
                      完成率 {detail.tracking.completion_rate ?? "?"}% · 交期 {detail.tracking.due_date || "—"} ·
                      ETA 口径 {detail.tracking.eta_status || "—"}
                    </span>
                  </div>
                )}
                {detail.procurement_risk ? (
                  <div className="collab-section">
                    <strong>采购维度</strong>
                    <span>
                      {detail.procurement_risk.has_shortage
                        ? `缺料 ${detail.procurement_risk.shortage_count} 项 · ${detail.procurement_risk.supplier_options.length} 个供应商选项`
                        : "当前库存充足，无缺料"}
                      {" "}· 方案 {detail.procurement_risk.plan_id}
                    </span>
                  </div>
                ) : null}
                {(detail.data_gaps ?? []).length > 0 && (
                  <div className="collab-section gaps">
                    <strong>数据缺口（如实标注）</strong>
                    <ul>
                      {(detail.data_gaps ?? []).map((g: { dimension: string; detail: string }, i: number) => (
                        <li key={i}>[{g.dimension}] {g.detail}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="field-hint">{detail.authority}</p>
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}
