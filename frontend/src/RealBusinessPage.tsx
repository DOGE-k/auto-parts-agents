import { useState, useEffect, useCallback } from "react";
import {
  api,
  agentRunTypeNames,
  askAssistant,
  approveProcurementPlan,
  approveQuotation as approveQuotationApi,
  createPoFromPlan,
  getRealIdentity,
  getRealSessionToken,
  getRealWriteToken,
  setRealSessionToken,
  setRealWriteToken,
  requestQualityIssueDisposition,
  approveQualityIssueDisposition,
  writeQualityIssueDisposition,
  checkQualityIssueClosure,
  requestQualityIssueClose,
  approveQualityIssueClose,
  writeQualityIssueClose,
} from "./api";
import type {
  AgentRunSummary,
  AgentRunDetail,
  AgentRunEvidenceItem,
  AssistantAnswer,
  ProposalSupplierOption,
  RealIdentity,
  RealQualityIssue,
  QualityClosureCheck,
} from "./api";

// ========== 类型定义 ==========
type Customer = {
  customer_id: string;
  customer_name: string;
  customer_group: string;
  territory: string;
  authority: string;
};

type Item = {
  item_id: string;
  item_code: string;
  item_name: string;
  item_group: string;
  stock_uom: string;
  is_stock_item: number;
  authority: string;
};

