const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:9001/api";

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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
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
  return { "Idempotency-Key": crypto.randomUUID() };
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
};

export async function askAssistant(
  question: string,
  context: Record<string, unknown> = {},
): Promise<AssistantAnswer> {
  return api<AssistantAnswer>("/real-orders/assistant/ask", {
    method: "POST",
    body: JSON.stringify({ question, context }),
  });
}
