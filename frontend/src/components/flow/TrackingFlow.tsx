// 跟单质量环节面板（步骤 7-8：工单选择 / 跟单 + 质量 NCR + 发运门禁；阶段十 10.5 从 RealBusinessPage 抽出）
import { useEffect, useState } from "react";
import NcrWorkflowCard from "../NcrWorkflowCard";
import type { NcrForm, NcrWorkflow, QualityInfo, Quotation, ShipGateInfo, TrackingInfo, WorkOrder } from "../../types/realBusiness";
import type { RealIdentity, RealQualityIssue, WorkOrderDispatchPlan, WorkOrderDispatchResult } from "../../api";
import {
  approveProductionReport,
  executeProductionReport,
  requestProductionReport,
  type ProductionReportPlan,
  type ProductionReportResult,
} from "../../api";
import {
  approveIssueRegistration,
  executeIssueRegistration,
  listIssueTypes,
  requestIssueRegistration,
  type IssueRegistrationPlan,
  type IssueRegistrationResult,
  type IssueTypeInfo,
} from "../../api";

// 工单下达流程状态（主组件持有，面板展示；三步审批一次确认、过程透明）
export type DispatchFlowState = {
  stage: "confirm" | "executing" | "done";
  approvalId?: string;
  plan?: WorkOrderDispatchPlan;
  result?: WorkOrderDispatchResult;
};

// 真实报工流程状态（面板自包含；①建审批→②批准→③官方报工链路，过程留痕）
type ReportFlowState = {
  stage: "confirm" | "executing" | "done";
  approvalId?: string;
  plan?: ProductionReportPlan;
  result?: ProductionReportResult;
};

// 质量问题登记流程状态（面板自包含；登记成功后立即出现在 NCR 列表可处置）
type IssueRegFlowState = {
  stage: "confirm" | "executing" | "done";
  approvalId?: string;
  plan?: IssueRegistrationPlan;
  result?: IssueRegistrationResult;
};

