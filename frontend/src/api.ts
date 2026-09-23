const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api";

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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(data?.detail ?? `请求失败 (${response.status})`);
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
