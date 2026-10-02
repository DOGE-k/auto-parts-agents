// NCR 人工处置与关闭卡片（阶段八-十：处置/关闭双审批线，写回后回读；10.5 从 RealBusinessPage 抽出为展示组件）
// 2026-10-02 补"解决"通道：处置写回 ≠ 问题解决——issue 状态需经"解决审批 → resolve 写回"
// 翻成 RESOLVED，关闭前置条件"问题已解决"才满足（此前前端未暴露该通道，用户卡在让步接收后）。
import { useState } from "react";
import type { NcrForm, NcrWorkflow } from "../types/realBusiness";
import type { RealIdentity, RealQualityIssue } from "../api";

type Props = {
  issue: RealQualityIssue;
  workflow: NcrWorkflow | undefined;
  focused: boolean;
  identity: RealIdentity | null;
  dataSource: string;
  onFormChange: (issueId: string, field: keyof NcrForm, value: string) => void;
  onRequestDisposition: (issue: RealQualityIssue) => void;
  onApproveDisposition: (issueId: string) => void;
  onWriteDisposition: (issueId: string) => void;
  onRequestResolution: (issueId: string, resolutionNotes: string) => void;
  onApproveResolution: (issueId: string) => void;
  onWriteResolution: (issueId: string, resolutionNotes: string) => void;
  onCheckClosure: (issueId: string) => void;
  onRequestClose: (issueId: string) => void;
  onApproveClose: (issueId: string) => void;
  onWriteClose: (issueId: string) => void;
  /** 缺料类问题跳转采购与缺料模块（补库存在采购侧处理，非 NCR 处置出口） */
  onGoProcurement?: () => void;
};

const CLOSURE_CHECK_LABELS: Record<string, string> = {
  resolved: "问题已解决",
  disposition_recorded: "已登记处置",
  root_cause_recorded: "已记录根因",
  containment_action_recorded: "已记录遏制措施",
  corrective_actions_verified: "纠正措施已验证",
};

