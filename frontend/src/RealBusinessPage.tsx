import { useState, useEffect, useCallback } from "react";
import QualityTodoPanel from "./components/QualityTodoPanel";
import CollaborationEventsPanel from "./components/CollaborationEventsPanel";
import AgentRunsPanel from "./components/AgentRunsPanel";
import AssistantPanel from "./components/AssistantPanel";
import BusinessOverviewPanel from "./components/BusinessOverviewPanel";
import ConnectionSettingsPanel from "./components/ConnectionSettingsPanel";
import MesCompletionsPanel from "./components/MesCompletionsPanel";
import PendingApprovalsPanel from "./components/PendingApprovalsPanel";
import ErrorBoundary from "./components/ErrorBoundary";
import { useNcrWorkflows } from "./hooks/useNcrWorkflows";
import {
  QuotationInputStep,
  QuotationReviewPanel,
  ErpDraftPanel,
} from "./components/flow/QuotationFlow";
import {
  ProcurementAnalyzePanel,
  PlanApprovalPanel,
  PoDraftPanel,
} from "./components/flow/ProcurementFlow";
import {
  WorkOrderSelectPanel,
  TrackingQualityGatePanels,
} from "./components/flow/TrackingFlow";
import {
  api,
  approveWorkOrderDispatch,
  dispatchWorkOrder,
  formatSessionRemaining,
  getQualityTodo,
  getRealIdentity,
  getSessionRemainingMs,
  getRealSessionToken,
  getRealWriteToken,
  loginRealSession,
  requestWorkOrderDispatch,
  setRealSessionToken,
  setRealWriteToken,
} from "./api";
import type {
  AssistantJumpTarget,
  CollaborationEvent,
  QualityTodoItem,
  RealIdentity,
  WorkOrderDispatchPlan,
  WorkOrderDispatchResult,
} from "./api";
import type {
  Approval,
  Customer,
  Item,
  ProcurementApproval,
  ProcurementPlan,
  QualityInfo,
  Quotation,
  ShipGateInfo,
  TrackingInfo,
  WorkOrder,
} from "./types/realBusiness";

// ========== 真实业务一级模块（信息架构改版 §四/§七：左侧导航按工厂业务模块分组，
// 本组件承载全部真实业务模块的视图，由 App 的一级导航控制当前模块） ==========
export type RealModuleKey =
  | "assistant"     // AI 协同问答（默认主入口）
  | "overview"      // 业务总览
  | "sales"         // 销售与订单（流程步骤 1-3）
  | "procurement"   // 采购与缺料（流程步骤 4-6）
  | "production"    // 生产跟单（流程步骤 7-8 + MES 完工数据）
  | "quality"       // 质量中心（质量待办 + 协同事件）
  | "audit"         // 审批与审计（Agent 运行记录）
  | "connection";   // 系统连接（数据连接 + 审批账号 + 高级联调）

// 各模块页头文案（信息架构改版 §4.2/§4.3：顶部保留业务语境，审批身份压缩为一行摘要）
const MODULE_META: Record<RealModuleKey, { eyebrow: string; title: string; subtitle: string }> = {
  assistant: { eyebrow: "AI 协同", title: "AI 协同问答", subtitle: "用一句话提问，协调者自动调度报价/采购/跟单/质量四个智能体查询真实 ERP/MES；写操作停在人工审批。" },
  overview: { eyebrow: "工作台", title: "业务总览", subtitle: "待审批、质量待办、风险事件与最近业务链；处理动作进入对应业务模块。" },
  sales: { eyebrow: "业务协同", title: "销售与订单", subtitle: "报价分析 → 报价审批 → ERP 销售订单草稿；后续采购与生产在对应模块继续。" },
  procurement: { eyebrow: "业务协同", title: "采购与缺料", subtitle: "采购分析 → 供应商方案审批 → PO 草稿；缺料事件会自动协同相关方。" },
  production: { eyebrow: "业务协同", title: "生产跟单", subtitle: "工单下达/选择 → 跟单进度 + 质量记录 + 发运门禁；MES 完工数据为只读视图。" },
  quality: { eyebrow: "业务协同", title: "质量中心", subtitle: "跨工单质量待办与协同事件；处置走 NCR 审批门禁，接管≠已处置。" },
  audit: { eyebrow: "记录与管理", title: "审批与审计", subtitle: "每次智能体调用的输入、结果与耗时全程留痕，可追溯。" },
  connection: { eyebrow: "记录与管理", title: "系统连接", subtitle: "数据连接状态与审批账号会话；调试令牌收在高级联调设置里。" },
};

// 订单全流程 8 步（与后端审批链一致；各业务模块只展示自己的阶段）
const FULL_ORDER_STEPS = [
  { n: 1, label: "报价分析" },
  { n: 2, label: "报价审批" },
  { n: 3, label: "ERP 草稿" },
  { n: 4, label: "采购分析" },
  { n: 5, label: "方案审批" },
  { n: 6, label: "PO 草稿" },
  { n: 7, label: "跟单质量" },
  { n: 8, label: "发运门禁" },
];

// ========== 页面组件 ==========
type Props = {
  activeModule: RealModuleKey;
  onNavigate: (module: RealModuleKey) => void;
  onIdentityChange?: (identity: RealIdentity | null) => void;
  pendingQuestion?: string;
  onPendingQuestionConsumed?: () => void;
  onAskQuestion?: (question: string) => void;
  /** 连接状态上报（评审意见③：顶栏/侧栏/系统连接页共用同一真实探测状态源） */
  onConnectionStatus?: (status: RealConnectionStatus) => void;
};

export type RealConnectionStatus = {
  erpnext: "pending" | "connected" | "error";
  openmes: "pending" | "connected" | "error";
};

