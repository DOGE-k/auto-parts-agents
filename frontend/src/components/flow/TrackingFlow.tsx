// 跟单质量环节面板（步骤 7-8：工单选择 / 跟单 + 质量 NCR + 发运门禁；阶段十 10.5 从 RealBusinessPage 抽出）
import NcrWorkflowCard from "../NcrWorkflowCard";
import type { NcrForm, NcrWorkflow, QualityInfo, Quotation, ShipGateInfo, TrackingInfo, WorkOrder } from "../../types/realBusiness";
import type { RealIdentity, RealQualityIssue } from "../../api";

export function WorkOrderSelectPanel({
  step,
  quotation,
  workOrders,
  workOrderId,
  onSelectWorkOrder,
  loading,
  onRefreshWorkOrders,
  onLoadTracking,
}: {
  step: number;
  quotation: Quotation | null;
  workOrders: WorkOrder[];
  workOrderId: string;
  onSelectWorkOrder: (workOrderId: string) => void;
  loading: boolean;
  onRefreshWorkOrders: () => void;
  onLoadTracking: () => void;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>生产跟单与质量</h2>
          <p>从 OpenMES 读取工单进度、质量记录和发运门禁状态</p>
        </div>
      </div>
      {step === 7 && (
        <div className="form-grid">
          <div className="form-group">
            <label>工单号 (来自 OpenMES)</label>
            <select
              value={workOrderId}
              onChange={(e) => onSelectWorkOrder(e.target.value)}
              disabled={!quotation?.erp_draft_id || workOrders.filter((wo) => wo.customer_order_no?.trim() === quotation.erp_draft_id?.trim()).length === 0}
            >
              <option value="">
                {quotation?.erp_draft_id ? "请选择与当前 ERP 订单正式关联的工单" : "请先创建 ERP 销售订单草稿"}
              </option>
              {workOrders
                .filter((wo) => wo.customer_order_no?.trim() === quotation?.erp_draft_id?.trim())
                .map((wo) => (
                <option key={wo.work_order_id} value={wo.work_order_id}>
                  {wo.work_order_no} · {wo.product_name} ({wo.status})
                </option>
              ))}
            </select>
            {quotation?.erp_draft_id && workOrders.filter((wo) => wo.customer_order_no?.trim() === quotation.erp_draft_id?.trim()).length === 0 && (
              <p className="field-hint">ERP 订单 {quotation.erp_draft_id} 当前没有正式关联的 OpenMES 工单。系统不会用其他订单的工单代替。</p>
            )}
            <button
              className="button ghost"
              onClick={onRefreshWorkOrders}
            >
              ↻ 刷新工单列表（在 OpenMES 建立关联后点击）
            </button>
          </div>
        </div>
      )}
      {step === 7 && (
        <div className="form-actions">
          <button
            className="button primary"
            onClick={onLoadTracking}
            disabled={loading || !workOrderId || !quotation?.erp_draft_id}
          >
            {loading ? "加载中..." : "加载跟单与质量数据 →"}
          </button>
        </div>
      )}
    </section>
  );
}