export default function NcrWorkflowCard({
  issue,
  workflow,
  focused,
  identity,
  dataSource,
  onFormChange,
  onRequestDisposition,
  onApproveDisposition,
  onWriteDisposition,
  onRequestResolution,
  onApproveResolution,
  onWriteResolution,
  onCheckClosure,
  onRequestClose,
  onApproveClose,
  onWriteClose,
  onGoProcurement,
}: Props) {
  const issueId = String(issue.record_id);
  const [resolutionNotes, setResolutionNotes] = useState("");
  const form = workflow?.form ?? { disposition: "", non_conforming_qty: "", root_cause: "", containment_action: "", nc_source: "" };
  const busy = Boolean(workflow?.busy);
  const statusUpper = String(issue.status ?? "").toUpperCase();
  const isClosed = statusUpper === "CLOSED";
  const isResolved = statusUpper === "RESOLVED" || isClosed;
  const dispositionRecorded = ["scrap", "rework", "return_to_supplier", "use_as_is"].includes(String(issue.disposition ?? "").toLowerCase());
  return (
    <div key={issueId} id={`ncr-issue-${issueId}`} className={`ncr-workflow-card ${focused ? "ncr-issue-focus" : ""}`}>
      <div className="ncr-workflow-title">
        <div>
          <strong>{issue.title || issue.record_type || `质量问题 ${issueId}`}</strong>
          <small>#{issueId} · {issue.severity ?? "未标严重度"} · 当前状态 {issue.status ?? "未知"} · 当前处置 {issue.disposition ?? "pending"}</small>
        </div>
        <span className={`badge ${isClosed ? "green" : dispositionRecorded ? "blue-badge" : "red-badge"}`}>
          {isClosed ? "已关闭" : dispositionRecorded ? "已登记处置" : "待处置"}
        </span>
      </div>
      {issue.description && <p className="ncr-description">{issue.description}</p>}
      {!isClosed && (
        <>
          {String(issue.record_type ?? "").toLowerCase().includes("shortage") && (
            <div className="ncr-shortage-hint">
              <strong>缺料类问题（{issue.record_type}）：补库存走采购，不走 NCR 处置出口</strong>
              <span>OpenMES 的 NCR 处置出口只有四种（报废/返工/退供应商/让步接收），不含"补库存"——这是上游 API 的定义，系统不伪造第五种。补库存的业务动作在「采购与缺料」模块：缺料分析 → 供应商方案 → PO 采购补货 → 到货。物料到位后，本 NCR 一般按"让步接收（use_as_is）"关闭，并把采购单号写进遏制措施留痕。</span>
              {onGoProcurement && (
                <button className="button ghost" onClick={onGoProcurement}>去采购与缺料（补库存）→</button>
              )}
            </div>
          )}
          <div className="ncr-form-grid">
            <label>
              处置方案（人工选择）
              <select value={form.disposition} onChange={(e) => onFormChange(issueId, "disposition", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)}>
                <option value="">请选择，不自动推断</option>
                <option value="scrap">报废（scrap）</option>
                <option value="rework">返工（rework）</option>
                <option value="return_to_supplier">退供应商（return_to_supplier）</option>
                <option value="use_as_is">让步接收（use_as_is）</option>
              </select>
            </label>
            <label>
              不合格数量（可选）
              <input type="number" min="0" value={form.non_conforming_qty} onChange={(e) => onFormChange(issueId, "non_conforming_qty", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)} />
            </label>
            <label>
              NC 来源（可选）
              <select value={form.nc_source} onChange={(e) => onFormChange(issueId, "nc_source", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)}>
                <option value="">未指定</option>
                <option value="internal">内部</option>
                <option value="supplier">供应商</option>
                <option value="external">外部</option>
              </select>
            </label>
            <label className="ncr-wide-field">
              根因
              <textarea value={form.root_cause} onChange={(e) => onFormChange(issueId, "root_cause", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)} rows={2} placeholder="填写可审计的根因" />
            </label>
            <label className="ncr-wide-field">
              遏制措施
              <textarea value={form.containment_action} onChange={(e) => onFormChange(issueId, "containment_action", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)} rows={2} placeholder="填写已执行或计划执行的遏制措施" />
            </label>
          </div>
          <div className="ncr-action-row">
            <button className="button ghost" disabled={busy || Boolean(workflow?.dispositionApprovalId)} onClick={() => onRequestDisposition(issue)}>
              {workflow?.busy === "requesting_disposition" ? "建立审批中…" : "① 建立处置审批"}
            </button>
            {workflow?.dispositionApprovalId && <code>审批号 {workflow.dispositionApprovalId}</code>}
            {workflow?.dispositionApprovalId && !workflow.dispositionApproved && (
              <button className="button ghost" disabled={busy} onClick={() => onApproveDisposition(issueId)}>
                {workflow?.busy === "approving_disposition" ? "审批中…" : "② 批准处置"}
              </button>
            )}
            {workflow?.dispositionApprovalId && workflow.dispositionApproved && (
              <button className="button primary" disabled={busy} onClick={() => onWriteDisposition(issueId)}>
                {workflow?.busy === "writing_disposition" ? "写回并回读中…" : "③ 写回 OpenMES"}
              </button>
            )}
          </div>
          {workflow?.dispositionResult && (
            <div className={`ncr-result ${workflow.dispositionResult.error ? "error" : "ok"}`}>
              处置状态：{workflow.dispositionResult.error ?? workflow.dispositionResult.status ?? "未知"}
              {workflow.dispositionResult.read_back_verified ? " · 回读已验证" : ""}
              {workflow.dispositionResult.idempotent ? " · 幂等命中" : ""}
            </div>
          )}
          {/* 解决通道：处置已登记但 issue 状态仍是 OPEN 时，"问题已解决"前置条件
              必须走"解决审批 → resolve 写回"才能满足（让步接收也不例外） */}
          {!isResolved && dispositionRecorded && (
            <div className="ncr-resolution-block">
              <small>处置已登记，但问题状态仍为 {issue.status ?? "OPEN"}——关闭前需先"解决"（状态翻成 RESOLVED）。让步接收也要写明放行依据。</small>
              <textarea
                value={resolutionNotes}
                onChange={(e) => setResolutionNotes(e.target.value)}
                rows={2}
                placeholder="解决说明（必填，例如：让步接收 use_as_is，经评审放行入库）"
                disabled={busy || Boolean(workflow?.resolutionApprovalId)}
              />
              <div className="ncr-action-row">
                <button className="button ghost" disabled={busy || Boolean(workflow?.resolutionApprovalId)} onClick={() => onRequestResolution(issueId, resolutionNotes)}>
                  {workflow?.busy === "requesting_resolution" ? "建立审批中…" : "① 建立解决审批"}
                </button>
                {workflow?.resolutionApprovalId && <code>审批号 {workflow.resolutionApprovalId}</code>}
                {workflow?.resolutionApprovalId && !workflow.resolutionApproved && (
                  <button className="button ghost" disabled={busy} onClick={() => onApproveResolution(issueId)}>
                    {workflow?.busy === "approving_resolution" ? "审批中…" : "② 批准解决"}
                  </button>
                )}
                {workflow?.resolutionApprovalId && workflow.resolutionApproved && (
                  <button className="button primary" disabled={busy} onClick={() => onWriteResolution(issueId, resolutionNotes)}>
                    {workflow?.busy === "writing_resolution" ? "写回并回读中…" : "③ 写回解决"}
                  </button>
                )}
              </div>
              {workflow?.resolutionResult && (
                <div className={`ncr-result ${workflow.resolutionResult.error ? "error" : "ok"}`}>
                  解决状态：{workflow.resolutionResult.error ?? workflow.resolutionResult.status ?? "未知"}
                  {workflow.resolutionResult.read_back_verified ? " · 回读已验证" : ""}
                  {workflow.resolutionResult.idempotent ? " · 幂等命中" : ""}
                </div>
              )}
            </div>
          )}
          {isResolved && !isClosed && (
            <div className="ncr-result ok">问题已解决（{statusUpper}）——可继续走下方关闭链路。</div>
          )}
          <div className="ncr-close-row">
            <button className="button ghost" disabled={busy} onClick={() => onCheckClosure(issueId)}>
              {workflow?.busy === "checking_closure" ? "校验中…" : "读取关闭前置条件"}
            </button>
            {workflow?.closureCheck && (
              <span className={`ncr-check-status ${workflow.closureCheck.closure_ready ? "ready" : "blocked"}`}>
                {workflow.closureCheck.closure_ready ? "关闭前置条件已满足" : "仍有前置条件未满足"}
              </span>
            )}
          </div>
          {workflow?.closureCheck && (
            <div className="ncr-check-details">
              {Object.entries(workflow.closureCheck.checks ?? {}).map(([key, passed]) => (
                <span key={key} className={passed ? "check-pass" : "check-fail"}>{passed ? "✓" : "✗"} {CLOSURE_CHECK_LABELS[key] ?? key}</span>
              ))}
            </div>
          )}
          {workflow?.closureCheck?.closure_ready && (
            <div className="ncr-action-row close-actions">
              <button className="button ghost" disabled={busy || Boolean(workflow.closeApprovalId)} onClick={() => onRequestClose(issueId)}>
                {workflow?.busy === "requesting_close" ? "建立关闭审批中…" : "① 建立关闭审批"}
              </button>
              {workflow?.closeApprovalId && <code>关闭审批号 {workflow.closeApprovalId}</code>}
              {workflow?.closeApprovalId && !workflow.closeApproved && (
                <button className="button ghost" disabled={busy} onClick={() => onApproveClose(issueId)}>
                  {workflow?.busy === "approving_close" ? "审批中…" : "② 批准关闭"}
                </button>
              )}
              {workflow?.closeApprovalId && workflow.closeApproved && (
                <button className="button primary" disabled={busy} onClick={() => onWriteClose(issueId)}>
                  {workflow?.busy === "writing_close" ? "关闭并回读中…" : "③ 写回关闭"}
                </button>
              )}
            </div>
          )}
          {workflow?.closeResult && (
            <div className={`ncr-result ${workflow.closeResult.error ? "error" : "ok"}`}>
              关闭状态：{workflow.closeResult.error ?? workflow.closeResult.status ?? "未知"}
              {workflow.closeResult.read_back_verified ? " · 回读已验证" : ""}
              {workflow.closeResult.idempotent ? " · 幂等命中" : ""}
            </div>
          )}
        </>
      )}
      <small className="ncr-audit-hint">身份：{identity?.actor_id ?? "未解析"} · 来源：{identity?.authority ?? "—"} · 数据：{issue.data_source ?? dataSource}</small>
    </div>
  );
}