export default function RealBusinessPage({ activeModule, onNavigate, onIdentityChange, pendingQuestion, onPendingQuestionConsumed, onAskQuestion, onConnectionStatus }: Props) {
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
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  // 生产跟单模块的二级视图（信息架构改版：MES 完工数据并入生产跟单）
  const [productionView, setProductionView] = useState<"tracking" | "completions">("tracking");
  // 全厂跟单（2026-10-02 用户建议）：跨订单选择任意未完工工单直接跟单，不绑定当前流程
  const [globalWorkOrderId, setGlobalWorkOrderId] = useState("");
  // 继续已有订单（2026-10-02 用户建议）：销售/采购空态时可选历史报价或采购方案载入流程
  const [salesQuotationOptions, setSalesQuotationOptions] = useState<Quotation[] | null>(null);
  const [salesPickQuotationId, setSalesPickQuotationId] = useState("");
  const [planOptions, setPlanOptions] = useState<ProcurementPlan[] | null>(null);
  const [procurementPickPlanId, setProcurementPickPlanId] = useState("");
  // 阶段九：跨工单质量待办（仅 OpenMES 登录会话可见）
  const [qualityTodo, setQualityTodo] = useState<QualityTodoItem[] | null>(null);
  const [qualityTodoLoading, setQualityTodoLoading] = useState(false);
  const [qualityTodoError, setQualityTodoError] = useState("");
  const [todoFocusIssueId, setTodoFocusIssueId] = useState("");

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3000);
  };

  // 连接状态（评审意见③）：来自真实探测，不把"已配置"说成"已连接"。
  // ERPNext=身份解析成功即已连接；OpenMES=工单列表真实可读即已连接。
  const [connErp, setConnErp] = useState<"pending" | "connected" | "error">("pending");
  const [connMes, setConnMes] = useState<"pending" | "connected" | "error">("pending");

  useEffect(() => {
    onConnectionStatus?.({ erpnext: connErp, openmes: connMes });
  }, [connErp, connMes, onConnectionStatus]);

  // 审批人来自 ERPNext/OpenMES 当前登录用户；页面不再让操作者自报角色。
  // 仅初始解析一次：登录/保存/清除会话路径各自显式刷新身份；
  // 依赖 identity 会因每次返回新对象引用而无限循环请求 identity/me。
  useEffect(() => {
    void getRealIdentity()
      .then((id) => {
        setIdentity(id);
        setConnErp("connected");
      })
      .catch((e) => {
        setConnErp("error");
        setError(e instanceof Error ? e.message : "真实身份解析失败");
      });
  }, []);

  // 加载客户、物料和工单列表（OpenMES 连通性单独探测，不与 ERP 数据互相掩盖）
  useEffect(() => {
    const loadData = async () => {
      try {
        const [custs, its] = await Promise.all([
          api<Customer[]>("/real-orders/erp/customers/search?limit=20"),
          api<Item[]>("/real-orders/erp/items/search?limit=20"),
        ]);
        setCustomers(custs);
        setItems(its);
        if (custs.length) setSelectedCustomer(custs[0].customer_id);
        if (its.length) setSelectedItem(its[0].item_code);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载 ERP 数据失败");
      }
      try {
        const wos = await api<WorkOrder[]>("/mes/work-orders?limit=20");
        setWorkOrders(wos);
        setConnMes("connected");
        // 工单不能默认选择第一条。正式流程必须按 customer_order_no
        // 与当前 ERP 销售订单号精确关联后才能进入跟单/质量门禁。
      } catch (e) {
        setConnMes("error");
        setError(e instanceof Error ? e.message : "加载 OpenMES 工单失败");
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
      // 新报价开始新的订单上下文：旧采购方案与新报价可能不是同一笔业务，立即清除
      setPlan(null);
      setSelectedOptionId("");
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
        notify("采购分析完成，检测到缺料");
      } else if (result.net_requirement.shortage_evaluable === false) {
        // BOM 缺失：缺料评估不可用（后端如实返回 EVALUATION_BLOCKED）。
        // 停留在当前步展示阻断信息，绝不当作"库存充足无需采购"继续走流程。
        setStep(4);
        notify("缺料评估不可用：该物料未配置 BOM，请先在 ERPNext 补录");
      } else {
        // 库存充足无需采购：跳过方案审批与 PO 草稿步骤，直接进入跟单质量
        setStep(7);
        notify("库存充足，无需采购");
      }
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

  // NCR 处置/关闭工作流（阶段八+十；10.5 抽出为 hook，状态与审批动作自包含）
  const {
    ncrWorkflows,
    restoreNcrWorkflowStates,
    updateNcrForm,
    requestNcrDisposition,
    approveNcrDisposition,
    writeNcrDisposition,
    requestNcrResolution,
    approveNcrResolution,
    writeNcrResolution,
    checkNcrClosure,
    requestNcrClose,
    approveNcrClose,
    writeNcrClose,
    resetNcrWorkflows,
  } = useNcrWorkflows({ workOrderId, refreshQualityAndGate, onError: setError, notify });

  // 步骤 7 的工单列表刷新（OpenMES 建立关联后重新拉取）
  const refreshWorkOrders = useCallback(async () => {
    try {
      setWorkOrders(await api<WorkOrder[]>("/mes/work-orders?limit=100"));
      notify("工单列表已刷新");
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新工单列表失败");
    }
  }, []);

  // 工单下达（ERP 草稿 → OpenMES 工单，阶段十通用链路补强）：三步审批一次确认、过程透明展示
  const [dispatchFlow, setDispatchFlow] = useState<{
    stage: "confirm" | "executing" | "done";
    approvalId?: string;
    approvalDisplayed?: boolean;
    plan?: WorkOrderDispatchPlan;
    result?: WorkOrderDispatchResult;
  } | null>(null);

  const runWorkOrderDispatch = useCallback(async () => {
    if (!quotation) return;
    setDispatchFlow((prev) => (prev ? { ...prev, stage: "executing" } : prev));
    setError("");
    try {
      const requested = await requestWorkOrderDispatch(quotation.quotation_id, identity?.actor_id);
      if (!requested.success || !requested.approval) throw new Error(requested.error ?? "建立工单下达审批失败");
      setDispatchFlow((prev) => (prev ? { ...prev, approvalId: requested.approval!.approval_id, approvalDisplayed: true, plan: requested.dispatch_plan } : prev));
      const approved = await approveWorkOrderDispatch(requested.approval.approval_id, identity?.actor_id);
      if (!approved.success) throw new Error(approved.error ?? "工单下达审批失败");
      setDispatchFlow((prev) => (prev ? { ...prev, approvalId: requested.approval!.approval_id } : prev));
      const dispatched = await dispatchWorkOrder(quotation.quotation_id, requested.approval.approval_id, identity?.actor_id);
      if (!dispatched.success) throw new Error(dispatched.error ?? "工单下达失败");
      setDispatchFlow((prev) => (prev ? { ...prev, stage: "done", result: dispatched } : prev));
      notify(`工单 ${dispatched.work_order?.order_no ?? ""} 已下达（customer_order_no 关联已回读验证）`);
      // 下达成功后刷新工单列表并自动选中新建工单
      const refreshed = await api<WorkOrder[]>("/mes/work-orders?limit=100");
      setWorkOrders(refreshed);
      const newId = dispatched.work_order_id != null ? String(dispatched.work_order_id) : "";
      if (newId && refreshed.some((wo) => String(wo.work_order_id) === newId)) setWorkOrderId(newId);
    } catch (e) {
      setDispatchFlow((prev) => (prev ? { ...prev, stage: "done", result: { success: false, error: e instanceof Error ? e.message : "工单下达失败" } } : prev));
      setError(e instanceof Error ? e.message : "工单下达失败");
    }
  }, [quotation, identity]);

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
      void restoreNcrWorkflowStates(qual);
      notify("跟单与质量数据已加载");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载跟单数据失败");
    } finally {
      setLoading(false);
    }
  }, [workOrderId, quotation, workOrders, restoreNcrWorkflowStates]);

  // ========== 阶段九：跨工单质量待办 ==========
  const hasOpenmesSession = identity?.provider === "openmes";

  // 阶段十：OpenMES 会话 15 分钟 TTL（交接文档 §6：保留上游 TTL 不改，但临期必须
  // 显示实时倒计时）。过期前 90 秒进入临期提醒；无签发时间的历史会话按保守口径立即提醒。
  const [sessionNowMs, setSessionNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!hasOpenmesSession) return;
    setSessionNowMs(Date.now());
    const timer = window.setInterval(() => setSessionNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasOpenmesSession]);
  const sessionRemainingMs = hasOpenmesSession ? getSessionRemainingMs() : null;
  const sessionExpiringSoon = hasOpenmesSession && (sessionRemainingMs === null || sessionRemainingMs <= 90 * 1000);

  const loadQualityTodo = useCallback(async () => {
    if (identity?.provider !== "openmes") return;
    setQualityTodoLoading(true);
    setQualityTodoError("");
    try {
      const result = await getQualityTodo();
      setQualityTodo(result.items ?? []);
    } catch (e) {
      setQualityTodoError(e instanceof Error ? e.message : "质量待办加载失败");
    } finally {
      setQualityTodoLoading(false);
    }
  }, [identity?.provider]);

  // 登录会话建立后自动加载一次质量待办
  useEffect(() => {
    if (hasOpenmesSession) void loadQualityTodo();
  }, [hasOpenmesSession, loadQualityTodo]);

  // 打开某工单的跟单视图（评审意见①：问答/事件/待办跳转都携带对象编号并自动定位，
  // 不是只切到空模块）。反查报价审批状态（不猜）→ 三面板只读加载 → 生产跟单·跟单视图。
  const openWorkOrderTracking = useCallback(async (
    workOrderId: string,
    workOrderNo: string,
    opts?: { focusIssueId?: string; note?: string },
  ) => {
    if (!workOrderId && !workOrderNo) {
      setError("缺少关联的工单编号，无法打开跟单视图");
      return;
    }
    setLoading(true);
    setError("");
    try {
      let quotationApproved = false;
      try {
        const orders = await api<WorkOrder[]>("/mes/work-orders?limit=100");
        setWorkOrders(orders);
        const linkedOrder = orders
          .find((wo) => String(wo.work_order_id) === String(workOrderId))
          ?.customer_order_no?.trim();
        if (linkedOrder) {
          const quotations = await api<Quotation[]>("/real-orders/quotations");
          quotationApproved = quotations.some(
            (q) => q.erp_draft_id?.trim() === linkedOrder && q.status === "APPROVED",
          );
        }
      } catch {
        // 反查失败按"未审批"口径显示
      }
      const [track, qual, ship] = await Promise.all([
        api<TrackingInfo>(`/real-orders/mes/track/${workOrderId}`),
        api<QualityInfo>(`/real-orders/quality/package/${workOrderId}`),
        api<ShipGateInfo>(`/real-orders/ship-gate/${workOrderId}?quotation_approved=${quotationApproved}`),
      ]);
      setTracking(track);
      setQuality(qual);
      setShipGate(ship);
      setWorkOrderId(workOrderId);
      setStep(8);
      setProductionView("tracking");
      if (opts?.focusIssueId) setTodoFocusIssueId(opts.focusIssueId);
      onNavigate("production");
      void restoreNcrWorkflowStates(qual);
      notify(opts?.note ?? `已打开工单 ${workOrderNo || workOrderId} 的跟单视图`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开跟单视图失败");
    } finally {
      setLoading(false);
    }
  }, [notify, onNavigate, restoreNcrWorkflowStates]);

  // "去处置"：打开该工单跟单视图并聚焦 NCR 卡片（处置流程零改动，评审意见①）
  const goToQualityDispose = useCallback(async (item: QualityTodoItem) => {
    await openWorkOrderTracking(String(item.work_order_id), String(item.work_order_no ?? ""), {
      focusIssueId: String(item.issue_id ?? ""),
      note: `已打开工单 ${item.work_order_no || item.work_order_id} 的 NCR 处置面板`,
    });
  }, [openWorkOrderTracking]);

  // 全厂跟单（用户建议 2026-10-02）：选择任意未完工工单直接跟单。
  // 复用 openWorkOrderTracking 的守卫链路（工单反查报价审批状态 → 三面板只读加载）。
  const unfinishedWorkOrders = workOrders
    // 口径：生产完工（DONE）≠ 发运完成——完工单仍需质量关闭与发运门禁，只排除已取消/已关闭与 DEMO 模拟单
    .filter((wo) => !["CANCELLED", "CLOSED"].includes(String(wo.status ?? "").toUpperCase())
      && !String(wo.work_order_no ?? "").startsWith("DEMO_"))
    .sort((a, b) => String(a.due_date ?? "9999").localeCompare(String(b.due_date ?? "9999")));
  const trackGlobalWorkOrder = useCallback(async () => {
    if (!globalWorkOrderId) return;
    const wo = workOrders.find((w) => String(w.work_order_id) === globalWorkOrderId);
    await openWorkOrderTracking(globalWorkOrderId, wo?.work_order_no ?? "", {
      note: `已打开工单 ${wo?.work_order_no ?? globalWorkOrderId} 的跟单视图（全厂跟单）`,
    });
    setGlobalWorkOrderId("");
  }, [globalWorkOrderId, workOrders, openWorkOrderTracking]);


  // 打开某方案的采购审批视图（缺料事件与问答跳转共用；只读加载，不执行审批）
  const openProcurementPlanView = useCallback(async (
    quotationId?: string,
    planId?: string,
    note?: string,
  ) => {
    if (!quotationId || !planId) {
      setError("缺少关联的报价/采购方案编号，无法打开方案视图");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const loadedQuotation = await api<Quotation>(`/real-orders/quotations/${encodeURIComponent(quotationId)}`);
      const plans = await api<ProcurementPlan[]>("/real-orders/procurement/plans");
      const target = plans.find((pl) => pl.plan_id === planId) ?? null;
      if (!target) {
        setError(`采购方案 ${planId} 在当前业务库中不存在，无法跳转`);
        return;
      }
      setQuotation(loadedQuotation);
      setPlan(target);
      if (target.net_requirement.has_shortage) {
        setSelectedOptionId(target.recommended_option_id ?? target.supplier_options[0]?.option_id ?? "");
        setStep(5);
      } else {
        setStep(4);
      }
      onNavigate("procurement");
      notify(note ?? `已打开方案 ${planId} 的采购审批视图`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开采购方案视图失败");
    } finally {
      setLoading(false);
    }
  }, [notify, onNavigate]);

  // 打开某报价的审批视图（问答跳转定位到具体报价；评审意见①）
  const openQuotationView = useCallback(async (quotationId: string) => {
    setLoading(true);
    setError("");
    try {
      const loaded = await api<Quotation>(`/real-orders/quotations/${encodeURIComponent(quotationId)}`);
      setQuotation(loaded);
      setPlan(null);
      setSelectedOptionId("");
      setStep(2);
      onNavigate("sales");
      notify(`已打开报价 ${quotationId}（当前状态 ${loaded.status}）`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开报价视图失败");
    } finally {
      setLoading(false);
    }
  }, [notify, onNavigate]);

  // 发运门禁阻塞行的"去处理"入口（2026-10-02 用户反馈）：报价审批 ✗ → 直达该订单的报价审批
  const linkedErpOrderNo = workOrders.find((wo) => String(wo.work_order_id) === String(workOrderId))?.customer_order_no?.trim() ?? "";
  const goQuotationApprovalForCurrentWorkOrder = useCallback(async () => {
    if (!linkedErpOrderNo) {
      notify("该工单未关联 ERP 销售订单，没有可打开的报价审批");
      return;
    }
    try {
      const quotations = await api<Quotation[]>("/real-orders/quotations");
      const match = quotations.find((q) => q.erp_draft_id?.trim() === linkedErpOrderNo);
      if (!match) {
        setError(`ERP 订单 ${linkedErpOrderNo} 尚无报价记录（系统不伪造审批入口）；可在「销售与订单」为该订单重新走报价流程。`);
        return;
      }
      await openQuotationView(match.quotation_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开报价审批失败");
    }
  }, [linkedErpOrderNo, openQuotationView, notify]);

  // 继续已有订单（用户建议 2026-10-02）：销售页选历史报价载入、采购页选历史方案载入
  useEffect(() => {
    if (activeModule === "sales" && salesQuotationOptions === null) {
      void api<Quotation[]>("/real-orders/quotations")
        .then((list) => setSalesQuotationOptions([...list].reverse().slice(0, 20)))
        .catch(() => setSalesQuotationOptions([]));
    }
    if (activeModule === "procurement" && planOptions === null) {
      void api<ProcurementPlan[]>("/real-orders/procurement/plans")
        .then((list) => setPlanOptions([...list].reverse().slice(0, 20)))
        .catch(() => setPlanOptions([]));
    }
  }, [activeModule, salesQuotationOptions, planOptions]);

  const loadPickedQuotation = useCallback(async () => {
    if (!salesPickQuotationId) return;
    await openQuotationView(salesPickQuotationId);
    setSalesPickQuotationId("");
  }, [salesPickQuotationId, openQuotationView]);

  const loadPickedPlan = useCallback(async () => {
    if (!procurementPickPlanId) return;
    const picked = planOptions?.find((pl) => pl.plan_id === procurementPickPlanId);
    await openProcurementPlanView(picked?.quotation_id ?? "", procurementPickPlanId);
    setProcurementPickPlanId("");
  }, [procurementPickPlanId, planOptions, openProcurementPlanView]);

  // 协同事件"去处置/去处理"导航（P1 任务 A；只读导航，不执行任何审批/写入）
  const goToEventTarget = useCallback(async (event: CollaborationEvent) => {
    const payload = event.payload ?? {};
    if (event.event_type === "quality_issue_raised") {
      // 复用质量待办的处置跳转：加载工单三面板并聚焦该问题的 NCR 卡片
      await goToQualityDispose({
        issue_id: String(payload.issue_id ?? ""),
        work_order_id: String(payload.work_order_id ?? ""),
        work_order_no: String(payload.work_order_no ?? ""),
        title: String(payload.title ?? ""),
        severity: "",
        status: "",
        disposition: "",
        reported_at: "",
        assigned_to: "",
      });
      return;
    }
    if (event.event_type === "material_shortage") {
      const quotationId = event.result?.shortage?.quotation_id ?? payload.quotation_id;
      const planId = event.result?.shortage?.plan_id ?? payload.plan_id;
      await openProcurementPlanView(
        quotationId ? String(quotationId) : undefined,
        planId ? String(planId) : undefined,
        `已打开方案 ${planId} 的采购审批视图（基于事件 ${event.event_id}）`,
      );
      return;
    }
    if (event.event_type === "production_overdue" || event.event_type === "production_at_risk") {
      await openWorkOrderTracking(
        String(payload.work_order_id ?? ""),
        String(payload.work_order_no ?? ""),
        { note: `已打开工单 ${payload.work_order_no || payload.work_order_id} 的跟单视图（生产延期事件）` },
      );
      return;
    }
    setError(`事件 ${event.event_id} 类型未知（${event.event_type}），无法跳转`);
  }, [goToQualityDispose, openProcurementPlanView, openWorkOrderTracking]);

  // 问答回答跳转：携带业务对象编号自动定位打开（评审意见①）。
  // 只跳模块不定位对象的跳转被认为不像"打开这个订单"。
  const handleAssistantNavigate = useCallback(async (
    module: "sales" | "procurement" | "production" | "quality",
    target?: AssistantJumpTarget,
  ) => {
    if (module === "production" && (target?.work_order_id || target?.work_order_no)) {
      await openWorkOrderTracking(target?.work_order_id ?? "", target?.work_order_no ?? "");
      return;
    }
    if (module === "procurement" && (target?.plan_id || target?.quotation_id)) {
      await openProcurementPlanView(target?.quotation_id, target?.plan_id);
      return;
    }
    if (module === "sales" && target?.quotation_id) {
      await openQuotationView(target.quotation_id);
      return;
    }
    onNavigate(module);
  }, [openProcurementPlanView, openQuotationView, openWorkOrderTracking, onNavigate]);

  // 跳转后滚动并高亮目标 NCR 卡片（依赖 activeModule：去处置先切到生产跟单模块，渲染后再滚动）
  useEffect(() => {
    if (!todoFocusIssueId) return;
    const el = document.getElementById(`ncr-issue-${todoFocusIssueId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      const timer = window.setTimeout(() => setTodoFocusIssueId(""), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [todoFocusIssueId, quality, step, activeModule]);




  // OpenMES 登录/令牌处理（信息架构改版后由「系统连接」模块的 ConnectionSettingsPanel 调用）
  const handleOpenmesLogin = () => {
    setLoginBusy(true);
    setLoginError("");
    loginRealSession(loginUsername.trim(), loginPassword)
      .then((result) => {
        setRealSessionToken(result.access_token);
        setSessionToken(result.access_token);
        setLoginPassword("");
        if (result.identity) setIdentity(result.identity);
        else void getRealIdentity().then(setIdentity).catch(() => undefined);
        notify(
          result.force_password_change
            ? "登录成功，但上游要求先修改密码，该会话仅能改密"
            : `已登录为 ${result.identity?.actor_id ?? result.access_token.slice(0, 6) + "…"}（OpenMES 短时会话）`,
        );
      })
      .catch((e) => setLoginError(e instanceof Error ? e.message : "OpenMES 登录失败"))
      .finally(() => setLoginBusy(false));
  };

  const handleSaveTokens = () => {
    setRealSessionToken(sessionToken);
    setRealWriteToken(writeToken);
    void getRealIdentity().then(setIdentity).catch((e) => setError(e instanceof Error ? e.message : "真实身份解析失败"));
    notify("会话设置已保存到当前浏览器会话");
  };

  const handleClearTokens = () => {
    setSessionToken("");
    setWriteToken("");
    setRealSessionToken("");
    setRealWriteToken("");
    void getRealIdentity().then(setIdentity).catch((e) => setError(e instanceof Error ? e.message : "真实身份解析失败"));
  };

  // 身份上报给 App：顶栏/侧栏显示当前审批人（信息架构改版 §4.2）。
  // 交接文档 §1：ERPNext 服务端集成账号是数据连接身份，不是浏览器审批用户——
  // 只有 OpenMES 登录会话的身份才能作为"当前审批人"上报，否则顶栏一律显示未登录。
  useEffect(() => {
    onIdentityChange?.(identity?.provider === "openmes" ? identity : null);
  }, [identity, onIdentityChange]);

  const resetFlow = () => {
    setStep(1);
    setQuotation(null);
    setPlan(null);
    setSelectedOptionId("");
    setTracking(null);
    setQuality(null);
    setShipGate(null);
    setWorkOrderId("");
    setDispatchFlow(null);
    resetNcrWorkflows();
  };

  // 返回初始状态（2026-10-02 用户反馈：没有入口开始下一笔订单）。
  // 只清空页面流程状态；已创建的审批记录、ERP 草稿与审计留痕仍保留。
  const confirmStartNewOrder = () => {
    if (!window.confirm("开始一笔新订单？将清空当前页面流程（报价 / 采购方案 / 工单选择）。\n\n已创建的审批记录、ERP 草稿和审计留痕仍保留在系统与「审批与审计」中，不受影响。")) return;
    resetFlow();
    notify("已返回初始状态，可开始新订单");
  };

  // 流程工具条：有任一流程对象时显示"返回初始状态"入口（销售与订单 / 采购与缺料共用）
  const renderFlowToolbar = () => (
    (quotation || plan || workOrderId) ? (
      <div className="module-actions">
        <button className="button ghost" onClick={confirmStartNewOrder}>↺ 返回初始状态（开始新订单）</button>
      </div>
    ) : null
  );

  // ========== 流程步骤与业务对象的一致性（交接文档 §8） ==========
  // 单一 step 状态会被跨模块跳转改写（如打开工单跟单 setStep(8)），步骤条与
  // "已完成"卡必须由已加载对象证明进度，不能仅凭 step 残留值渲染：
  // - 无报价 → 最多亮到第 1 步；有报价无 ERP 草稿 → 最多第 3 步；
  // - 有草稿且方案已批准 → 允许到第 8 步；有草稿无已批方案 → 最多第 7 步。
  const planApproved = plan?.status === "APPROVED";
  const objectProvenMaxStep = !quotation
    ? 1
    : quotation.erp_draft_id
      ? (planApproved ? 8 : 7)
      : 3;
  const displayStep = Math.min(step, objectProvenMaxStep);

  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">真实系统业务链 · {MODULE_META[activeModule].eyebrow}</div>
          <h1>{MODULE_META[activeModule].title}</h1>
          <p>{MODULE_META[activeModule].subtitle}</p>
          <div className="real-identity-compact">
            <span className="source-tag erp">审批身份</span>
            {identity === null && connErp === "error" ? (
              // 身份解析已失败（如 OpenMES 会话过期——后端安全设计：失效会话不回退
              // 集成账号，直接报错），不能显示成"正在解析"误导用户
              <span className="ds-label">审批账号会话已失效，请到「系统连接」重新登录</span>
            ) : identity === null ? (
              <span className="ds-label">正在解析当前登录用户…</span>
            ) : hasOpenmesSession ? (
              <>
                <span className="ds-label">{identity.actor_id}（OpenMES）</span>
                <details className="identity-roles">
                  <summary>身份详情</summary>
                  <p>{identity.actor_id} · 角色：{identity.roles.join("、") || "未返回"}</p>
                </details>
              </>
            ) : (
              <>
                <span className="ds-label">审批账号未登录</span>
                <details className="identity-roles">
                  <summary>身份详情</summary>
                  <p>只读查询无需登录；审批、报工、NCR 处置等写入门禁需要 OpenMES 审批账号会话（去「系统连接」登录）。</p>
                  {identity && (
                    <p>ERPNext 数据连接账号：{identity.actor_id}（服务端集成账号，用于读取 ERP 数据，不是浏览器用户，不代表当前审批人）</p>
                  )}
                </details>
              </>
            )}
          </div>
          {hasOpenmesSession && sessionExpiringSoon && (
            <div className="session-expiry-warning">
              OpenMES 会话即将过期（{formatSessionRemaining(sessionRemainingMs)}）——请到「系统连接」重新登录，以继续审批与处置操作。
              <button className="button ghost" onClick={() => onNavigate("connection")}>去系统连接</button>
            </div>
          )}
        </div>
        <div className="heading-badges">
          <span className="badge green">真实数据连接</span>
          <span className="badge blue-badge">Agent 辅助</span>
        </div>
      </div>

      {/* 全局错误横幅：任何页签的操作失败都在此显示 */}
      {error && (
        <div className="error-banner">
          <span>操作失败</span> {error}
          <button onClick={() => setError("")}>×</button>
        </div>
      )}

      {/* AI 协同问答（默认主入口；信息架构改版 §4.3）。
          交接文档 §3：面板保持挂载（切模块时隐藏而非卸载），切换返回后聊天记录仍在 */}
      <div className={activeModule === "assistant" ? "assistant-pane" : "assistant-pane hidden-module-pane"}>
        <ErrorBoundary name="智能协同问答">
          <AssistantPanel
            identity={identity}
            currentErpDraftId={quotation?.erp_draft_id ?? ""}
            currentWorkOrderId={workOrderId}
            notify={notify}
            onError={setError}
            onNavigate={handleAssistantNavigate}
            initialQuestion={pendingQuestion}
            onQuestionConsumed={onPendingQuestionConsumed}
          />
        </ErrorBoundary>
      </div>

      {/* 业务总览（信息架构改版 §4.4：第二入口，只给概览与"需要处理"） */}
      {activeModule === "overview" && (
        <ErrorBoundary name="业务总览">
          <BusinessOverviewPanel
            hasOpenmesSession={hasOpenmesSession}
            qualityTodoCount={qualityTodo?.length ?? null}
            onNavigateAssistant={(question) => onAskQuestion?.(question)}
            onNavigate={onNavigate}
            onOpenEvent={(event) => void goToEventTarget(event)}
          />
        </ErrorBoundary>
      )}

      {/* 质量中心：跨工单质量待办 + 协同事件（复用原组件与处置链路） */}
      {activeModule === "quality" && (
        <ErrorBoundary name="质量中心">
          <QualityTodoPanel
            hasOpenmesSession={hasOpenmesSession}
            qualityTodo={qualityTodo}
            qualityTodoLoading={qualityTodoLoading}
            qualityTodoError={qualityTodoError}
            busy={loading}
            onRefresh={() => void loadQualityTodo()}
            onGoDispose={(item) => {
              onNavigate("production");
              void goToQualityDispose(item);
            }}
          />
          <CollaborationEventsPanel notify={notify} onError={setError} onGoTarget={(event) => void goToEventTarget(event)} />
        </ErrorBoundary>
      )}

      {/* 订单全流程按业务模块分段（信息架构改版 §七.4：订单流程变为详情/执行视图，
          销售与订单=步骤1-3、采购与缺料=步骤4-6、生产跟单=步骤7-8；步骤状态机共享） */}
      {(activeModule === "sales" || activeModule === "procurement" || activeModule === "production") && (
        <>
          {(() => {
            // 评审意见②：各模块只显示本业务阶段的步骤条；完整 8 步链收进折叠，
            // 不再像手动 Demo 一样默认铺开整条流程。
            // 交接文档 §8.6：步骤条高亮用 displayStep（对象可证明的进度），跳转残留的
            // step 不能把无对象的模块高亮到第 8 步。
            const stageSteps =
              activeModule === "sales"
                ? FULL_ORDER_STEPS.filter((s) => s.n <= 3)
                : activeModule === "procurement"
                  ? FULL_ORDER_STEPS.filter((s) => s.n >= 4 && s.n <= 6)
                  : FULL_ORDER_STEPS.filter((s) => s.n >= 7);
            const renderStepper = (steps: typeof FULL_ORDER_STEPS) => (
              <div className="stepper">
                {steps.map((s) => (
                  <div
                    key={s.n}
                    className={`step-item ${displayStep >= s.n ? "active" : ""} ${step === s.n ? "current" : ""}`}
                  >
                    <div className="step-number">{s.n}</div>
                    <span>{s.label}</span>
                  </div>
                ))}
              </div>
            );
            return (
              <>
                {renderStepper(stageSteps)}
                <details className="full-chain-details">
                  <summary>查看完整执行链（8 步）</summary>
                  {renderStepper(FULL_ORDER_STEPS)}
                </details>
              </>
            );
          })()}

          {/* 销售与订单：步骤 1-3（交接文档 §8.2：内容由已加载报价对象决定，
              无报价时显示报价表单而不是"已完成"空壳） */}
          {activeModule === "sales" && (
            <>
              {renderFlowToolbar()}
              {!quotation && (
                <section className="panel global-track-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>继续已有订单</h2>
                      <p>选择一条已有报价载入流程（最近 20 条，含历史记录）；或用下方表单开始新订单。</p>
                    </div>
                    <span className="badge blue-badge">{salesQuotationOptions?.length ?? "…"}</span>
                  </div>
                  <div className="global-track-row">
                    <select value={salesPickQuotationId} onChange={(e) => setSalesPickQuotationId(e.target.value)} aria-label="选择已有报价">
                      <option value="">选择报价 / 订单…</option>
                      {(salesQuotationOptions ?? []).map((q) => (
                        <option key={q.quotation_id} value={q.quotation_id}>
                          {q.quotation_id} · {q.customer?.customer_name ?? "未知客户"} · {q.item?.item_id ?? "?"} × {q.quantity} · {q.status}{q.erp_draft_id ? ` · ${q.erp_draft_id}` : ""}
                        </option>
                      ))}
                    </select>
                    <button className="button primary" disabled={!salesPickQuotationId || loading} onClick={() => void loadPickedQuotation()}>
                      {loading ? "载入中..." : "载入该订单 →"}
                    </button>
                  </div>
                </section>
              )}
              {!quotation ? (
                <QuotationInputStep
                  customers={customers}
                  items={items}
                  selectedCustomer={selectedCustomer}
                  onSelectCustomer={setSelectedCustomer}
                  selectedItem={selectedItem}
                  onSelectItem={setSelectedItem}
                  quantity={quantity}
                  onQuantityChange={setQuantity}
                  deliveryDate={deliveryDate}
                  onDeliveryDateChange={setDeliveryDate}
                  loading={loading}
                  onGenerate={() => void generateQuotation()}
                />
              ) : (
                <>
                  <QuotationReviewPanel
                    step={quotation.status === "DRAFT" ? 2 : step}
                    quotation={quotation}
                    loading={loading}
                    onApprove={(approved) => void approveQuotation(approved)}
                  />
                  {(quotation.status === "APPROVED" || quotation.erp_draft_id) && (
                    <ErpDraftPanel
                      step={quotation.erp_draft_id ? 7 : 3}
                      quotation={quotation}
                      loading={loading}
                      onCreateDraft={() => void createErpDraft()}
                    />
                  )}
                  {step >= 4 && quotation.erp_draft_id && (
                    <div className="module-hint-card">
                      <strong>报价与订单草稿已完成（{quotation.erp_draft_id}）</strong>
                      <span>采购分析、供应商方案审批和 PO 草稿在「采购与缺料」模块继续。</span>
                      <button className="button primary" onClick={() => onNavigate("procurement")}>去采购与缺料 →</button>
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {/* 采购与缺料：步骤 4-6（交接文档 §8.3：无报价/方案对象时显示明确空态，
              提供待审批方案入口；"采购链路已完成"必须由已批准方案证明） */}
          {activeModule === "procurement" && (
            <>
              {renderFlowToolbar()}
              {!quotation && (
                <section className="panel global-track-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>继续已有采购方案</h2>
                      <p>选择一条已有采购方案载入（最近 20 条，含历史记录），查看供应商选项与审批状态。</p>
                    </div>
                    <span className="badge blue-badge">{planOptions?.length ?? "…"}</span>
                  </div>
                  <div className="global-track-row">
                    <select value={procurementPickPlanId} onChange={(e) => setProcurementPickPlanId(e.target.value)} aria-label="选择已有采购方案">
                      <option value="">选择采购方案…</option>
                      {(planOptions ?? []).map((pl) => (
                        <option key={pl.plan_id} value={pl.plan_id}>
                          {pl.plan_id} · 报价 {pl.quotation_id} · {pl.status}{pl.net_requirement?.has_shortage ? ` · 缺料 ${pl.net_requirement.shortage_count} 项` : " · 无缺料"}
                        </option>
                      ))}
                    </select>
                    <button className="button primary" disabled={!procurementPickPlanId || loading} onClick={() => void loadPickedPlan()}>
                      {loading ? "载入中..." : "载入该方案 →"}
                    </button>
                  </div>
                </section>
              )}
              {!quotation ? (
                <div className="module-hint-card">
                  <strong>尚未选择报价或采购方案</strong>
                  <span>先在「销售与订单」完成报价、审批并创建 ERP 订单草稿，这里才能做采购分析；也可从待审批方案列表进入（该列表为全局口径，含历史记录）。</span>
                  <div className="hint-actions">
                    <button className="button primary" onClick={() => onNavigate("sales")}>去销售与订单 →</button>
                    <button className="button ghost" onClick={() => onNavigate("audit")}>去审批与审计（待审批方案）→</button>
                  </div>
                </div>
              ) : plan && plan.quotation_id !== quotation.quotation_id ? (
                /* 方案与当前报价不同单（跨订单状态残留防护，2026-10-02）：明确提示并给出出路 */
                <div className="module-hint-card">
                  <strong>当前采购方案与报价不是同一笔订单</strong>
                  <span>方案 {plan.plan_id} 属于报价 {plan.quotation_id}，而当前报价是 {quotation.quotation_id}（{quotation.erp_draft_id || "尚未创建 ERP 草稿"}）。可为当前报价重新执行采购分析（将生成新方案），或返回初始状态重开一笔。</span>
                  <div className="hint-actions">
                    <button className="button primary" disabled={loading} onClick={() => void analyzeProcurement()}>为当前报价重新执行采购分析 →</button>
                    <button className="button ghost" onClick={confirmStartNewOrder}>返回初始状态</button>
                  </div>
                </div>
              ) : (
                <>
                  <ProcurementAnalyzePanel
                    step={plan ? step : 4}
                    quotation={quotation}
                    plan={plan}
                    loading={loading}
                    selectedOptionId={selectedOptionId}
                    onSelectOption={setSelectedOptionId}
                    onAnalyze={() => void analyzeProcurement()}
                  />
                  {plan && plan.net_requirement.has_shortage && plan.status === "PENDING_APPROVAL" && (
                    <PlanApprovalPanel
                      plan={plan}
                      loading={loading}
                      hasSelectedOption={Boolean(selectedOptionId)}
                      onApprove={(approved) => void approveProcurement(approved)}
                    />
                  )}
                  {plan && plan.net_requirement.has_shortage && (plan.status === "APPROVED" || plan.po_draft_id) && (
                    <PoDraftPanel
                      step={plan.po_draft_id ? 7 : 6}
                      plan={plan}
                      loading={loading}
                      onCreatePo={() => void createPoDraft()}
                    />
                  )}
                  {step >= 7 && plan && plan.status === "APPROVED" && (
                    <div className="module-hint-card">
                      <strong>采购链路已完成（方案 {plan.plan_id}）</strong>
                      <span>工单下达、生产跟单与发运门禁在「生产跟单」模块继续。</span>
                      <button className="button primary" onClick={() => onNavigate("production")}>去生产跟单 →</button>
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {/* 生产跟单：步骤 7-8 + MES 完工数据（原独立导航项并入为二级视图） */}
          {activeModule === "production" && (
            <>
              <div className="real-tabs subview-tabs" role="tablist">
                <button
                  role="tab"
                  aria-selected={productionView === "tracking"}
                  className={`real-tab ${productionView === "tracking" ? "active" : ""}`}
                  onClick={() => setProductionView("tracking")}
                >
                  跟单视图
                </button>
                <button
                  role="tab"
                  aria-selected={productionView === "completions"}
                  className={`real-tab ${productionView === "completions" ? "active" : ""}`}
                  onClick={() => setProductionView("completions")}
                >
                  MES 完工数据
                </button>
              </div>
              {productionView === "completions" ? (
                <MesCompletionsPanel />
              ) : (
                <>
                  {/* 全厂跟单（2026-10-02 用户建议）：跨订单选择任意未完工工单，不绑定当前流程 */}
                  <section className="panel global-track-panel">
                    <div className="panel-heading">
                      <div>
                        <h2>全厂跟单（跨订单）</h2>
                        <p>选择任意未完工工单直接查看进度、质量与发运门禁；审批身份照常生效，不必先在销售流程中选中该订单。</p>
                      </div>
                      <span className="badge blue-badge">{unfinishedWorkOrders.length} 张未完工</span>
                    </div>
                    <div className="global-track-row">
                      <select
                        value={globalWorkOrderId}
                        onChange={(e) => setGlobalWorkOrderId(e.target.value)}
                        aria-label="选择未完工工单"
                      >
                        <option value="">
                          {unfinishedWorkOrders.length
                            ? `选择未完工工单（按交期排序，共 ${unfinishedWorkOrders.length} 张）`
                            : "当前没有未完工的工单"}
                        </option>
                        {unfinishedWorkOrders.map((wo) => (
                          <option key={wo.work_order_id} value={String(wo.work_order_id)}>
                            {wo.work_order_no} · {wo.product_name || wo.product_id}（{wo.status}）
                            {wo.customer_order_no?.trim() ? ` · 关联 ${wo.customer_order_no}` : " · 未关联 ERP 订单"}
                            {wo.due_date ? ` · 交期 ${wo.due_date.slice(0, 10)}` : ""}
                          </option>
                        ))}
                      </select>
                      <button
                        className="button primary"
                        disabled={!globalWorkOrderId || loading}
                        onClick={() => void trackGlobalWorkOrder()}
                      >
                        {loading ? "加载中..." : "加载该工单跟单 →"}
                      </button>
                    </div>
                  </section>
                  {step < 7 && (
                    <div className="module-hint-card">
                      <strong>还没有选定的工单</strong>
                      <span>完成报价与采购方案后可下达新工单；协同事件/问答跳转也会直接打开对应工单的跟单视图。</span>
                      <button className="button ghost" onClick={() => onNavigate("sales")}>回销售与订单</button>
                    </div>
                  )}
                  {step >= 7 && (
                    <WorkOrderSelectPanel
                      step={tracking ? step : 7}
                      quotation={quotation}
                      workOrders={workOrders}
                      workOrderId={workOrderId}
                      onSelectWorkOrder={setWorkOrderId}
                      loading={loading}
                      onRefreshWorkOrders={() => void refreshWorkOrders()}
                      onLoadTracking={() => void loadTracking()}
                      dispatchFlow={dispatchFlow}
                      onOpenDispatch={() => setDispatchFlow({ stage: "confirm" })}
                      onConfirmDispatch={() => void runWorkOrderDispatch()}
                      onDismissDispatch={() => setDispatchFlow(null)}
                    />
                  )}
                  {step >= 8 && tracking && quality && shipGate && (
                    <ErrorBoundary name="跟单质量与发运门禁">
                      <TrackingQualityGatePanels
                        tracking={tracking}
                        quality={quality}
                        shipGate={shipGate}
                        loading={loading}
                        workOrderId={workOrderId}
                        identity={identity}
                        ncrWorkflows={ncrWorkflows}
                        todoFocusIssueId={todoFocusIssueId}
                        onNcrFormChange={updateNcrForm}
                        onRequestNcrDisposition={(issue) => void requestNcrDisposition(issue)}
                        onApproveNcrDisposition={(id) => void approveNcrDisposition(id)}
                        onWriteNcrDisposition={(id) => void writeNcrDisposition(id)}
                        onRequestNcrResolution={(id, notes) => void requestNcrResolution(id, notes)}
                        onApproveNcrResolution={(id) => void approveNcrResolution(id)}
                        onWriteNcrResolution={(id, notes) => void writeNcrResolution(id, notes)}
                        onCheckNcrClosure={(id) => void checkNcrClosure(id)}
                        linkedErpOrderNo={linkedErpOrderNo}
                        onGoQuotationApproval={() => void goQuotationApprovalForCurrentWorkOrder()}
                        onGoQualityCenter={() => onNavigate("quality")}
                        onGoProcurementModule={() => onNavigate("procurement")}
                        onRequestNcrClose={(id) => void requestNcrClose(id)}
                        onApproveNcrClose={(id) => void approveNcrClose(id)}
                        onWriteNcrClose={(id) => void writeNcrClose(id)}
                        onReload={() => void loadTracking()}
                        onReset={resetFlow}
                      />
                    </ErrorBoundary>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}

      {/* 审批与审计（信息架构改版 §七.5；评审意见④：补待审批区块，像审批工作台而不只是运行记录） */}
      {activeModule === "audit" && (
        <ErrorBoundary name="审批与审计">
          <PendingApprovalsPanel
            onOpenQuotation={(quotationId) => void openQuotationView(quotationId)}
            onOpenPlan={(quotationId, planId) => void openProcurementPlanView(quotationId, planId)}
          />
          <AgentRunsPanel />
        </ErrorBoundary>
      )}

      {/* 系统连接（信息架构改版 §六：数据连接 + 审批账号 + 高级联调设置） */}
      {activeModule === "connection" && (
        <ErrorBoundary name="系统连接">
          <ConnectionSettingsPanel
            identity={identity}
            connErp={connErp}
            connMes={connMes}
            hasOpenmesSession={hasOpenmesSession}
            sessionExpiringSoon={sessionExpiringSoon}
            sessionToken={sessionToken}
            writeToken={writeToken}
            loginUsername={loginUsername}
            loginPassword={loginPassword}
            loginBusy={loginBusy}
            loginError={loginError}
            onLoginUsernameChange={setLoginUsername}
            onLoginPasswordChange={setLoginPassword}
            onLogin={handleOpenmesLogin}
            onSessionTokenChange={setSessionToken}
            onWriteTokenChange={setWriteToken}
            onSaveTokens={handleSaveTokens}
            onClearTokens={handleClearTokens}
          />
        </ErrorBoundary>
      )}

      {toast && <div className="toast">✓ &nbsp;{toast}</div>}
      {loading && <div className="busy-indicator"><span />处理中</div>}
    </div>
  );
}