export function WorkOrderSelectPanel({
  step,
  quotation,
  workOrders,
  workOrderId,
  onSelectWorkOrder,
  loading,
  onRefreshWorkOrders,
  onLoadTracking,
  dispatchFlow,
  onOpenDispatch,
  onConfirmDispatch,
  onDismissDispatch,
}: {
  step: number;
  quotation: Quotation | null;
  workOrders: WorkOrder[];
  workOrderId: string;
  onSelectWorkOrder: (workOrderId: string) => void;
  loading: boolean;
  onRefreshWorkOrders: () => void;
  onLoadTracking: () => void;
  dispatchFlow: DispatchFlowState | null;
  onOpenDispatch: () => void;
  onConfirmDispatch: () => void;
  onDismissDispatch: () => void;
}) {
  const noLinkedOrder = Boolean(
    quotation?.erp_draft_id
    && workOrders.filter((wo) => wo.customer_order_no?.trim() === quotation?.erp_draft_id?.trim()).length === 0,
  );
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
            {noLinkedOrder && (
              <p className="field-hint">ERP 订单 {quotation?.erp_draft_id} 当前没有正式关联的 OpenMES 工单。系统不会用其他订单的工单代替。</p>
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
      {/* 工单下达（阶段十通用链路补强）：任意新订单不再依赖人工在 OpenMES 手工建单 */}
      {step === 7 && noLinkedOrder && (
        <div className="dispatch-block">
          {!dispatchFlow && (
            <>
              <small>没有关联工单？可以在审批门禁内向 OpenMES 下达工单（真实创建 + customer_order_no 自动关联 + 回读验证）</small>
              <button className="button primary" onClick={onOpenDispatch} disabled={loading}>
                下达工单到 OpenMES（需人工审批）→
              </button>
            </>
          )}
          {dispatchFlow?.stage === "confirm" && (
            <div className="dispatch-confirm">
              <small>将执行（真实写入 OpenMES，需逐级审批）：</small>
              <code>① 建立"工单下达"审批（记录将创建的工单内容，不写入）</code>
              <code>② 人工批准该审批（审批人 = 当前登录身份）</code>
              <code>③ 按审批创建 OpenMES 工单（order_no 由 ERP 订单号派生，customer_order_no 精确关联）→ 回读验证</code>
              <div className="proposal-confirm-row">
                <button className="button primary" onClick={onConfirmDispatch} disabled={loading}>
                  确认下达（①→②→③ 自动执行，过程留痕）
                </button>
                <button className="button ghost" onClick={onDismissDispatch}>取消</button>
              </div>
            </div>
          )}
          {dispatchFlow?.stage === "executing" && (
            <div className="dispatch-executing">
              执行中：① 建立审批{dispatchFlow.approvalId ? `（${dispatchFlow.approvalId}）✓` : "…"} → ② 人工批准 → ③ 创建工单 + 回读…
            </div>
          )}
          {dispatchFlow?.stage === "done" && dispatchFlow.result && (
            dispatchFlow.result.success ? (
              <div className={`dispatch-result ${dispatchFlow.result.idempotent ? "" : "ok"}`}>
                ✓ 工单已下达：{dispatchFlow.result.work_order?.order_no ?? "—"}
                （id {dispatchFlow.result.work_order_id ?? "—"} · customer_order_no 关联已回读验证
                {dispatchFlow.result.idempotent ? " · 幂等命中既有工单" : ""}）
                请从上方工单下拉选择该工单继续。
              </div>
            ) : (
              <div className="dispatch-result error">
                工单下达失败：{dispatchFlow.result.error}
              </div>
            )
          )}
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
  // 真实报工（通用链路补强）：面板自包含状态；审批门禁内走 OpenMES 官方报工链路
  const [reportQty, setReportQty] = useState("");
  const [reportElapsed, setReportElapsed] = useState("");
  const [reportLot, setReportLot] = useState("");
  const [reportFlow, setReportFlow] = useState<ReportFlowState | null>(null);

  const openProductionReport = async () => {
    const qty = Number(reportQty);
    const elapsed = Number(reportElapsed);
    if (!workOrderId || !Number.isFinite(qty) || qty <= 0 || !Number.isInteger(elapsed) || elapsed <= 0) {
      setReportFlow({ stage: "done", result: { success: false, error: "请填写正数的本批数量和正整数分钟的实际耗时" } });
      return;
    }
    try {
      const requested = await requestProductionReport(
        workOrderId,
        {
          target_qty: qty,
          actual_elapsed_minutes: elapsed,
          ...(reportLot.trim() ? { lot_number: reportLot.trim() } : {}),
        },
        identity?.actor_id,
      );
      if (!requested.success || !requested.approval) throw new Error(requested.error ?? "建立报工审批失败");
      setReportFlow({ stage: "confirm", approvalId: requested.approval.approval_id, plan: requested.report_plan });
    } catch (e) {
      setReportFlow({ stage: "done", result: { success: false, error: e instanceof Error ? e.message : "建立报工审批失败" } });
    }
  };

  const confirmProductionReport = async () => {
    if (!reportFlow?.approvalId) return;
    setReportFlow((prev) => (prev ? { ...prev, stage: "executing" } : prev));
    try {
      const approved = await approveProductionReport(reportFlow.approvalId, identity?.actor_id);
      if (!approved.success) throw new Error(approved.error ?? "报工审批失败");
      const executed = await executeProductionReport(workOrderId, reportFlow.approvalId, identity?.actor_id);
      if (!executed.success) throw new Error(executed.error ?? "报工失败");
      setReportFlow({ stage: "done", result: executed });
      onReload();
    } catch (e) {
      setReportFlow((prev) => (prev ? { ...prev, stage: "done", result: { success: false, error: e instanceof Error ? e.message : "报工失败" } } : prev));
    }
  };

  const resetReportFlow = () => {
    setReportFlow(null);
    setReportQty("");
    setReportElapsed("");
    setReportLot("");
  };

  // 质量问题登记（通用链路补强）：审批门禁内创建真实 OpenMES NCR
  const [issueTypes, setIssueTypes] = useState<IssueTypeInfo[]>([]);
  const [issueTypesError, setIssueTypesError] = useState("");
  const [issueTypeId, setIssueTypeId] = useState("");
  const [issueTitle, setIssueTitle] = useState("");
  const [issueDesc, setIssueDesc] = useState("");
  const [issueFlow, setIssueFlow] = useState<IssueRegFlowState | null>(null);

  const loadIssueTypes = () => {
    setIssueTypesError("");
    listIssueTypes()
      .then((types) => setIssueTypes(types))
      .catch((e) => setIssueTypesError(e instanceof Error ? e.message : "问题类型加载失败"));
  };
  useEffect(() => {
    loadIssueTypes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openIssueRegistration = async () => {
    if (!workOrderId) return;
    if (!issueTypeId || !issueTitle.trim()) {
      setIssueFlow({ stage: "done", result: { success: false, error: "请选择问题类型并填写问题标题" } });
      return;
    }
    try {
      const requested = await requestIssueRegistration(
        workOrderId,
        Number(issueTypeId),
        issueTitle.trim(),
        issueDesc.trim() || undefined,
        identity?.actor_id,
      );
      if (!requested.success || !requested.approval) throw new Error(requested.error ?? "建立质量问题登记审批失败");
      setIssueFlow({ stage: "confirm", approvalId: requested.approval.approval_id, plan: requested.issue_plan });
    } catch (e) {
      setIssueFlow({ stage: "done", result: { success: false, error: e instanceof Error ? e.message : "建立质量问题登记审批失败" } });
    }
  };

  const confirmIssueRegistration = async () => {
    if (!issueFlow?.approvalId) return;
    setIssueFlow((prev) => (prev ? { ...prev, stage: "executing" } : prev));
    try {
      const approved = await approveIssueRegistration(issueFlow.approvalId, identity?.actor_id);
      if (!approved.success) throw new Error(approved.error ?? "质量问题登记审批失败");
      const executed = await executeIssueRegistration(workOrderId, issueFlow.approvalId, identity?.actor_id);
      if (!executed.success) throw new Error(executed.error ?? "质量问题登记失败");
      setIssueFlow({ stage: "done", result: executed });
      onReload();
    } catch (e) {
      setIssueFlow((prev) => (prev ? { ...prev, stage: "done", result: { success: false, error: e instanceof Error ? e.message : "质量问题登记失败" } } : prev));
    }
  };

  const resetIssueFlow = () => {
    setIssueFlow(null);
    setIssueTypeId("");
    setIssueTitle("");
    setIssueDesc("");
  };

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
          {/* 真实报工（通用链路补强）：审批门禁内调用 OpenMES 官方报工链路，现场产生速率数据 */}
          <div className="dispatch-block">
            {!reportFlow && (
              <>
                <small>没有速率数据？可在审批门禁内对当前工单真实报工（建批次 → 开工 → 完工，OpenMES 官方 API），报工后 ETA 自动按真实速率计算。</small>
                <div className="proposal-confirm-row">
                  <input
                    type="number"
                    min="1"
                    placeholder="本批数量"
                    value={reportQty}
                    onChange={(e) => setReportQty(e.target.value)}
                  />
                  <input
                    type="number"
                    min="1"
                    placeholder="实际耗时(分钟)"
                    value={reportElapsed}
                    onChange={(e) => setReportElapsed(e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="批次号(留空自动生成)"
                    value={reportLot}
                    onChange={(e) => setReportLot(e.target.value)}
                  />
                </div>
                <button className="button primary" onClick={() => void openProductionReport()}>
                  报工（需人工审批）→
                </button>
              </>
            )}
            {reportFlow?.stage === "confirm" && reportFlow.plan && (
              <div className="dispatch-confirm">
                <small>将执行（真实写入 OpenMES，需逐级审批）：</small>
                <code>① 建立"真实报工"审批（批次 {reportFlow.plan.lot_number} · 数量 {reportFlow.plan.batch_target_qty} · 实际耗时 {reportFlow.plan.actual_elapsed_minutes} 分钟，不写入）</code>
                <code>② 人工批准该审批（审批人 = 当前登录身份）</code>
                <code>③ 按审批走官方报工链路：建批次 → 开工 → 完工（produced_qty + actual_elapsed_minutes）→ 回读验证</code>
                <div className="proposal-confirm-row">
                  <button className="button primary" onClick={() => void confirmProductionReport()} disabled={loading}>
                    确认报工（①→②→③ 自动执行，过程留痕）
                  </button>
                  <button className="button ghost" onClick={resetReportFlow}>取消</button>
                </div>
              </div>
            )}
            {reportFlow?.stage === "executing" && (
              <div className="dispatch-executing">
                执行中：① 建立审批{reportFlow.approvalId ? `（${reportFlow.approvalId}）✓` : "…"} → ② 人工批准 → ③ 建批次 → 开工 → 完工 → 回读…
              </div>
            )}
            {reportFlow?.stage === "done" && reportFlow.result && (
              reportFlow.result.success ? (
                <div className={`dispatch-result ${reportFlow.result.idempotent ? "" : "ok"}`}>
                  ✓ 报工{reportFlow.result.idempotent ? "幂等命中：该批次号已报工，未重复写入" : "成功（回读已验证）"}
                  ：批次 {reportFlow.result.batch_id ?? "—"}
                  {reportFlow.result.work_order_produced_qty != null && ` · 工单累计产量 ${reportFlow.result.work_order_produced_qty}`}
                  。进度与 ETA 已刷新。
                  <button className="button ghost" onClick={resetReportFlow}>再报一批</button>
                </div>
              ) : (
                <div className="dispatch-result error">
                  报工失败：{reportFlow.result.error}
                  <button className="button ghost" onClick={resetReportFlow}>返回修改</button>
                </div>
              )
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
          {/* 质量问题登记（通用链路补强）：审批门禁内创建真实 OpenMES NCR，登记后立即出现在上方列表可处置 */}
          <div className="dispatch-block">
            {!issueFlow && (
              <>
                <small>现场发现质量问题？在审批门禁内登记为真实 OpenMES NCR（登记后立即出现在上方 NCR 列表，可直接走处置/关闭闭环）。</small>
                <div className="proposal-confirm-row">
                  <select value={issueTypeId} onChange={(e) => setIssueTypeId(e.target.value)}>
                    <option value="">选择问题类型（OpenMES 真实类型）</option>
                    {issueTypes.map((t) => (
                      <option key={t.id} value={t.id}>{t.id} · {t.name}（{t.severity ?? "—"}）</option>
                    ))}
                  </select>
                  <input
                    type="text"
                    placeholder="问题标题"
                    value={issueTitle}
                    onChange={(e) => setIssueTitle(e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="描述（可选）"
                    value={issueDesc}
                    onChange={(e) => setIssueDesc(e.target.value)}
                  />
                </div>
                <button className="button primary" onClick={() => void openIssueRegistration()}>
                  登记质量问题（需人工审批）→
                </button>
                {issueTypesError && (
                  <div className="dispatch-result error">
                    问题类型加载失败：{issueTypesError}
                    <button className="button ghost" onClick={loadIssueTypes}>重试</button>
                  </div>
                )}
              </>
            )}
            {issueFlow?.stage === "confirm" && issueFlow.plan && (
              <div className="dispatch-confirm">
                <small>将执行（真实写入 OpenMES，需逐级审批）：</small>
                <code>① 建立"质量问题登记"审批（{issueFlow.plan.work_order_no} · 类型 {issueFlow.plan.issue_type_name}（{issueFlow.plan.severity}）· "{issueFlow.plan.title}"，不写入）</code>
                <code>② 人工批准该审批（审批人 = 当前登录身份）</code>
                <code>③ 按审批创建 OpenMES 质量问题 → 回读验证（同工单同标题未关闭问题幂等）</code>
                <div className="proposal-confirm-row">
                  <button className="button primary" onClick={() => void confirmIssueRegistration()} disabled={loading}>
                    确认登记（①→②→③ 自动执行，过程留痕）
                  </button>
                  <button className="button ghost" onClick={resetIssueFlow}>取消</button>
                </div>
              </div>
            )}
            {issueFlow?.stage === "executing" && (
              <div className="dispatch-executing">
                执行中：① 建立审批{issueFlow.approvalId ? `（${issueFlow.approvalId}）✓` : "…"} → ② 人工批准 → ③ 创建质量问题 → 回读…
              </div>
            )}
            {issueFlow?.stage === "done" && issueFlow.result && (
              issueFlow.result.success ? (
                <div className={`dispatch-result ${issueFlow.result.idempotent ? "" : "ok"}`}>
                  ✓ 质量问题{issueFlow.result.idempotent ? "幂等命中：同工单同标题的未关闭问题已存在，未重复登记" : "已登记（回读已验证）"}
                  ：issue id {issueFlow.result.issue_id ?? "—"}。质量数据已刷新，上方 NCR 列表可直接处置。
                  <button className="button ghost" onClick={resetIssueFlow}>再登记一条</button>
                </div>
              ) : (
                <div className="dispatch-result error">
                  质量问题登记失败：{issueFlow.result.error}
                  <button className="button ghost" onClick={resetIssueFlow}>返回修改</button>
                </div>
              )
            )}
          </div>
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
