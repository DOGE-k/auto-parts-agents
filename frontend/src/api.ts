import { SseParser } from "./lib/sse";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:9001/api";
const REAL_SESSION_TOKEN_KEY = "real_business_session_token";
const REAL_SESSION_ISSUED_KEY = "real_business_session_issued_at";
const REAL_WRITE_TOKEN_KEY = "real_business_write_token";

/**
 * Keep short lived runtime credentials in sessionStorage only. They are never
 * written to project files, query strings, agent run payloads, or logs.
 */
export function getRealSessionToken(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(REAL_SESSION_TOKEN_KEY) ?? "";
}

export function setRealSessionToken(token: string): void {
  if (typeof window === "undefined") return;
  const value = token.trim();
  if (value) {
    window.sessionStorage.setItem(REAL_SESSION_TOKEN_KEY, value);
    // OpenMES Sanctum token 默认 15 分钟 TTL；记录签发时间供前端过期提醒
    window.sessionStorage.setItem(REAL_SESSION_ISSUED_KEY, String(Date.now()));
  } else {
    window.sessionStorage.removeItem(REAL_SESSION_TOKEN_KEY);
    window.sessionStorage.removeItem(REAL_SESSION_ISSUED_KEY);
  }
}

export function getSessionIssuedAt(): number {
  if (typeof window === "undefined") return 0;
  return Number(window.sessionStorage.getItem(REAL_SESSION_ISSUED_KEY) || 0);
}

export function getRealWriteToken(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(REAL_WRITE_TOKEN_KEY) ?? "";
}

export function setRealWriteToken(token: string): void {
  if (typeof window === "undefined") return;
  const value = token.trim();
  if (value) window.sessionStorage.setItem(REAL_WRITE_TOKEN_KEY, value);
  else window.sessionStorage.removeItem(REAL_WRITE_TOKEN_KEY);
}

export type Project = {
  project_id: string;
  correlation_id: string;
  customer_id: string;
  product_id: string;
  current_version: number;
  lifecycle_state: string;
  data_source: string;
  scenario: string;
};
export type Case = {
  case_id: string;
  project_id: string;
  agent_type: string;
  operational_state: string;
  business_state: string;
  version: number;
  objective: string;
};

export type Approval = {
  approval_id: string;
  project_id: string;
  case_id: string;
  action_type: string;
  object_id: string;
  object_version: number;
  snapshot_hash: string;
  rule_version: string;
  status: string;
  action_payload_json: Record<string, any>;
  created_at: string;
};

export type Event = {
  event_id: string;
  event_type: string;
  project_id: string;
  correlation_id: string;
  causation_id?: string | null;
  source_agent?: string | null;
  target_agent?: string | null;
  object_id: string;
  object_version: number;
  occurred_at: string;
  payload: Record<string, any>;
  evidence_ids: string[];
};