type Quotation = {
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

type Approval = {
  approval_id: string;
  approved: boolean;
  approved_by: string;
  quotation_id: string;
  notes?: string;
  created_at: string;
};

type TrackingInfo = {
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

type QualityInfo = {
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

type NcrForm = {
  disposition: string;
  non_conforming_qty: string;
  root_cause: string;
  containment_action: string;
  nc_source: string;
};

type NcrWorkflow = {
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

type ShipGateInfo = {
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

type WorkOrder = {
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

type NetRequirementItem = {
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

type ProcOptionItem = {
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

type SupplierOption = {
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

type ProcurementPlan = {
  plan_id: string;
  quotation_id: string;
  status: string;
  adapter_mode: string;
  net_requirement: {
    finished_item: string;
    production_quantity: number;
    bom_found: boolean;
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

type ProcurementApproval = {
  success: boolean;
  status: string;
  plan_id: string;
  approval_id: string;
  selected_option_id: string;
};

// ========== 页面组件 ==========
// 轻量 Markdown 渲染（协调者回答）：先转义 HTML，再恢复标题/加粗/表格结构。
// 内容来源是本系统协调者与真实工具结果，无用户富文本输入面。
function formatAssistantAnswer(text: string): string {
  const esc = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = esc.split("\n");
  const out: string[] = [];
  let tableRows: string[][] = [];
  const bold = (s: string) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  const flushTable = () => {
    if (tableRows.length === 0) return;
    // 第二行是分隔行（---），跳过
    const body = tableRows.filter((cells, i) => !(i === 1 && cells.every((c) => /^:?-{2,}:?$/.test(c.trim()))));
    const rows = body.map((cells) => `<tr>${cells.map((c) => `<td>${bold(c.trim())}</td>`).join("")}</tr>`).join("");
    out.push(`<table class="md-table">${rows}</table>`);
    tableRows = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      tableRows.push(trimmed.slice(1, -1).split("|"));
      continue;
    }
    flushTable();
    if (/^#{1,4}\s/.test(trimmed)) {
      out.push(`<div class="md-heading">${bold(trimmed.replace(/^#{1,4}\s/, ""))}</div>`);
    } else if (trimmed === "") {
      out.push('<div class="md-gap"></div>');
    } else {
      out.push(`<div class="md-line">${bold(trimmed)}</div>`);
    }
  }
  flushTable();
  return out.join("");
}

export default function RealBusinessPage() {
  const [step, setStep] = useState(1);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState("");
  const [selectedItem, setSelectedItem] = useState("");
  const [quantity, setQuantity] = useState(100);
  const [deliveryDate, setDeliveryDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [quotation, setQuotation] = useState<Quotation | null>(null);
  const [plan, setPlan] = useState<ProcurementPlan | null>(null);
  const [selectedOptionId, setSelectedOptionId] = useState("");
  const [tracking, setTracking] = useState<TrackingInfo | null>(null);
  const [quality, setQuality] = useState<QualityInfo | null>(null);
  const [shipGate, setShipGate] = useState<ShipGateInfo | null>(null);
  const [workOrderId, setWorkOrderId] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [identity, setIdentity] = useState<RealIdentity | null>(null);
  const [sessionToken, setSessionToken] = useState(() => getRealSessionToken());
  const [writeToken, setWriteToken] = useState(() => getRealWriteToken());
  const [sessionSettingsOpen, setSessionSettingsOpen] = useState(false);
  const [ncrWorkflows, setNcrWorkflows] = useState<Record<string, NcrWorkflow>>({});
  // Agent 运行记录（阶段三：可从页面查询，持久化于 real_agent_runs 表）
  const [agentRunsOpen, setAgentRunsOpen] = useState(false);
  const [agentRuns, setAgentRuns] = useState<AgentRunSummary[]>([]);
  const [agentRunsLoading, setAgentRunsLoading] = useState(false);
  const [agentRunsError, setAgentRunsError] = useState("");
  const [agentTypeFilter, setAgentTypeFilter] = useState("");
  const [expandedRunId, setExpandedRunId] = useState("");
  const [agentRunDetail, setAgentRunDetail] = useState<AgentRunDetail | null>(null);
  // 智能协同问答（阶段五：协调智能体动态调用四个真实智能体）
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState<AssistantAnswer | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(false);
  // 方案卡片执行闭环（阶段七+八）：报价未审批时走双审批线（报价+方案各留痕）
  const [proposalExec, setProposalExec] = useState<{
    option_id: string;
    stage: "confirming" | "executing";
    approver: string;
    quotationApprover: string;
  } | null>(null);
  const [proposalExecResult, setProposalExecResult] = useState<{
    option_id: string;
    approval_id: string;
    quotation_approval_id?: string;
    po_draft_id?: string;
    read_back_verified?: boolean;
    error?: string;
  } | null>(null);

  const executeProposal = useCallback(async (
    planId: string,
    optionId: string,
    approver: string,
    quotation: { id: string; needsApproval: boolean; approver: string } | null,
  ) => {
    setProposalExec((prev) => (prev ? { ...prev, stage: "executing" } : prev));
    setError("");
    try {
      const actor = identity?.actor_id || approver;
      if (!actor) throw new Error("真实身份尚未解析，不能提交审批");
      let quotationApprovalId = "";
      if (quotation?.needsApproval) {
        const qApproval = await approveQuotationApi(quotation.id, actor);
        quotationApprovalId = qApproval.approval_id;
      }
      const approval = await approveProcurementPlan(planId, optionId, actor);
      const draftResult = await createPoFromPlan(planId, approval.approval_id, actor);
      setProposalExecResult({
        option_id: optionId,
        approval_id: approval.approval_id,
        quotation_approval_id: quotationApprovalId || undefined,
        po_draft_id: draftResult.draft?.draft_id,
        read_back_verified: draftResult.draft?.read_back_verified,
      });
      setProposalExec(null);
      notify(`方案已批准，PO 草稿 ${draftResult.draft?.draft_id ?? ""} 已创建`);
    } catch (e) {
      setProposalExecResult({
        option_id: optionId,
        approval_id: "",
        error: e instanceof Error ? e.message : "执行失败",
      });
      setProposalExec(null);
    }
  }, []);

  const submitAssistantQuestion = useCallback(async () => {
    const q = assistantQuestion.trim();
    if (!q || assistantLoading) return;
    setAssistantLoading(true);
    setAssistantAnswer(null);
    setError("");
    try {
      const context: Record<string, unknown> = {};
      if (quotation?.erp_draft_id) context.当前流程ERP订单号 = quotation.erp_draft_id;
      if (workOrderId) context.当前流程MES工单id = workOrderId;
      const result = await askAssistant(q, context);
      setAssistantAnswer(result);
      notify("协调智能体已回答");
    } catch (e) {
      setError(e instanceof Error ? e.message : "协调智能体调用失败");
    } finally {
      setAssistantLoading(false);
    }
  }, [assistantQuestion, assistantLoading, quotation, workOrderId]);

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3000);
  };

  // 审批人来自 ERPNext/OpenMES 当前登录用户；页面不再让操作者自报角色。
  useEffect(() => {
    void getRealIdentity()
      .then(setIdentity)
      .catch((e) => setError(e instanceof Error ? e.message : "真实身份解析失败"));
  }, [identity]);

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

  // 加载客户、物料和工单列表
  useEffect(() => {
    const loadData = async () => {
      try {
        const [custs, its, wos] = await Promise.all([
          api<Customer[]>("/real-orders/erp/customers/search?limit=20"),
          api<Item[]>("/real-orders/erp/items/search?limit=20"),
          api<WorkOrder[]>("/mes/work-orders?limit=20"),
        ]);
        setCustomers(custs);
        setItems(its);
        setWorkOrders(wos);
        if (custs.length) setSelectedCustomer(custs[0].customer_id);
        if (its.length) setSelectedItem(its[0].item_code);
        // 工单不能默认选择第一条。正式流程必须按 customer_order_no
        // 与当前 ERP 销售订单号精确关联后才能进入跟单/质量门禁。
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载数据失败");
      }
    };
    void loadData();
  }, []);

  // 步骤1: 生成报价
  const generateQuotation = useCallback(async () => {
    if (!selectedCustomer || !selectedItem || quantity <= 0) return;
    if (!deliveryDate) {
      setError("请填写客户要求交期（报价与销售订单草稿必需）");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const result = await api<Quotation>("/real-orders/quotation/analyze", {
        method: "POST",
        body: JSON.stringify({
          customer_id: selectedCustomer,
          item_code: selectedItem,
          quantity,
          delivery_date: deliveryDate,
        }),
      });
      setQuotation(result);
      setStep(2);
      notify("报价分析完成");
    } catch (e) {
      setError(e instanceof Error ? e.message : "报价分析失败");
    } finally {
      setLoading(false);
    }
  }, [selectedCustomer, selectedItem, quantity, deliveryDate]);

  // 步骤2: 审批报价
  const approveQuotation = useCallback(async (approved: boolean) => {
    if (!quotation) return;
    setLoading(true);
    setError("");
    try {
      const result = await api<Approval>(
        `/real-orders/quotations/${quotation.quotation_id}/approve`,
        {
          method: "POST",
          body: JSON.stringify({
            approved,
            approved_by: identity?.actor_id,
            notes: approved ? "同意报价，价格合理" : "驳回，价格需重新评估",
          }),
        },
      );
      // 重新获取报价状态
      const updated = await api<Quotation>(
        `/real-orders/quotations/${quotation.quotation_id}`,
      );
      const refreshedWorkOrders = await api<WorkOrder[]>('/mes/work-orders?limit=100');
      setWorkOrders(refreshedWorkOrders);
      const linked = refreshedWorkOrders.filter(
        (wo) => wo.customer_order_no?.trim() === updated.erp_draft_id?.trim(),
      );
      setWorkOrderId(linked.length === 1 ? linked[0].work_order_id : "");
      setQuotation(updated);
      setStep(3);
      notify(approved ? "报价已批准" : "报价已驳回");
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批操作失败");
    } finally {
      setLoading(false);
    }
  }, [quotation, identity]);

  // 步骤3: 创建 ERP 草稿
  const createErpDraft = useCallback(async () => {
    if (!quotation || !quotation.approval_id) return;
    setLoading(true);
    setError("");
    try {
      const result = await api<Record<string, any>>(
        "/real-orders/erp/draft/from-quotation",
        {
          method: "POST",
          body: JSON.stringify({
            quotation_id: quotation.quotation_id,
            approval_id: quotation.approval_id,
            approved_by: identity?.actor_id,
          }),
        },
      );
      // 更新报价
      const updated = await api<Quotation>(
        `/real-orders/quotations/${quotation.quotation_id}`,
      );
      setQuotation(updated);
      setStep(4);
      notify("ERP 销售订单草稿已创建");
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建 ERP 草稿失败");
    } finally {
      setLoading(false);
    }
  }, [quotation, identity]);

  // 步骤4: 采购分析（基于报价，读取真实 BOM/库存/供应商数据）
  const analyzeProcurement = useCallback(async () => {
    if (!quotation) return;
    setLoading(true);
    setError("");
    try {
      const result = await api<ProcurementPlan>("/real-orders/procurement/analyze", {
        method: "POST",
        body: JSON.stringify({ quotation_id: quotation.quotation_id }),
      });
      setPlan(result);
      if (result.net_requirement.has_shortage) {
        setSelectedOptionId(result.recommended_option_id ?? result.supplier_options[0]?.option_id ?? "");
        setStep(5);
      } else {
        // 库存充足无需采购：跳过方案审批与 PO 草稿步骤，直接进入跟单质量
        setStep(7);
      }
      notify(result.net_requirement.has_shortage ? "采购分析完成，检测到缺料" : "库存充足，无需采购");
    } catch (e) {
      setError(e instanceof Error ? e.message : "采购分析失败");
    } finally {
      setLoading(false);
    }
  }, [quotation]);

  // 步骤5: 选择供应商方案并人工审批
  const approveProcurement = useCallback(async (approved: boolean) => {
    if (!plan || !selectedOptionId) return;
    setLoading(true);
    setError("");
    try {
      await api<ProcurementApproval>(
        `/real-orders/procurement/plans/${plan.plan_id}/approve`,
        {
          method: "POST",
          body: JSON.stringify({
            option_id: selectedOptionId,
            approved,
            approved_by: identity?.actor_id,
            notes: approved ? "确认供应商方案" : "驳回，重新询价",
          }),
        },
      );
      const plans = await api<ProcurementPlan[]>("/real-orders/procurement/plans");
      const updated = plans.find((p) => p.plan_id === plan.plan_id) ?? null;
      setPlan(updated);
      if (updated) {
        notify(`采购方案已${approved ? "批准" : "驳回"}`);
        if (approved && updated.status === "APPROVED") setStep(6);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "采购审批失败");
    } finally {
      setLoading(false);
    }
  }, [plan, selectedOptionId, identity]);

  // 步骤6: 创建 ERP 采购订单草稿并回读
  const createPoDraft = useCallback(async () => {
    if (!plan || !plan.approval_id) return;
    setLoading(true);
    setError("");
    try {
      const result = await api<Record<string, any>>("/real-orders/erp/draft/po-from-plan", {
        method: "POST",
        body: JSON.stringify({
          plan_id: plan.plan_id,
          approval_id: plan.approval_id,
          approved_by: identity?.actor_id,
        }),
      });
      const plans = await api<ProcurementPlan[]>("/real-orders/procurement/plans");
      const updated = plans.find((p) => p.plan_id === plan.plan_id) ?? null;
      setPlan(updated);
      // PO 草稿创建成功后进入跟单质量步骤，并刷新工单列表
      // （新建 ERP 草稿可能在创建后通过官方接口建立了 MES 关联）
      const refreshedWorkOrders = await api<WorkOrder[]>("/mes/work-orders?limit=100");
      setWorkOrders(refreshedWorkOrders);
      setStep(7);
      notify(result?.draft?.draft_id ? `采购订单草稿 ${result.draft.draft_id} 已创建` : "采购订单草稿已创建");
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建采购订单草稿失败");
    } finally {
      setLoading(false);
    }
  }, [plan, identity]);

  // 步骤7: 跟单 + 质量 + 发运门禁
  const loadTracking = useCallback(async () => {
    if (!workOrderId) return;
    const selected = workOrders.find((wo) => wo.work_order_id === workOrderId);
    if (!quotation?.erp_draft_id || !selected || selected.customer_order_no?.trim() !== quotation.erp_draft_id.trim()) {
      setError("当前 MES 工单未与本次 ERP 销售订单正式关联，不能加载跟单与质量结果。请先在 OpenMES 建立 customer_order_no 精确关联。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [track, qual, ship] = await Promise.all([
        api<TrackingInfo>(`/real-orders/mes/track/${workOrderId}`),
        api<QualityInfo>(`/real-orders/quality/package/${workOrderId}`),
        api<ShipGateInfo>(
          `/real-orders/ship-gate/${workOrderId}?quotation_approved=${quotation?.status === "APPROVED"}`,
        ),
      ]);
      setTracking(track);
      setQuality(qual);
      setShipGate(ship);
      setStep(8);
      notify("跟单与质量数据已加载");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载跟单数据失败");
    } finally {
      setLoading(false);
    }
  }, [workOrderId, quotation, workOrders]);

  const refreshQualityAndGate = useCallback(async () => {
    if (!workOrderId) return;
    const [qual, ship] = await Promise.all([
      api<QualityInfo>(`/real-orders/quality/package/${encodeURIComponent(workOrderId)}`),
      api<ShipGateInfo>(
        `/real-orders/ship-gate/${encodeURIComponent(workOrderId)}?quotation_approved=${quotation?.status === "APPROVED"}`,
      ),
    ]);
    setQuality(qual);
    setShipGate(ship);
  }, [workOrderId, quotation]);

  const updateNcrWorkflow = useCallback((issueId: string, patch: Partial<NcrWorkflow>) => {
    setNcrWorkflows((prev) => ({
      ...prev,
      [issueId]: { ...prev[issueId], ...patch, form: patch.form ?? prev[issueId]?.form ?? {
        disposition: "",
        non_conforming_qty: "",
        root_cause: "",
        containment_action: "",
        nc_source: "",
      } },
    }));
  }, []);

  const updateNcrForm = useCallback((issueId: string, field: keyof NcrForm, value: string) => {
    setNcrWorkflows((prev) => {
      const current = prev[issueId] ?? {
        form: { disposition: "", non_conforming_qty: "", root_cause: "", containment_action: "", nc_source: "" },
      };
      return { ...prev, [issueId]: { ...current, form: { ...current.form, [field]: value } } };
    });
  }, []);

  const requestNcrDisposition = useCallback(async (issue: RealQualityIssue) => {
    const issueId = String(issue.record_id ?? "");
    if (!issueId || !workOrderId) return;
    const form = ncrWorkflows[issueId]?.form;
    if (!form?.disposition || !form.root_cause.trim() || !form.containment_action.trim()) {
      setError("请先选择 NCR 处置，并填写根因与遏制措施；系统不会替你推断处置结论。");
      return;
    }
    updateNcrWorkflow(issueId, { busy: "requesting_disposition" });
    setError("");
    try {
      const result = await requestQualityIssueDisposition(issueId, {
        work_order_id: workOrderId,
        disposition: form.disposition,
        non_conforming_qty: form.non_conforming_qty.trim() ? Number(form.non_conforming_qty) : undefined,
        root_cause: form.root_cause.trim(),
        containment_action: form.containment_action.trim(),
        nc_source: form.nc_source || undefined,
      });
      updateNcrWorkflow(issueId, {
        dispositionApprovalId: result.approval?.approval_id,
        dispositionApproved: false,
        dispositionResult: { status: result.status ?? "待审批", error: result.error },
        busy: "",
      });
      notify(`NCR ${issueId} 处置审批已建立${result.approval?.approval_id ? `（${result.approval.approval_id}）` : ""}`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", dispositionResult: { error: e instanceof Error ? e.message : "创建处置审批失败" } });
      setError(e instanceof Error ? e.message : "创建处置审批失败");
    }
  }, [ncrWorkflows, updateNcrWorkflow, workOrderId]);

  const approveNcrDisposition = useCallback(async (issueId: string) => {
    const approvalId = ncrWorkflows[issueId]?.dispositionApprovalId;
    if (!approvalId) return;
    updateNcrWorkflow(issueId, { busy: "approving_disposition" });
    setError("");
    try {
      const result = await approveQualityIssueDisposition(approvalId);
      updateNcrWorkflow(issueId, { dispositionApproved: true, dispositionResult: { status: "已批准", ...result }, busy: "" });
      notify(`NCR ${issueId} 处置审批已批准`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", dispositionResult: { error: e instanceof Error ? e.message : "处置审批失败" } });
      setError(e instanceof Error ? e.message : "处置审批失败");
    }
  }, [ncrWorkflows, updateNcrWorkflow]);

  const writeNcrDisposition = useCallback(async (issueId: string) => {
    const workflow = ncrWorkflows[issueId];
    const approvalId = workflow?.dispositionApprovalId;
    if (!approvalId || !workflow.dispositionApproved || !workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "writing_disposition" });
    setError("");
    try {
      const result = await writeQualityIssueDisposition(issueId, workOrderId, approvalId);
      updateNcrWorkflow(issueId, {
        dispositionResult: {
          status: result.status,
          error: result.success ? undefined : result.error,
          read_back_verified: result.read_back_verified,
          idempotent: result.idempotent,
        },
        busy: "",
      });
      await refreshQualityAndGate();
      if (!result.success) throw new Error(result.error ?? "NCR 处置写回未验证");
      notify(`NCR ${issueId} 处置已写回并完成回读验证`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", dispositionResult: { error: e instanceof Error ? e.message : "NCR 处置写回失败" } });
      setError(e instanceof Error ? e.message : "NCR 处置写回失败");
    }
  }, [ncrWorkflows, refreshQualityAndGate, updateNcrWorkflow, workOrderId]);

  const checkNcrClosure = useCallback(async (issueId: string) => {
    if (!workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "checking_closure" });
    setError("");
    try {
      const result = await checkQualityIssueClosure(issueId, workOrderId);
      updateNcrWorkflow(issueId, { closureCheck: result, busy: "" });
      notify(result.closure_ready ? `NCR ${issueId} 已满足关闭前置条件` : `NCR ${issueId} 仍有关闭前置条件未满足`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closureCheck: undefined });
      setError(e instanceof Error ? e.message : "关闭前置校验失败");
    }
  }, [updateNcrWorkflow, workOrderId]);

  const requestNcrClose = useCallback(async (issueId: string) => {
    if (!workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "requesting_close" });
    setError("");
    try {
      const result = await requestQualityIssueClose(issueId, workOrderId);
      updateNcrWorkflow(issueId, { closeApprovalId: result.approval?.approval_id, closeApproved: false, closeResult: { status: "待审批" }, busy: "" });
      notify(`NCR ${issueId} 关闭审批已建立${result.approval?.approval_id ? `（${result.approval.approval_id}）` : ""}`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closeResult: { error: e instanceof Error ? e.message : "创建关闭审批失败" } });
      setError(e instanceof Error ? e.message : "创建关闭审批失败");
    }
  }, [updateNcrWorkflow, workOrderId]);

  const approveNcrClose = useCallback(async (issueId: string) => {
    const approvalId = ncrWorkflows[issueId]?.closeApprovalId;
    if (!approvalId) return;
    updateNcrWorkflow(issueId, { busy: "approving_close" });
    setError("");
    try {
      await approveQualityIssueClose(approvalId);
      updateNcrWorkflow(issueId, { closeApproved: true, closeResult: { status: "已批准" }, busy: "" });
      notify(`NCR ${issueId} 关闭审批已批准`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closeResult: { error: e instanceof Error ? e.message : "关闭审批失败" } });
      setError(e instanceof Error ? e.message : "关闭审批失败");
    }
  }, [ncrWorkflows, updateNcrWorkflow]);

  const writeNcrClose = useCallback(async (issueId: string) => {
    const workflow = ncrWorkflows[issueId];
    const approvalId = workflow?.closeApprovalId;
    if (!approvalId || !workflow.closeApproved || !workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "writing_close" });
    setError("");
    try {
      const result = await writeQualityIssueClose(issueId, workOrderId, approvalId);
      updateNcrWorkflow(issueId, {
        closeResult: { status: result.status, error: result.success ? undefined : result.error, read_back_verified: result.read_back_verified, idempotent: result.idempotent },
        busy: "",
      });
      await refreshQualityAndGate();
      if (!result.success) throw new Error(result.error ?? "NCR 关闭写回未验证");
      notify(`NCR ${issueId} 已关闭并完成回读验证`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closeResult: { error: e instanceof Error ? e.message : "NCR 关闭写回失败" } });
      setError(e instanceof Error ? e.message : "NCR 关闭写回失败");
    }
  }, [ncrWorkflows, refreshQualityAndGate, updateNcrWorkflow, workOrderId]);

  const resetFlow = () => {
    setStep(1);
    setQuotation(null);
    setPlan(null);
    setSelectedOptionId("");
    setTracking(null);
    setQuality(null);
    setShipGate(null);
    setNcrWorkflows({});
  };

  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">真实系统业务链</div>
          <h1>真实 ERP + MES 订单全流程</h1>
          <p>
            数据全部来自 ERPNext 与 OpenMES 真实系统，所有操作保留来源系统与原始记录编号。
          </p>
          <div className="data-source-info">
            <span className="source-tag erp">ERPNext</span>
            <span className="ds-label">客户/物料/BOM/价格/库存 · 只读 + 草稿写入</span>
            <span className="source-tag mes">OpenMES</span>
            <span className="ds-label">工单/进度/质量 · 只读</span>
          </div>
          <div className="data-source-info">
            <span className="source-tag erp">审批身份</span>
            <span className="ds-label">
              {identity
                ? `${identity.display_name}（${identity.actor_id}，${identity.authority}，角色：${identity.roles.join("、") || "未返回"}）`
                : "正在从 ERPNext/OpenMES 解析当前登录用户…"}
            </span>
          </div>
          <div className="real-session-toolbar">
            <button className="button ghost session-toggle" onClick={() => setSessionSettingsOpen((open) => !open)}>
              {sessionSettingsOpen ? "收起会话设置 ▲" : "会话设置 ▼"}
            </button>
            {sessionSettingsOpen && (
              <div className="real-session-panel">
                <p>仅在当前浏览器会话内保存短期 Bearer 会话和本地写入门禁令牌，不写入项目配置或审计记录。</p>
                <label>
                  Bearer 会话（可选）
                  <input
                    type="password"
                    value={sessionToken}
                    onChange={(e) => setSessionToken(e.target.value)}
                    placeholder="粘贴短期企业会话令牌"
                    autoComplete="off"
                  />
                </label>
                <label>
                  本地写入令牌（可选）
                  <input
                    type="password"
                    value={writeToken}
                    onChange={(e) => setWriteToken(e.target.value)}
                    placeholder="服务端 REAL_WRITE_API_TOKEN"
                    autoComplete="off"
                  />
                </label>
                <div className="real-session-actions">
                  <button
                    className="button primary"
                    onClick={() => {
                      setRealSessionToken(sessionToken);
                      setRealWriteToken(writeToken);
                      void getRealIdentity().then(setIdentity).catch((e) => setError(e instanceof Error ? e.message : "真实身份解析失败"));
                      notify("会话设置已保存到当前浏览器会话");
                    }}
                  >
                    保存并重新解析身份
                  </button>
                  <button
                    className="button ghost"
                    onClick={() => {
                      setSessionToken("");
                      setWriteToken("");
                      setRealSessionToken("");
                      setRealWriteToken("");
                      void getRealIdentity().then(setIdentity).catch((e) => setError(e instanceof Error ? e.message : "真实身份解析失败"));
                    }}
                  >
                    清除会话
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="heading-badges">
          <span className="badge green">真实数据连接</span>
          <span className="badge blue-badge">Agent 辅助</span>
        </div>
      </div>

      {/* 智能协同问答（阶段五：协调智能体动态调用四个真实智能体） */}
      <section className="panel assistant-panel">
        <div className="panel-heading">
          <div>
            <h2>智能协同问答</h2>
            <p>直接用一句话提问（如"SAL-ORD-2026-00023 什么时候能做完"），协调智能体会自己判断需要什么数据，动态调用报价/采购/跟单/质量四个智能体查询真实 ERP/MES 后回答，调用链全程留痕</p>
          </div>
          <span className="badge blue-badge">动态协同 · 只读</span>
        </div>
        <div className="assistant-input-row">
          <textarea
            value={assistantQuestion}
            onChange={(e) => setAssistantQuestion(e.target.value)}
            placeholder="例如：SAL-ORD-2026-00023 什么时候能做完？/ 这单为什么还不能发运？/ BD-2401 现在 2000 件多少钱？"
            rows={2}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submitAssistantQuestion();
              }
            }}
          />
          <button
            className="button primary"
            onClick={() => void submitAssistantQuestion()}
            disabled={assistantLoading || !assistantQuestion.trim()}
          >
            {assistantLoading ? "协调智能体处理中..." : "提问 →"}
          </button>
        </div>
        {assistantAnswer && (
          <div className="assistant-result">
            <div
              className="assistant-answer md-body"
              dangerouslySetInnerHTML={{ __html: formatAssistantAnswer(assistantAnswer.answer) }}
            />
            {assistantAnswer.proposal_options && (
              <div className="proposal-block">
                <small>可执行方案（真实工具结果汇集；最终由人工确认，执行请走 8 步审批流程）</small>
                {assistantAnswer.proposal_options.shortage && (
                  <div className="proposal-shortage">
                    缺料 {assistantAnswer.proposal_options.shortage.shortage_count} 项：
                    {assistantAnswer.proposal_options.shortage.shortage_items
                      .map((s) => `${s.item_id}(缺 ${s.net_requirement ?? "?"})`)
                      .join("、")}
                  </div>
                )}
                <div className="proposal-cards">
                  {(assistantAnswer.proposal_options.supplier_options ?? []).map((opt: ProposalSupplierOption) => {
                    const cost = (assistantAnswer.proposal_options?.cost_assessments ?? []).find((c) => c.option_id === opt.option_id);
                    const execResult = proposalExecResult?.option_id === opt.option_id ? proposalExecResult : null;
                    const confirming = proposalExec?.option_id === opt.option_id && proposalExec.stage === "confirming";
                    const executing = proposalExec?.option_id === opt.option_id && proposalExec.stage === "executing";
                    const planId = assistantAnswer.proposal_options?.plan_id ?? "";
                    return (
                      <div key={opt.option_id} className={`proposal-card ${opt.is_recommended ? "recommended" : ""}`}>
                        <div className="proposal-card-head">
                          <strong>{opt.supplier_name}</strong>
                          {opt.is_recommended && <span className="badge green">推荐</span>}
                          <span className="proposal-coverage">{opt.coverage} 项覆盖</span>
                        </div>
                        <div className="proposal-card-row"><span>采购总价</span><strong>{opt.total_cost ? `${opt.total_cost} ${opt.currency}` : "价格数据不完整"}</strong></div>
                        <div className="proposal-card-row"><span>交期(天)</span><strong>{opt.lead_time_days ?? "缺数据"}</strong></div>
                        {cost && (
                          <>
                            <div className="proposal-card-row">
                              <span>成本影响</span>
                              <strong className={cost.material_cost_delta.startsWith("-") ? "text-green" : "text-red"}>
                                {cost.material_cost_delta.startsWith("-") ? "" : "+"}{cost.material_cost_delta} {cost.currency}
                              </strong>
                            </div>
                            <div className="proposal-card-row"><span>单件加价</span><strong>{cost.per_unit_surcharge ? `${cost.per_unit_surcharge} ${cost.currency}` : "—"}</strong></div>
                            <div className="proposal-card-row"><span>材料毛利</span><strong>{cost.material_margin_before} → {cost.material_margin_after}</strong></div>
                          </>
                        )}
                        <div className="proposal-card-note">{opt.recommendation_reason}</div>
                        {/* 阶段七：方案批准 → PO 草稿（审批门禁内，草稿级写入） */}
                        {planId && opt.total_cost_complete && (execResult ? (
                          <div className={`proposal-exec-result ${execResult.error ? "error" : "ok"}`}>
                            {execResult.error
                              ? `执行失败：${execResult.error}`
                              : `✓ 已批准方案（${execResult.approval_id}）${execResult.quotation_approval_id ? `，报价审批（${execResult.quotation_approval_id}）` : ""}，PO 草稿 ${execResult.po_draft_id ?? "?"} ${execResult.read_back_verified ? "回读确认" : "待回读"}`}
                          </div>
                        ) : executing ? (
                          <div className="proposal-executing">执行中：审批 → 起草 PO → 回读...</div>
                        ) : confirming ? (
                          <div className="proposal-confirm">
                            <small>将执行（草稿级写入，不提交）：</small>
                            {(() => {
                              const needsQuotationApproval = (assistantAnswer.proposal_options?.quotation_status ?? "DRAFT") !== "APPROVED";
                              const qid = assistantAnswer.proposal_options?.quotation_id ?? "";
                              return (
                                <>
                                  {needsQuotationApproval && qid && (
                                    <code>① 审批报价 {qid}（当前状态 {assistantAnswer.proposal_options?.quotation_status}，审批人 {proposalExec?.quotationApprover}）</code>
                                  )}
                                  <code>{needsQuotationApproval ? "②" : "①"} 审批方案 {planId} 选 {opt.option_id}（{opt.supplier_name}，{opt.total_cost} {opt.currency}）→ 创建采购订单草稿</code>
                                  <div className="proposal-confirm-row">
                                    {needsQuotationApproval && qid && (
                                      <input
                                        value={identity?.actor_id ?? proposalExec?.quotationApprover ?? ""}
                                        readOnly
                                        placeholder="等待真实身份"
                                      />
                                    )}
                                    <input
                                      value={identity?.actor_id ?? proposalExec?.approver ?? ""}
                                      readOnly
                                      placeholder="等待真实身份"
                                    />
                                  </div>
                                  <div className="proposal-confirm-row">
                                    <button
                                      className="button primary"
                                      disabled={!identity}
                                      onClick={() => void executeProposal(
                                        planId,
                                        opt.option_id,
                                        proposalExec?.approver ?? "",
                                        needsQuotationApproval && qid
                                          ? { id: qid, needsApproval: true, approver: proposalExec?.quotationApprover ?? "" }
                                          : null,
                                      )}
                                    >
                                      确认批准并起草{needsQuotationApproval ? "（含报价审批）" : ""}
                                    </button>
                                    <button className="button ghost" onClick={() => setProposalExec(null)}>取消</button>
                                  </div>
                                </>
                              );
                            })()}
                          </div>
                        ) : (
                          <button className="button ghost proposal-exec-btn" disabled={!identity} onClick={() => setProposalExec({ option_id: opt.option_id, stage: "confirming", approver: identity?.actor_id ?? "", quotationApprover: identity?.actor_id ?? "" })}>
                            选择此方案并起草 PO（需人工确认）→
                          </button>
                        ))}
                      </div>
                    );
                  })}
                </div>
                {(assistantAnswer.proposal_options.delivery_assessments ?? []).map((d, idx) => (
                  <div key={idx} className={`proposal-delivery ${d.verdict === "arrival_after_due" ? "late" : "ok"}`}>
                    交期影响：{d.conclusion}（到货 {d.material_ready_date} vs 交期 {d.due_date}）
                  </div>
                ))}
                {(assistantAnswer.proposal_options.quality_impacts ?? []).map((q, idx) => (
                  <div key={idx} className="quality-impact-block">
                    <div className={`quality-impact-head ${q.quality_gate_passed ? "ok" : "bad"}`}>
                      质量异常影响分析 · 工单 {q.work_order_no}
                      （未关闭问题 {q.open_issues_count} · 质量门禁{q.quality_gate_passed ? "通过" : "未通过"}）
                    </div>
                    {q.quality_records.length > 0 && (
                      <table className="md-table">
                        <tbody>
                          {q.quality_records.map((r, i) => (
                            <tr key={i}>
                              <td>{r.severity}</td>
                              <td>{r.title}</td>
                              <td>{r.status}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                    {q.batches.length > 0 && (
                      <div className="quality-impact-batches">
                        批次关联：{q.batches.map((b) => `${b.lot_number || b.batch_id}（${b.status}，目标 ${b.target_qty}）`).join("、")}
                      </div>
                    )}
                    <ul className="quality-impact-conclusions">
                      {q.impact_conclusions.map((c, i) => <li key={i}>{c}</li>)}
                    </ul>
                    {q.handling_options.length > 0 && (
                      <div className="quality-impact-options">
                        <small>可执行处理选项（均需人工确认）：</small>
                        {q.handling_options.map((o, i) => (
                          <div key={i} className="quality-impact-option">{o.option} — {o.how}</div>
                        ))}
                      </div>
                    )}
                    {q.data_gaps.length > 0 && (
                      <div className="quality-impact-gaps">
                        数据缺口（如实标注）：{q.data_gaps.map((g) => g.detail).join("；")}
                      </div>
                    )}
                  </div>
                ))}
                {(assistantAnswer.proposal_options.data_missing ?? []).map((m, idx) => (
                  <div key={idx} className="proposal-missing">
                    数据缺失（{m.source_skill}）：{m.missing_fields.map((f) => f.detail).join("；")} → {m.need}
                  </div>
                ))}
              </div>
            )}
            <div className="assistant-chain">
              <small>
                调用链（{assistantAnswer.tool_count} 次智能体调用 · {assistantAnswer.rounds} 轮推理 · 记录 {assistantAnswer.coordination_run_id}）
              </small>
              {assistantAnswer.call_chain.map((step) => (
                <div key={step.seq} className={`assistant-step ${step.status}`}>
                  <span className="assistant-step-seq">{step.seq}</span>
                  <span className="assistant-step-callee">{agentRunTypeNames[step.callee] ?? step.callee}</span>
                  <code className="assistant-step-skill">{step.skill_id}</code>
                  <code className="assistant-step-args">{JSON.stringify(step.arguments)}</code>
                  <span className="assistant-step-status">
                    {step.status === "ok" ? "✓" : "✗"} {step.elapsed_ms}ms
                  </span>
                </div>
              ))}
            </div>
            <p className="field-hint">数据来源：{assistantAnswer.authority}。回答由 DeepSeek 汇总真实工具结果生成；写入类操作不在本通道执行。</p>
          </div>
        )}
      </section>

      {/* 步骤指示器 */}
      <div className="stepper">
        {[
          { n: 1, label: "报价分析" },
          { n: 2, label: "报价审批" },
          { n: 3, label: "ERP 草稿" },
          { n: 4, label: "采购分析" },
          { n: 5, label: "方案审批" },
          { n: 6, label: "PO 草稿" },
          { n: 7, label: "跟单质量" },
          { n: 8, label: "发运门禁" },
        ].map((s) => (
          <div
            key={s.n}
            className={`step-item ${step >= s.n ? "active" : ""} ${step === s.n ? "current" : ""}`}
          >
            <div className="step-number">{s.n}</div>
            <span>{s.label}</span>
          </div>
        ))}
      </div>

      {error && (
        <div className="error-banner">
          <span>操作失败</span> {error}
          <button onClick={() => setError("")}>×</button>
        </div>
      )}

      {/* Step 1: 报价输入 */}
      {step === 1 && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>第一步 · 生成报价</h2>
              <p>选择客户、物料和数量，系统从 ERP 读取真实数据生成报价方案</p>
            </div>
          </div>
          <div className="form-grid">
            <div className="form-group">
              <label>客户 (来自 ERPNext)</label>
              <select
                value={selectedCustomer}
                onChange={(e) => setSelectedCustomer(e.target.value)}
              >
                {customers.map((c) => (
                  <option key={c.customer_id} value={c.customer_id}>
                    {c.customer_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>物料 (来自 ERPNext)</label>
              <select
                value={selectedItem}
                onChange={(e) => setSelectedItem(e.target.value)}
              >
                {items.map((i) => (
                  <option key={i.item_code} value={i.item_code}>
                    {i.item_name} ({i.item_code})
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>数量</label>
              <input
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(Number(e.target.value))}
                min={1}
              />
            </div>
            <div className="form-group">
              <label>客户要求交期（必填）</label>
              <input
                type="date"
                value={deliveryDate}
                onChange={(e) => setDeliveryDate(e.target.value)}
              />
            </div>
          </div>
          <div className="form-actions">
            <button
              className="button primary"
              onClick={() => void generateQuotation()}
              disabled={loading || !selectedCustomer || !selectedItem}
            >
              {loading ? "分析中..." : "生成报价方案 →"}
            </button>
          </div>
        </section>
      )}

      {/* Step 2: 报价详情 + 审批 */}
      {step >= 2 && quotation && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>报价方案</h2>
              <p>
                报价 ID: {quotation.quotation_id} · 数据源:{" "}
                {quotation.adapter_mode === "real" ? "ERPNext 真实数据" : "Mock"}
              </p>
            </div>
            <span
              className={`badge ${quotation.status === "APPROVED" ? "green" : quotation.status === "REJECTED" ? "red-badge" : "amber-chip"}`}
            >
              {quotation.status === "DRAFT"
                ? "待审批"
                : quotation.status === "APPROVED"
                  ? "已批准"
                  : "已驳回"}
            </span>
          </div>

          <div className="kpi-grid">
            <div className="kpi-card">
              <span className="kpi-icon blue">¥</span>
              <span className="kpi-label">报价单价</span>
              <strong>
                {quotation.currency} {Number(quotation.unit_price).toLocaleString()}
              </strong>
            </div>
            <div className="kpi-card">
              <span className="kpi-icon purple">Σ</span>
              <span className="kpi-label">报价总额</span>
              <strong>
                {quotation.currency} {Number(quotation.total_price).toLocaleString()}
              </strong>
            </div>
            <div className="kpi-card">
              <span className="kpi-icon amber">◷</span>
              <span className="kpi-label">预计交期</span>
              <strong
                title={quotation.delivery_estimate?.note || ""}
              >
                {quotation.delivery_estimate?.estimated_days != null
                  ? `${quotation.delivery_estimate.estimated_days} 天`
                  : "数据缺失"}
              </strong>
              {quotation.delivery_estimate?.basis === "missing" && (
                <small>需补录 ERP 交期数据</small>
              )}
            </div>
            <div className="kpi-card">
              <span
                className={`kpi-icon ${quotation.stock_sufficient ? "green" : "red"}`}
              >
                {quotation.stock_sufficient ? "✓" : "!"}
              </span>
              <span className="kpi-label">库存状态</span>
              <strong>{quotation.stock_sufficient ? "库存充足" : "需生产"}</strong>
            </div>
          </div>

          <div className="detail-columns">
            <div>
              <small>计算依据</small>
              <ul className="evidence-list">
                {quotation.evidence.map((ev, idx) => (
                  <li key={idx}>
                    <span className={`source-tag ${ev.source === "ERPNext" ? "erp" : "mes"}`}>
                      {ev.source}
                    </span>
                    <span className="evidence-type">{ev.record_type}</span>
                    <span className="evidence-summary">{ev.summary}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <small>BOM 结构</small>
              {quotation.bom.found ? (
                <div className="bom-info">
                  <p>
                    BOM ID: <code>{quotation.bom.bom_id}</code>
                  </p>
                  <p>子项数量: {quotation.bom.items?.length ?? 0}</p>
                </div>
              ) : (
                <p>未找到 BOM</p>
              )}
            </div>
          </div>

          {step === 2 && (
            <div className="form-actions">
              <button
                className="button danger-ghost"
                onClick={() => void approveQuotation(false)}
                disabled={loading}
              >
                驳回
              </button>
              <button
                className="button primary"
                onClick={() => void approveQuotation(true)}
                disabled={loading}
              >
                {loading ? "处理中..." : "批准报价 →"}
              </button>
            </div>
          )}
        </section>
      )}

      {/* Step 3: ERP 草稿 */}
      {step >= 3 && quotation && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>ERP 销售订单草稿</h2>
              <p>审批通过后，在 ERPNext 中创建销售订单草稿（docstatus=0，不提交）</p>
            </div>
            {quotation.erp_draft_id ? (
              <span className="badge green">已创建</span>
            ) : (
              <span className="badge amber-chip">待创建</span>
            )}
          </div>

          {quotation.erp_draft ? (
            <div className="draft-info">
              <div className="draft-meta">
                <div>
                  <small>草稿编号</small>
                  <strong>{quotation.erp_draft.draft_id}</strong>
                </div>
                <div>
                  <small>客户</small>
                  <strong>{quotation.erp_draft.customer}</strong>
                </div>
                <div>
                  <small>总额</small>
                  <strong>{quotation.erp_draft.currency} {Number(quotation.erp_draft.total).toLocaleString()}</strong>
                </div>
                <div>
                  <small>状态</small>
                  <strong>{quotation.erp_draft.status}</strong>
                </div>
                <div>
                  <small>回读验证</small>
                  <strong className={quotation.erp_draft.read_back_verified ? "text-green" : "text-red"}>
                    {quotation.erp_draft.read_back_verified ? "✓ 已确认" : "✗ 未确认"}
                  </strong>
                </div>
              </div>
              <div className="source-note">
                <span className="source-tag erp">ERPNext</span>
                数据来源：ERPNext REST API · 草稿 docstatus=0 · 未提交
              </div>
            </div>
          ) : step === 3 ? (
            <div className="form-actions">
              <button
                className="button primary"
                onClick={() => void createErpDraft()}
                disabled={loading || quotation.status !== "APPROVED"}
              >
                {loading ? "创建中..." : "创建 ERP 销售订单草稿 →"}
              </button>
            </div>
          ) : null}
        </section>
      )}

      {/* Step 4: 采购分析 */}
      {step >= 4 && quotation && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>采购 Agent · 物料需求与供应商方案</h2>
              <p>
                基于 BOM 展开 + 真实库存计算缺料；供应商来自 ERP Item Supplier 关系，
                价格为供应商特定价格记录，交期来自 Item.lead_time_days
              </p>
            </div>
            {plan && (
              <span className={`badge ${plan.net_requirement.has_shortage ? "amber-chip" : "green"}`}>
                {plan.net_requirement.has_shortage
                  ? `缺料 ${plan.net_requirement.shortage_count} 项`
                  : "库存充足，无需采购"}
              </span>
            )}
          </div>

          {!plan ? (
            step === 4 && (
              <div className="form-actions">
                <button
                  className="button primary"
                  onClick={() => void analyzeProcurement()}
                  disabled={loading || quotation.status !== "APPROVED" || !quotation.erp_draft_id}
                  title={!quotation.erp_draft_id ? "请先创建 ERP 销售订单草稿" : ""}
                >
                  {loading ? "分析中..." : "开始采购分析 →"}
                </button>
              </div>
            )
          ) : (
            <>
              {plan.net_requirement.has_shortage && (
                <div className="shortage-section">
                  <small>缺料清单（毛需求 − 真实库存；数量含最小采购量约束）</small>
                  <table className="proc-table">
                    <thead>
                      <tr>
                        <th>物料</th>
                        <th>毛需求</th>
                        <th>库存</th>
                        <th>净需求</th>
                        <th>采购数量</th>
                        <th>MOQ</th>
                        <th>交期(天)</th>
                        <th>供应商</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plan.net_requirement.shortage_items.map((si) => (
                        <tr key={si.item_id}>
                          <td>{si.item_id} · {si.item_name}</td>
                          <td>{si.gross_requirement}</td>
                          <td>{si.available_stock}</td>
                          <td>{si.net_requirement}</td>
                          <td>
                            {si.order_qty}
                            {si.qty_basis === "min_order_qty" && (
                              <em className="moq-note">（按MOQ上调）</em>
                            )}
                          </td>
                          <td>
                            {si.min_order_qty_configured ? si.min_order_qty : <em>未配置</em>}
                          </td>
                          <td>
                            {si.lead_time_days != null ? si.lead_time_days : <em>未配置</em>}
                          </td>
                          <td>{si.suppliers?.join("、") || <em>无</em>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {plan.supplier_options.length > 0 && (
                <div className="supplier-options">
                  <small>
                    供应商方案 · 推荐规则 {plan.recommendation_rule}（价格：供应商特定价格记录；
                    交期：Item.lead_time_days）
                  </small>
                  {plan.supplier_options.map((opt) => (
                    <label
                      key={opt.option_id}
                      className={`supplier-option-card ${selectedOptionId === opt.option_id ? "selected" : ""}`}
                    >
                      <div className="option-head">
                        <input
                          type="radio"
                          name="supplier-option"
                          checked={selectedOptionId === opt.option_id}
                          onChange={() => setSelectedOptionId(opt.option_id)}
                        />
                        <strong>{opt.supplier_name}</strong>
                        <span className={`badge ${opt.is_recommended ? "green" : "blue-badge"}`}>
                          {opt.is_recommended ? "推荐" : `覆盖 ${opt.coverage}`}
                        </span>
                        {opt.total_cost && (
                          <span className="option-cost">
                            {opt.currency} {Number(opt.total_cost).toLocaleString()}
                          </span>
                        )}
                        <span className="option-lead">
                          交期 {opt.lead_time_days != null ? `${opt.lead_time_days} 天` : "缺失"}
                        </span>
                      </div>
                      <table className="proc-table">
                        <thead>
                          <tr>
                            <th>物料</th>
                            <th>数量</th>
                            <th>单价</th>
                            <th>价格依据</th>
                            <th>价格记录号</th>
                            <th>小计</th>
                          </tr>
                        </thead>
                        <tbody>
                          {opt.items.map((it) => (
                            <tr key={`${opt.option_id}-${it.item_id}`}>
                              <td>{it.item_id}</td>
                              <td>{it.quantity} {it.uom}</td>
                              <td>{it.unit_price || <em>缺失</em>}</td>
                              <td>
                                {it.price_basis === "supplier_specific_price"
                                  ? "供应商特定价"
                                  : it.price_basis === "standard_buying_price_fallback"
                                    ? "标准买价(回退)"
                                    : "数据缺失"}
                              </td>
                              <td><code>{it.price_record || "—"}</code></td>
                              <td>{it.line_total || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {opt.recommendation_reason && (
                        <p className="recommend-reason">{opt.recommendation_reason}</p>
                      )}
                    </label>
                  ))}
                </div>
              )}

              <div className="data-limitations">
                <small>数据来源与限制（如实标注）</small>
                <ul>
                  {(plan.data_limitations ?? []).map((d, idx) => (
                    <li key={idx}>{d.detail}</li>
                  ))}
                </ul>
                <ul className="evidence-list">
                  {(plan.evidence ?? []).map((ev, idx) => (
                    <li key={idx}>
                      <span className={`source-tag ${ev.source === "ERPNext" ? "erp" : "mes"}`}>
                        {ev.source}
                      </span>
                      <span className="evidence-type">{ev.record_type}</span>
                      <span className="evidence-summary">{ev.summary}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </section>
      )}

      {/* Step 5: 方案审批 */}
      {step === 5 && plan && plan.net_requirement.has_shortage && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>采购方案人工审批</h2>
              <p>
                {plan.recommendation}
              </p>
            </div>
            <span className={`badge ${plan.status === "APPROVED" ? "green" : "amber-chip"}`}>
              {plan.status === "APPROVED" ? "已批准" : "待审批"}
            </span>
          </div>
          <div className="form-actions">
            <button
              className="button danger-ghost"
              onClick={() => void approveProcurement(false)}
              disabled={loading || !selectedOptionId}
            >
              驳回
            </button>
            <button
              className="button primary"
              onClick={() => void approveProcurement(true)}
              disabled={loading || !selectedOptionId}
            >
              {loading ? "处理中..." : "批准选中方案 →"}
            </button>
          </div>
        </section>
      )}

      {/* Step 6: PO 草稿 */}
      {step >= 6 && plan && plan.net_requirement.has_shortage && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>ERP 采购订单草稿</h2>
              <p>审批通过后创建采购订单草稿（docstatus=0，不提交），并回读确认</p>
            </div>
            {plan.po_draft_id ? (
              <span className="badge green">已创建</span>
            ) : plan.status === "APPROVED" ? (
              <span className="badge amber-chip">待创建</span>
            ) : (
              <span className="badge red-badge">方案未批准</span>
            )}
          </div>
          {plan.po_draft ? (
            <div className="draft-info">
              <div className="draft-meta">
                <div>
                  <small>草稿编号</small>
                  <strong>{plan.po_draft?.draft_id}</strong>
                </div>
                <div>
                  <small>供应商</small>
                  <strong>{plan.po_draft?.supplier}</strong>
                </div>
                <div>
                  <small>交期</small>
                  <strong>{plan.po_draft?.schedule_date || "—"}</strong>
                </div>
                <div>
                  <small>回读验证</small>
                  <strong className={plan.po_draft?.read_back_verified ? "text-green" : "text-red"}>
                    {plan.po_draft?.read_back_verified ? "✓ 已确认" : "✗ 未确认"}
                  </strong>
                </div>
              </div>
              <div className="source-note">
                <span className="source-tag erp">ERPNext</span>
                {plan.po_draft?.delivery_note || "数据来源：ERPNext REST API · 草稿 docstatus=0 · 未提交"}
              </div>
            </div>
          ) : step === 6 ? (
            <div className="form-actions">
              <button
                className="button primary"
                onClick={() => void createPoDraft()}
                disabled={loading || plan.status !== "APPROVED"}
              >
                {loading ? "创建中..." : "创建 ERP 采购订单草稿 →"}
              </button>
            </div>
          ) : null}
        </section>
      )}

      {/* Step 7: 选择工单 + 加载跟单 */}
      {step >= 7 && (
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
                  onChange={(e) => setWorkOrderId(e.target.value)}
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
                  onClick={async () => {
                    try {
                      setWorkOrders(await api<WorkOrder[]>("/mes/work-orders?limit=100"));
                      notify("工单列表已刷新");
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "刷新工单列表失败");
                    }
                  }}
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
                onClick={() => void loadTracking()}
                disabled={loading || !workOrderId || !quotation?.erp_draft_id}
              >
                {loading ? "加载中..." : "加载跟单与质量数据 →"}
              </button>
            </div>
          )}
        </section>
      )}

      {/* Step 8: 跟单 + 质量 + 发运门禁 */}
      {step >= 8 && tracking && quality && shipGate && (
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
                  {quality.quality_records.filter((q) => q.record_id).map((issue) => {
                    const issueId = String(issue.record_id);
                    const workflow = ncrWorkflows[issueId];
                    const form = workflow?.form ?? { disposition: "", non_conforming_qty: "", root_cause: "", containment_action: "", nc_source: "" };
                    const busy = Boolean(workflow?.busy);
                    const isClosed = String(issue.status ?? "").toUpperCase() === "CLOSED";
                    const dispositionRecorded = ["scrap", "rework", "return_to_supplier", "use_as_is"].includes(String(issue.disposition ?? "").toLowerCase());
                    return (
                      <div key={issueId} className="ncr-workflow-card">
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
                            <div className="ncr-form-grid">
                              <label>
                                处置方案（人工选择）
                                <select value={form.disposition} onChange={(e) => updateNcrForm(issueId, "disposition", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)}>
                                  <option value="">请选择，不自动推断</option>
                                  <option value="scrap">报废（scrap）</option>
                                  <option value="rework">返工（rework）</option>
                                  <option value="return_to_supplier">退供应商（return_to_supplier）</option>
                                  <option value="use_as_is">让步接收（use_as_is）</option>
                                </select>
                              </label>
                              <label>
                                不合格数量（可选）
                                <input type="number" min="0" value={form.non_conforming_qty} onChange={(e) => updateNcrForm(issueId, "non_conforming_qty", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)} />
                              </label>
                              <label>
                                NC 来源（可选）
                                <select value={form.nc_source} onChange={(e) => updateNcrForm(issueId, "nc_source", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)}>
                                  <option value="">未指定</option>
                                  <option value="internal">内部</option>
                                  <option value="supplier">供应商</option>
                                  <option value="external">外部</option>
                                </select>
                              </label>
                              <label className="ncr-wide-field">
                                根因
                                <textarea value={form.root_cause} onChange={(e) => updateNcrForm(issueId, "root_cause", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)} rows={2} placeholder="填写可审计的根因" />
                              </label>
                              <label className="ncr-wide-field">
                                遏制措施
                                <textarea value={form.containment_action} onChange={(e) => updateNcrForm(issueId, "containment_action", e.target.value)} disabled={busy || Boolean(workflow?.dispositionApprovalId)} rows={2} placeholder="填写已执行或计划执行的遏制措施" />
                              </label>
                            </div>
                            <div className="ncr-action-row">
                              <button className="button ghost" disabled={busy || Boolean(workflow?.dispositionApprovalId)} onClick={() => void requestNcrDisposition(issue)}>
                                {workflow?.busy === "requesting_disposition" ? "建立审批中…" : "① 建立处置审批"}
                              </button>
                              {workflow?.dispositionApprovalId && <code>审批号 {workflow.dispositionApprovalId}</code>}
                              {workflow?.dispositionApprovalId && !workflow.dispositionApproved && (
                                <button className="button ghost" disabled={busy} onClick={() => void approveNcrDisposition(issueId)}>
                                  {workflow?.busy === "approving_disposition" ? "审批中…" : "② 批准处置"}
                                </button>
                              )}
                              {workflow?.dispositionApprovalId && workflow.dispositionApproved && (
                                <button className="button primary" disabled={busy} onClick={() => void writeNcrDisposition(issueId)}>
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
                            <div className="ncr-close-row">
                              <button className="button ghost" disabled={busy} onClick={() => void checkNcrClosure(issueId)}>
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
                                  <span key={key} className={passed ? "check-pass" : "check-fail"}>{passed ? "✓" : "✗"} {({ resolved: "问题已解决", disposition_recorded: "已登记处置", root_cause_recorded: "已记录根因", containment_action_recorded: "已记录遏制措施", corrective_actions_verified: "纠正措施已验证" } as Record<string, string>)[key] ?? key}</span>
                                ))}
                              </div>
                            )}
                            {workflow?.closureCheck?.closure_ready && (
                              <div className="ncr-action-row close-actions">
                                <button className="button ghost" disabled={busy || Boolean(workflow.closeApprovalId)} onClick={() => void requestNcrClose(issueId)}>
                                  {workflow?.busy === "requesting_close" ? "建立关闭审批中…" : "① 建立关闭审批"}
                                </button>
                                {workflow?.closeApprovalId && <code>关闭审批号 {workflow.closeApprovalId}</code>}
                                {workflow?.closeApprovalId && !workflow.closeApproved && (
                                  <button className="button ghost" disabled={busy} onClick={() => void approveNcrClose(issueId)}>
                                    {workflow?.busy === "approving_close" ? "审批中…" : "② 批准关闭"}
                                  </button>
                                )}
                                {workflow?.closeApprovalId && workflow.closeApproved && (
                                  <button className="button primary" disabled={busy} onClick={() => void writeNcrClose(issueId)}>
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
                        <small className="ncr-audit-hint">身份：{identity?.actor_id ?? "未解析"} · 来源：{identity?.authority ?? "—"} · 数据：{issue.data_source ?? quality.data_source}</small>
                      </div>
                    );
                  })}
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
            <button className="button ghost" onClick={resetFlow}>
              ← 重新开始
            </button>
            <button
              className="button primary"
              onClick={() => void loadTracking()}
              disabled={loading || !workOrderId}
            >
              {loading ? "加载中..." : "↻ 重新加载跟单与门禁（MES 进度更新后点击）"}
            </button>
          </div>
        </>
      )}

      {/* Agent 运行记录（阶段三：页面可查，持久化） */}
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
            {agentRunsError && <p className="field-hint">{agentRunsError}</p>}
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
                      <span className="agent-run-type">{agentRunTypeNames[run.agent_type] ?? run.agent_type}</span>
                      <span className="agent-run-op">{run.operation}</span>
                      <span className="agent-run-summary">{run.result_summary || "—"}</span>
                      <span className="agent-run-time">{run.started_at?.replace("T", " ").slice(0, 19) ?? ""}</span>
                    </button>
                    {expandedRunId === run.run_id && (
                      <div className="agent-run-detail">
                        {!agentRunDetail && <small>加载详情中...</small>}
                        {agentRunDetail && (
                          <>
                            <div className="agent-run-input">
                              <small>输入</small>
                              <code>{JSON.stringify(agentRunDetail.input?.args ?? [])}{JSON.stringify(agentRunDetail.input?.kwargs ?? {})}</code>
                            </div>
                            {agentRunDetail.error && (
                              <div className="agent-run-error">
                                <small>错误</small>
                                <code>{agentRunDetail.error.type}: {agentRunDetail.error.message}</code>
                              </div>
                            )}
                                {(agentRunDetail.result?.evidence ?? []).length > 0 && (
                                  <div className="agent-run-evidence">
                                    <small>证据链（真实记录编号，可追溯）</small>
                                    {(agentRunDetail.result?.evidence as AgentRunEvidenceItem[]).map((ev, idx) => (
                                  <div key={idx} className="evidence-item">
                                    <span className="evidence-source">{ev.source ?? "—"}</span>
                                    <span className="evidence-type">{ev.record_type ?? "—"}</span>
                                    <code className="evidence-id">{ev.record_id ?? "—"}</code>
                                    <span className="evidence-summary">{ev.summary ?? ""}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                            {((agentRunDetail.result?.data_limitations as Record<string, string>[]) ?? []).length > 0 && (
                              <div className="agent-run-limitations">
                                <small>数据限制（如实标注）</small>
                                {(agentRunDetail.result?.data_limitations as Record<string, string>[]).map((lim, idx) => (
                                  <div key={idx} className="limitation-item">{lim.field ?? ""} {lim.reason ?? JSON.stringify(lim)}</div>
                                ))}
                              </div>
                            )}
                            <div className="agent-run-times">
                              <small>开始 {agentRunDetail.started_at?.replace("T", " ").slice(0, 19) ?? "—"} · 结束 {agentRunDetail.finished_at?.replace("T", " ").slice(0, 19) ?? "—"}</small>
                            </div>
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

      {toast && <div className="toast">✓ &nbsp;{toast}</div>}
      {loading && <div className="busy-indicator"><span />处理中</div>}
    </div>
  );
}