export function TrackingQualityGatePanels({
  tracking,
  quality,
  shipGate,
  loading,
  workOrderId,
  identity,
  ncrWorkflows,
  todoFocusIssueId,
  onNcrFormChange,
  onRequestNcrDisposition,
  onApproveNcrDisposition,
  onWriteNcrDisposition,
  onCheckNcrClosure,
  onRequestNcrClose,
  onApproveNcrClose,
  onWriteNcrClose,
  onReload,
  onReset,
}: {
  tracking: TrackingInfo;
  quality: QualityInfo;
  shipGate: ShipGateInfo;
  loading: boolean;
  workOrderId: string;
  identity: RealIdentity | null;
  ncrWorkflows: Record<string, NcrWorkflow>;
  todoFocusIssueId: string;
  onNcrFormChange: (issueId: string, field: keyof NcrForm, value: string) => void;
  onRequestNcrDisposition: (issue: RealQualityIssue) => void;
  onApproveNcrDisposition: (issueId: string) => void;
  onWriteNcrDisposition: (issueId: string) => void;
  onCheckNcrClosure: (issueId: string) => void;
  onRequestNcrClose: (issueId: string) => void;
  onApproveNcrClose: (issueId: string) => void;
  onWriteNcrClose: (issueId: string) => void;
  onReload: () => void;
  onReset: () => void;
}) {
  return (
    <>
      <div className="dashboard-grid">
        {/* 跟单 */}
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>跟单 Agent</h2>
              <p>工单生产进度与风险评估</p>
            </div>
            <span className="badge blue-badge">OpenMES</span>
          </div>
          <div className="tracking-info">
            <div className="tracking-row">
              <span>工单号</span>
              <strong>{tracking.work_order_no}</strong>
            </div>
            <div className="tracking-row">
              <span>状态</span>
              <strong>{tracking.status}</strong>
            </div>
            <div className="tracking-row">
              <span>产线</span>
              <strong>{tracking.line_name}</strong>
            </div>
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${tracking.completion_rate}%` }}
              />
            </div>
            <p className="progress-text">
              完成率: {tracking.completion_rate}% ({tracking.completed_qty} /{" "}
              {tracking.quantity})
            </p>
            <div className="tracking-row">
              <span>生产 ETA</span>
              <strong>{tracking.eta ?? "数据不足，未预测"}</strong>
            </div>
            <small className="muted-text">
              {tracking.eta_status === "RATE_BASED"
                ? `依据 OpenMES 实际速率 ${tracking.observed_rate?.units_per_hour ?? "—"} 件/小时`
                : tracking.eta_status === "COMPLETED"
                  ? "工单已完成"
                  : "缺少实际产量与耗时记录，未使用交期或固定天数代替 ETA"}
            </small>
            {tracking.risks.length > 0 && (
              <div className="risk-list">
                <small>风险预警</small>
                {tracking.risks.map((risk, idx) => (
                  <div
                    key={idx}
                    className={`risk-item ${risk.level === "high" ? "high" : "medium"}`}
                  >
                    {risk.message}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* 质量 */}
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>质量文档 Agent</h2>
              <p>质量记录与资料包完整性</p>
            </div>
            <span
              className={`badge ${quality.quality_gate_passed ? "green" : "red-badge"}`}
            >
              {quality.quality_gate_passed ? "质量门禁通过" : "质量门禁未通过"}
            </span>
          </div>
          <div className="quality-info">
            <div className="quality-stat">
              <span className="quality-count">{quality.quality_records.length}</span>
              <small>质量记录</small>
            </div>
            <div className="quality-stat">
              <span className="quality-count open">{quality.open_issues_count}</span>
              <small>未关闭问题</small>
            </div>
            <div className="quality-stat">
              <span className="quality-count missing">{quality.missing_documents.length}</span>
              <small>缺失文档</small>
            </div>
          </div>
          {quality.quality_records.length > 0 && (
            <div className="quality-records">
              {quality.quality_records.slice(0, 3).map((q) => (
                <div key={q.record_id} className="quality-record">
                  <span
                    className={`severity ${q.severity?.toLowerCase()}`}
                  >
                    {q.severity}
                  </span>
                  <div className="quality-detail">
                    <strong>{q.title}</strong>
                    <small>{q.status} · {q.record_type}</small>
                  </div>
                </div>
              ))}
            </div>
          )}
          {quality.quality_records.some((q) => q.record_id) && (
            <div className="ncr-workflows">
              <div className="ncr-workflow-heading">
                <strong>NCR 人工处置与关闭</strong>
                <small>真实 OpenMES 写回必须经过处置方案、审批、写回、回读；关闭还必须通过纠正措施校验。</small>
              </div>
              {quality.quality_records.filter((q) => q.record_id).map((issue) => (
                <NcrWorkflowCard
                  key={String(issue.record_id)}
                  issue={issue}
                  workflow={ncrWorkflows[String(issue.record_id)]}
                  focused={todoFocusIssueId === String(issue.record_id)}
                  identity={identity}
                  dataSource={quality.data_source}
                  onFormChange={onNcrFormChange}
                  onRequestDisposition={onRequestNcrDisposition}
                  onApproveDisposition={onApproveNcrDisposition}
                  onWriteDisposition={onWriteNcrDisposition}
                  onCheckClosure={onCheckNcrClosure}
                  onRequestClose={onRequestNcrClose}
                  onApproveClose={onApproveNcrClose}
                  onWriteClose={onWriteNcrClose}
                />
              ))}
            </div>
          )}
          <p className="gate-reason">{quality.gate_details.reason}</p>
          {quality.unsupported_capabilities?.length > 0 && (
            <div className="unsupported-list">
              <small>当前系统不支持（如实标注，不伪造）</small>
              {quality.unsupported_capabilities.map((u, idx) => (
                <div key={idx} className="unsupported-item">
                  <strong>{u.capability}</strong>
                  <small>{u.reason}</small>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* 发运门禁 */}
      <section className="panel gate-panel">
        <div className="panel-heading">
          <div>
            <h2>发运门禁</h2>
            <p>三重门禁必须全部通过才能发运</p>
          </div>
          <span
            className={`badge ${shipGate.can_ship ? "green" : "red-badge"}`}
          >
            {shipGate.can_ship ? "可以发运" : "禁止发运"}
          </span>
        </div>
        <div className="gate-checks">
          <div className={`gate-row ${shipGate.quotation_approved ? "pass" : "block"}`}>
            <span className="gate-icon">{shipGate.quotation_approved ? "✓" : "✗"}</span>
            <div>
              <strong>报价审批</strong>
              <small>{shipGate.quotation_approved ? "已通过" : "未审批"}</small>
            </div>
          </div>
          <div className={`gate-row ${shipGate.quality_gate_passed ? "pass" : "block"}`}>
            <span className="gate-icon">{shipGate.quality_gate_passed ? "✓" : "✗"}</span>
            <div>
              <strong>质量门禁</strong>
              <small>{shipGate.quality_gate_passed ? "已通过" : "未通过"}</small>
            </div>
          </div>
          <div className={`gate-row ${shipGate.production_ready ? "pass" : "block"}`}>
            <span className="gate-icon">{shipGate.production_ready ? "✓" : "✗"}</span>
            <div>
              <strong>生产进度</strong>
              <small>
                {shipGate.production_ready ? "≥90% 可发运" : `${shipGate.completion_rate}% < 90%`}
              </small>
            </div>
          </div>
        </div>
        {shipGate.blocking_reasons.length > 0 && (
          <div className="blocking-reasons">
            <small>阻塞原因</small>
            <ul>
              {shipGate.blocking_reasons.map((reason, idx) => (
                <li key={idx}>{reason}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <div className="form-actions">
        <button className="button ghost" onClick={onReset}>
          ← 重新开始
        </button>
        <button
          className="button primary"
          onClick={onReload}
          disabled={loading || !workOrderId}
        >
          {loading ? "加载中..." : "↻ 重新加载跟单与门禁（MES 进度更新后点击）"}
        </button>
      </div>
    </>
  );
}