export type ProductionCompletion = Record<string, any>;

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const sessionToken = getRealSessionToken();
  if (sessionToken && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${sessionToken}`);
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    // detail 可能是字符串，也可能是 {code, message} 结构化错误
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ?? `请求失败 (${response.status})`;
    throw new Error(message);
  }
  return data as T;
}

export function writeHeaders(): HeadersInit {
  const headers: Record<string, string> = { "Idempotency-Key": crypto.randomUUID() };
  const writeToken = getRealWriteToken();
  if (writeToken) headers["X-Real-Write-Token"] = writeToken;
  return headers;
}

export type RealQualityIssue = {
  record_id?: string;
  record_type?: string;
  issue_code?: string;
  work_order_id?: string;
  work_order_no?: string;
  title?: string;
  description?: string;
  severity?: string;
  status?: string;
  disposition?: string;
  non_conforming_qty?: string | number | null;
  nc_source?: string;
  root_cause?: string;
  containment_action?: string;
  reported_at?: string;
  resolved_at?: string;
  authority?: string;
  data_source?: string;
};

export type QualityApproval = {
  approval_id: string;
  approved?: boolean;
  approved_by?: string;
  reference_id?: string;
  reference_type?: string;
  notes?: string;
  created_at?: string;
};

export type QualityClosureCheck = {
  status: string;
  work_order_id: string;
  issue_id: string;
  issue?: RealQualityIssue;
  actions?: { action_id?: string; title?: string; status?: string; due_date?: string; verified_at?: string }[];
  checks?: Record<string, boolean>;
  closure_ready: boolean;
  data_gap?: string | null;
  authority?: string;
  data_source?: string;
};

export type QualityWorkflowResult = {
  success: boolean;
  status?: string;
  written?: boolean;
  idempotent?: boolean;
  approval?: QualityApproval;
  read_back?: RealQualityIssue;
  read_back_verified?: boolean;
  closure_check?: QualityClosureCheck;
  error?: string;
  authority?: string;
  data_source?: string;
};

export async function requestQualityIssueDisposition(issueId: string, payload: {
  work_order_id: string;
  disposition: string;
  non_conforming_qty?: number;
  root_cause: string;
  containment_action: string;
  nc_source?: string;
}): Promise<QualityWorkflowResult> {
  return api<QualityWorkflowResult>(`/real-orders/quality/issues/${encodeURIComponent(issueId)}/disposition-request`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  });
}

export async function approveQualityIssueDisposition(approvalId: string): Promise<QualityWorkflowResult> {
  return api<QualityWorkflowResult>(`/real-orders/quality/disposition-approvals/${encodeURIComponent(approvalId)}/approve`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({}),
  });
}

export async function writeQualityIssueDisposition(issueId: string, workOrderId: string, approvalId: string): Promise<QualityWorkflowResult> {
  return api<QualityWorkflowResult>(`/real-orders/quality/issues/${encodeURIComponent(issueId)}/disposition`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ work_order_id: workOrderId, approval_id: approvalId }),
  });
}

export async function checkQualityIssueClosure(issueId: string, workOrderId: string): Promise<QualityClosureCheck> {
  return api<QualityClosureCheck>(`/real-orders/quality/issues/${encodeURIComponent(issueId)}/closure-check?work_order_id=${encodeURIComponent(workOrderId)}`);
}

export async function requestQualityIssueClose(issueId: string, workOrderId: string): Promise<QualityWorkflowResult> {
  return api<QualityWorkflowResult>(`/real-orders/quality/issues/${encodeURIComponent(issueId)}/close-request`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ work_order_id: workOrderId }),
  });
}

export async function approveQualityIssueClose(approvalId: string): Promise<QualityWorkflowResult> {
  return api<QualityWorkflowResult>(`/real-orders/quality/close-approvals/${encodeURIComponent(approvalId)}/approve`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({}),
  });
}

export async function writeQualityIssueClose(issueId: string, workOrderId: string, approvalId: string): Promise<QualityWorkflowResult> {
  return api<QualityWorkflowResult>(`/real-orders/quality/issues/${encodeURIComponent(issueId)}/close`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ work_order_id: workOrderId, approval_id: approvalId }),
  });
}

export const eventNames: Record<string, string> = {
  RFQ_CREATED: "收到询价单",
  QUOTE_NEEDS_INFO: "报价需要补充资料",
  QUOTE_DRAFT_READY: "报价草稿已生成",
  QUOTE_APPROVED: "报价已批准",
  SALES_ORDER_RELEASED: "销售订单已发布",
  MATERIAL_DEMAND_CREATED: "正式物料需求已发布",
  MATERIAL_SHORTAGE: "发现物料缺口",
  SUPPLY_PLAN_READY: "供应方案已就绪",
  COST_ASSESSMENT_READY: "订单期成本评估已完成",
  PO_DRAFT_READY: "采购订单草稿已生成",
  SUPPLIER_DELAYED: "供应商交期晚于物料需求日",
  IQC_REQUESTED: "已请求来料检验",
  QUALITY_HOLD: "质量冻结",
  NCR_CREATED: "不合格报告已创建",
  QUALITY_RELEASED: "质量已放行（MockMES 权威事件）",
  DOCUMENT_PACKAGE_APPROVED: "资料包已批准",
  DELIVERY_RISK: "发现交付风险",
  ETA_RECALCULATED: "ETA 已重算",
  EXPEDITE_REQUESTED: "收到加急请求",
  EXPEDITE_OPTIONS_READY: "加急方案已就绪",
  SHIPMENT_APPROVAL_REQUIRED: "发运等待审批",
  SHIPMENT_RELEASED: "发运已批准",
  DELIVERED: "已签收",
};

export const agentNames: Record<string, string> = {
  quotation: "报价智能体",
  procurement: "采购智能体",
  tracking: "跟单智能体",
  quality_document: "质量文档智能体",
};

// 真实 Agent 运行记录（real_agent_runs 表，服务重启后仍在）
export type AgentRunSummary = {
  run_id: string;
  agent_type: string;
  operation: string;
  result_status: string;
  result_summary: string;
  input: { args: unknown[]; kwargs: Record<string, unknown> };
  started_at: string | null;
  finished_at: string | null;
};

export type AgentRunEvidenceItem = {
  source?: string;
  record_type?: string;
  record_id?: string;
  summary?: string;
};

export type AgentRunDetail = AgentRunSummary & {
  result: Record<string, any> | null;
  error: { type: string; message: string } | null;
};

export const agentRunTypeNames: Record<string, string> = {
  coordinator: "协调智能体",
  quotation: "报价 Agent",
  procurement: "采购 Agent",
  tracking: "跟单 Agent",
  quality: "质量文档 Agent",
  shipping: "发运门禁",
  approval: "人工审批",
  erp_write: "ERP 草稿写入",
  linkage: "ERP↔MES 关联",
};

// 协调智能体（动态协同问答）
export type AssistantCallStep = {
  seq: number;
  caller: string;
  callee: string;
  skill_id: string;
  arguments: Record<string, unknown>;
  result_summary: string;
  status: string;
  elapsed_ms: number;
};

export type AssistantAnswer = {
  question: string;
  answer: string;
  call_chain: AssistantCallStep[];
  rounds: number;
  tool_count: number;
  coordination_run_id: string;
  authority: string;
  context: Record<string, unknown>;
  proposal_options?: ProposalOptions | null;
};

// 方案化协同（阶段六）：真实工具结果原样汇集，前端渲染方案对比卡片
export type ProposalShortageItem = {
  item_id: string;
  item_name?: string;
  net_requirement?: string;
  unit_price?: string;
  price_status?: string;
  suppliers?: string[];
};

export type ProposalSupplierOption = {
  option_id: string;
  supplier_id: string;
  supplier_name: string;
  lead_time_days: number | null;
  covers_all_shortage_items: boolean;
  coverage: string;
  total_cost: string;
  total_cost_complete: boolean;
  currency: string;
  is_recommended: boolean;
  recommendation_reason: string;
};

export type ProposalCostAssessment = {
  option_id: string;
  supplier_name: string;
  covers_all_shortage_items?: boolean;
  revenue: string;
  currency: string;
  material_cost_baseline: string;
  material_cost_with_option: string;
  material_cost_delta: string;
  per_unit_surcharge: string;
  material_margin_before: string;
  material_margin_after: string;
  calculation_basis: string;
};

export type ProposalDeliveryAssessment = {
  work_order_id: string;
  work_order_no: string;
  due_date: string;
  material_ready_date: string;
  buffer_days: number;
  verdict: string;
  conclusion: string;
};

export type ProposalCombination = {
  plan_id: string;
  option_ids: string[];
  suppliers: string[];
  combined_cost: string;
  currency: string;
  coverage: { covered_items: string[]; uncovered_items: string[]; shortage_total: number; complete: boolean };
  overlapping_items: string[];
  max_lead_time_days: number | null;
  warnings: string[];
};

export type QualityImpactResult = {
  work_order_id: string;
  work_order_no: string;
  quality_records: { record_id?: string; title?: string; severity?: string; status?: string; record_type?: string; reported_at?: string }[];
  open_issues_count: number;
  open_records: { record_id?: string; title?: string; severity?: string; status?: string }[];
  batches: { batch_id: string; lot_number: string; target_qty: string; status: string }[];
  quality_gate_passed: boolean;
  missing_documents: string[];
  production: { completion_rate: number; status: string; due_date: string };
  impact_conclusions: string[];
  handling_options: { option: string; how: string; requires_human_confirmation: boolean }[];
  data_gaps: { field: string; detail: string }[];
};

export type RealIdentity = {
  subject: string;
  actor_id: string;
  display_name: string;
  roles: string[];
  authority: string;
  provider: string;
  authenticated: boolean;
};

export async function getRealIdentity(): Promise<RealIdentity> {
  return api<RealIdentity>("/real-orders/identity/me");
}

export type QualityTodoItem = {
  issue_id: string;
  work_order_id: string;
  work_order_no: string;
  title: string;
  severity: string;
  status: string;
  disposition: string;
  reported_at: string;
  assigned_to: string;
  reported_days?: number | null;
  authority?: string;
  data_source?: string;
};

export type QualityTodoList = {
  items: QualityTodoItem[];
  authority: string;
  data_source: string;
  authenticated_identity?: RealIdentity;
};

export async function getQualityTodo(): Promise<QualityTodoList> {
  return api<QualityTodoList>("/real-orders/quality/todo");
}

export type QualityWorkflowState = {
  approval_id: string;
  approved: boolean;
  approved_by: string;
  created_at?: string | null;
  work_order_id: string;
  disposition: string;
};

export type QualityWorkflowStates = {
  states: Record<string, { disposition?: QualityWorkflowState; close?: QualityWorkflowState }>;
  authority: string;
  data_source: string;
};

export async function getQualityWorkflowStates(): Promise<QualityWorkflowStates> {
  return api<QualityWorkflowStates>("/real-orders/quality/workflow-states");
}

export type RealLoginResult = {
  provider: string;
  token_type: string;
  access_token: string;
  force_password_change: boolean;
  session_scope?: string;
  expires_hint?: string;
  identity: RealIdentity;
  authority?: string;
  data_source?: string;
};

export async function loginRealSession(username: string, password: string): Promise<RealLoginResult> {
  return api<RealLoginResult>("/real-orders/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export type ProposalOptions = {
  plan_id?: string;
  quotation_id?: string;
  quotation_status?: string;
  shortage?: {
    finished_item: string;
    shortage_count: number;
    shortage_items: ProposalShortageItem[];
  };
  supplier_options?: ProposalSupplierOption[];
  recommendation?: string;
  cost_assessments?: ProposalCostAssessment[];
  delivery_assessments?: ProposalDeliveryAssessment[];
  combination_assessments?: ProposalCombination[];
  quality_impacts?: QualityImpactResult[];
  data_missing?: { source_skill: string; missing_fields: { field: string; detail: string }[]; need: string }[];
};

// 方案卡片执行闭环（阶段七）：批准方案 → 起草 PO → 回读
export type PoDraftResult = {
  draft?: { draft_id?: string; read_back_verified?: boolean; supplier?: string; schedule_date?: string } | null;
};

export async function approveProcurementPlan(
  planId: string,
  optionId: string,
  approvedBy: string,
): Promise<{ success: boolean; status: string; plan_id: string; approval_id: string; selected_option_id: string }> {
  return api<{ success: boolean; status: string; plan_id: string; approval_id: string; selected_option_id: string }>(`/real-orders/procurement/plans/${encodeURIComponent(planId)}/approve`, {
    method: "POST",
    body: JSON.stringify({
      option_id: optionId,
      approved: true,
      approved_by: approvedBy,
      notes: "问答方案卡片人工确认（阶段七执行闭环）",
    }),
  });
}

export type QuotationApprovalResult = { approval_id: string; approved_by: string; quotation_id: string };

export async function approveQuotation(
  quotationId: string,
  approvedBy: string,
): Promise<QuotationApprovalResult> {
  return api<QuotationApprovalResult>(`/real-orders/quotations/${encodeURIComponent(quotationId)}/approve`, {
    method: "POST",
    body: JSON.stringify({
      approved: true,
      approved_by: approvedBy,
      notes: "问答方案卡片双审批线（阶段八审批一致性）",
    }),
  });
}

export async function createPoFromPlan(
  planId: string,
  approvalId: string,
  approvedBy: string,
): Promise<PoDraftResult> {
  return api<PoDraftResult>("/real-orders/erp/draft/po-from-plan", {
    method: "POST",
    body: JSON.stringify({ plan_id: planId, approval_id: approvalId, approved_by: approvedBy }),
  });
}

// ==================== 工单下达（ERP 草稿 → OpenMES 工单，三步审批门禁） ====================

export type WorkOrderDispatchPlan = {
  quotation_id: string;
  erp_draft_id: string;
  order_no: string;
  customer_order_no: string;
  product_id: string;
  planned_qty: number | string;
  due_date: string;
  customer_name: string;
};

export type WorkOrderDispatchResult = {
  success: boolean;
  status?: string;
  written?: boolean;
  idempotent?: boolean;
  work_order?: Record<string, any>;
  work_order_id?: string | number;
  read_back_verified?: boolean;
  approval?: { approval_id: string; approved_by: string; approved: boolean };
  dispatch_plan?: WorkOrderDispatchPlan;
  error?: string;
};

export async function requestWorkOrderDispatch(
  quotationId: string,
  requestedBy?: string,
): Promise<WorkOrderDispatchResult> {
  return api<WorkOrderDispatchResult>("/real-orders/work-orders/dispatch-request", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ quotation_id: quotationId, requested_by: requestedBy }),
  });
}

export async function approveWorkOrderDispatch(
  approvalId: string,
  approvedBy?: string,
): Promise<WorkOrderDispatchResult> {
  return api<WorkOrderDispatchResult>(
    "/real-orders/work-orders/dispatch-approvals/" + encodeURIComponent(approvalId) + "/approve",
    {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ approved_by: approvedBy }),
    },
  );
}

export async function dispatchWorkOrder(
  quotationId: string,
  approvalId: string,
  approvedBy?: string,
): Promise<WorkOrderDispatchResult> {
  return api<WorkOrderDispatchResult>("/real-orders/work-orders/dispatch", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ quotation_id: quotationId, approval_id: approvalId, approved_by: approvedBy }),
  });
}

// ==================== 真实报工（OpenMES 官方报工链路，三步审批门禁） ====================

export type ProductionReportPlan = {
  work_order_id: string;
  work_order_no: string;
  customer_order_no: string;
  planned_qty?: number | string;
  batch_target_qty: string;
  produced_qty: string;
  actual_elapsed_minutes: number;
  actual_setup_minutes?: number | null;
  actual_run_minutes?: number | null;
  lot_number: string;
};

export type ProductionReportResult = {
  success: boolean;
  status?: string;
  written?: boolean;
  idempotent?: boolean;
  batch?: Record<string, any>;
  batch_id?: string;
  batch_step_id?: string;
  work_order_produced_qty?: number | string;
  read_back_verified?: boolean;
  approval?: { approval_id: string; approved_by: string; approved: boolean };
  report_plan?: ProductionReportPlan;
  error?: string;
};

export async function requestProductionReport(
  workOrderId: string,
  payload: { target_qty: number; actual_elapsed_minutes: number; lot_number?: string },
  requestedBy?: string,
): Promise<ProductionReportResult> {
  return api<ProductionReportResult>("/real-orders/production-reports/request", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ work_order_id: workOrderId, ...payload, requested_by: requestedBy }),
  });
}

export async function approveProductionReport(
  approvalId: string,
  approvedBy?: string,
): Promise<ProductionReportResult> {
  return api<ProductionReportResult>(
    "/real-orders/production-reports/approvals/" + encodeURIComponent(approvalId) + "/approve",
    {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ approved_by: approvedBy }),
    },
  );
}

export async function executeProductionReport(
  workOrderId: string,
  approvalId: string,
  approvedBy?: string,
): Promise<ProductionReportResult> {
  return api<ProductionReportResult>("/real-orders/production-reports/execute", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ work_order_id: workOrderId, approval_id: approvalId, approved_by: approvedBy }),
  });
}

// ==================== 质量问题登记（OpenMES NCR，三步审批门禁） ====================

export type IssueTypeInfo = { id: number; name: string; severity?: string };

export type IssueRegistrationPlan = {
  work_order_id: string;
  work_order_no: string;
  issue_type_id: number;
  issue_type_name?: string;
  severity?: string;
  title: string;
  description?: string;
};

export type IssueRegistrationResult = {
  success: boolean;
  status?: string;
  written?: boolean;
  idempotent?: boolean;
  issue_id?: string;
  issue?: Record<string, any>;
  read_back_verified?: boolean;
  approval?: { approval_id: string; approved_by: string; approved: boolean };
  issue_plan?: IssueRegistrationPlan;
  error?: string;
};

export async function listIssueTypes(): Promise<IssueTypeInfo[]> {
  return api<IssueTypeInfo[]>("/real-orders/quality/issue-types");
}

export async function requestIssueRegistration(
  workOrderId: string,
  issueTypeId: number,
  title: string,
  description?: string,
  requestedBy?: string,
): Promise<IssueRegistrationResult> {
  return api<IssueRegistrationResult>("/real-orders/quality-issues/registration-request", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({
      work_order_id: workOrderId,
      issue_type_id: issueTypeId,
      title,
      ...(description ? { description } : {}),
      requested_by: requestedBy,
    }),
  });
}

export async function approveIssueRegistration(
  approvalId: string,
  approvedBy?: string,
): Promise<IssueRegistrationResult> {
  return api<IssueRegistrationResult>(
    "/real-orders/quality-issues/approvals/" + encodeURIComponent(approvalId) + "/approve",
    {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ approved_by: approvedBy }),
    },
  );
}

export async function executeIssueRegistration(
  workOrderId: string,
  approvalId: string,
  approvedBy?: string,
): Promise<IssueRegistrationResult> {
  return api<IssueRegistrationResult>("/real-orders/quality-issues/execute", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ work_order_id: workOrderId, approval_id: approvalId, approved_by: approvedBy }),
  });
}

export async function askAssistant(
  question: string,
  context: Record<string, unknown> = {},
): Promise<AssistantAnswer> {
  return api<AssistantAnswer>("/real-orders/assistant/ask", {
    method: "POST",
    body: JSON.stringify({ question, context }),
  });
}

// 流式问答（阶段十 SSE）：每完成一次智能体工具调用回调 onStep（调用链逐步点亮），
// 返回与同步端点结构一致的完整回答。网络异常/非 200/事件流中断时抛错，由调用方回退同步端点。
export async function askAssistantStream(
  question: string,
  context: Record<string, unknown> = {},
  options: { onStep?: (step: AssistantCallStep) => void; signal?: AbortSignal } = {},
): Promise<AssistantAnswer> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const sessionToken = getRealSessionToken();
  if (sessionToken) headers.set("Authorization", `Bearer ${sessionToken}`);
  const response = await fetch(`${API_BASE}/real-orders/assistant/ask/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, context }),
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    let message = `流式问答请求失败 (${response.status})`;
    try {
      const data = await response.json();
      const detail = data?.detail;
      message = typeof detail === "string" ? detail : detail?.message ?? message;
    } catch {
      // 保留默认消息
    }
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let final: AssistantAnswer | null = null;
  let streamError = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const ev of parser.feed(decoder.decode(value, { stream: true }))) {
      if (ev.event === "step") {
        options.onStep?.(JSON.parse(ev.data) as AssistantCallStep);
      } else if (ev.event === "done") {
        final = JSON.parse(ev.data) as AssistantAnswer;
      } else if (ev.event === "error") {
        streamError = (JSON.parse(ev.data) as { message?: string }).message ?? "协调智能体执行失败";
      }
    }
  }
  if (streamError) throw new AssistantStreamExecError(streamError);
  if (!final) throw new Error("流式响应提前结束，未收到完整回答");
  return final;
}

// 协调者执行失败（SSE error 事件）——同步端点重试必然同样失败，调用方不应回退；
// 传输层失败（网络/非 200/流中断）抛普通 Error，调用方回退同步端点。
export class AssistantStreamExecError extends Error {}
