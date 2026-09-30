// 真实业务页面共享类型（阶段十 10.5 组件拆分：从 RealBusinessPage 抽出）
import type { QualityClosureCheck, RealQualityIssue } from "../api";

export type Customer = {
  customer_id: string;
  customer_name: string;
  customer_group: string;
  territory: string;
  authority: string;
};

export type Item = {
  item_id: string;
  item_code: string;
  item_name: string;
  item_group: string;
  stock_uom: string;
  is_stock_item: number;
  authority: string;
};

export type Quotation = {
  quotation_id: string;
  status: string;
  adapter_mode: string;
  customer: Record<string, any>;
  item: Record<string, any>;
  quantity: number;
  unit_price: string;
  total_price: string;
  currency: string;
  base_price: string;
  quantity_discount_factor: number;
  bom: Record<string, any>;
  inventory: Record<string, any>[];
  stock_sufficient: boolean;
  delivery_estimate: {
    basis: string;
    source: string;
    estimated_days: number | null;
    note: string;
  };
  evidence: Record<string, any>[];
  approval_id?: string;
  approved_by?: string;
  erp_draft?: Record<string, any>;
  erp_draft_id?: string;
  created_at: string;
};

export type Approval = {
  approval_id: string;
  approved: boolean;
  approved_by: string;
  quotation_id: string;
  notes?: string;
  created_at: string;
};

export type TrackingInfo = {
  work_order_id: string;
  work_order_no: string;
  status: string;
  quantity: string;
  completed_qty: string;
  completion_rate: number;
  due_date: string;
  eta: string | null;
  eta_status?: "RATE_BASED" | "DATA_MISSING" | "COMPLETED" | string;
  eta_basis?: string;
  observed_rate?: { units_per_hour?: number; remaining_qty?: string; estimated_remaining_hours?: number; source?: string } | null;
  eta_data_gaps?: { field: string; detail: string }[];
  risks: Record<string, any>[];
  line_name: string;
  authority: string;
  data_source: string;
};

export type QualityInfo = {
  work_order_id: string;
  quality_records: RealQualityIssue[];
  documents: Record<string, any>[];
  inspections: Record<string, any>[];
  open_issues_count: number;
  missing_documents: string[];
  unsupported_capabilities: { capability: string; status: string; reason: string }[];
  quality_gate_passed: boolean;
  gate_details: Record<string, any>;
  authority: string;
  data_source: string;
};

export type NcrForm = {
  disposition: string;
  non_conforming_qty: string;
  root_cause: string;
  containment_action: string;
  nc_source: string;
};

export type NcrWorkflow = {
  form: NcrForm;
  dispositionApprovalId?: string;
  dispositionApproved?: boolean;
  dispositionResult?: { status?: string; error?: string; read_back_verified?: boolean; idempotent?: boolean };
  closureCheck?: QualityClosureCheck;
  closeApprovalId?: string;
  closeApproved?: boolean;
  closeResult?: { status?: string; error?: string; read_back_verified?: boolean; idempotent?: boolean };
  busy?: string;
};

export type ShipGateInfo = {
  work_order_id: string;
  work_order_no: string;
  can_ship: boolean;
  blocking_reasons: string[];
  quotation_approved: boolean;
  quality_gate_passed: boolean;
  production_ready: boolean;
  completion_rate: number;
  quality_gate_details: Record<string, any>;
  tracking_summary: Record<string, any>;
  authority: string;
};

export type WorkOrder = {
  work_order_id: string;
  work_order_no: string;
  customer_order_no: string;
  product_id: string;
  product_name: string;
  quantity: string;
  status: string;
  line_name: string;
  authority: string;
};

export type NetRequirementItem = {
  item_id: string;
  item_name: string;
  uom: string;
  gross_requirement: string;
  available_stock: string;
  net_requirement: string;
  order_qty: string;
  qty_basis: string;
  stock_sufficient: boolean;
  unit_price: string;
  unit_price_record: string;
  price_status: string;
  currency: string;
  min_order_qty: string;
  min_order_qty_configured: boolean;
  lead_time_days: number | null;
  suppliers: string[];
  authority: string;
};

export type ProcOptionItem = {
  item_id: string;
  item_name: string;
  quantity: string;
  qty_basis: string;
  uom: string;
  unit_price: string;
  price_basis: string;
  price_record: string;
  line_total: string;
  authority: string;
};

export type SupplierOption = {
  option_id: string;
  supplier_id: string;
  supplier_name: string;
  lead_time_days: number | null;
  lead_time_source: string;
  covers_all_shortage_items: boolean;
  coverage: string;
  total_cost: string;
  total_cost_complete: boolean;
  currency: string;
  items: ProcOptionItem[];
  is_recommended: boolean;
  recommendation_reason: string;
  supplier_authority: string;
};

export type ProcurementPlan = {
  plan_id: string;
  quotation_id: string;
  status: string;
  adapter_mode: string;
  net_requirement: {
    finished_item: string;
    production_quantity: number;
    bom_found: boolean;
    shortage_evaluable?: boolean;
    net_requirements: NetRequirementItem[];
    shortage_items: NetRequirementItem[];
    shortage_count: number;
    has_shortage: boolean;
    total_estimated_cost: string;
    total_estimated_cost_complete: boolean;
    missing_data: Record<string, string>[];
  };
  supplier_options: SupplierOption[];
  recommended_option_id: string | null;
  recommendation_rule: string;
  recommendation: string;
  data_limitations: Record<string, string>[];
  evidence: Record<string, any>[];
  selected_option_id: string | null;
  approval_id: string | null;
  po_draft?: Record<string, any> | null;
  po_draft_id?: string;
  created_at: string;
};

export type ProcurementApproval = {
  success: boolean;
  status: string;
  plan_id: string;
  approval_id: string;
  selected_option_id: string;
};
