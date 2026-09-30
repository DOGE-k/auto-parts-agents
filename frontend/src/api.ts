const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:9001/api";
const REAL_SESSION_TOKEN_KEY = "real_business_session_token";
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
  if (value) window.sessionStorage.setItem(REAL_SESSION_TOKEN_KEY, value);
  else window.sessionStorage.removeItem(REAL_SESSION_TOKEN_KEY);
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

export async function askAssistant(
  question: string,
  context: Record<string, unknown> = {},
): Promise<AssistantAnswer> {
  return api<AssistantAnswer>("/real-orders/assistant/ask", {
    method: "POST",
    body: JSON.stringify({ question, context }),
  });
}
