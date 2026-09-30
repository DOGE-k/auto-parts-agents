import { useState, useEffect, useCallback } from "react";
import QualityTodoPanel from "./components/QualityTodoPanel";
import AgentRunsPanel from "./components/AgentRunsPanel";
import AssistantPanel from "./components/AssistantPanel";
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
  getQualityTodo,
  getRealIdentity,
  getSessionIssuedAt,
  getRealSessionToken,
  getRealWriteToken,
  loginRealSession,
  setRealSessionToken,
  setRealWriteToken,
} from "./api";
import type {
  QualityTodoItem,
  RealIdentity,
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

// ========== 页面页签（阶段十信息架构优化：单页纵向堆叠改为页签分区） ==========
type RealTabKey = "assistant" | "flow" | "quality" | "runs";
const TAB_ITEMS: { key: RealTabKey; label: string }[] = [
  { key: "assistant", label: "协同问答" },
  { key: "flow", label: "订单流程" },
  { key: "quality", label: "质量中心" },
  { key: "runs", label: "运行记录" },
];

// ========== 页面组件 ==========
export default function RealBusinessPage() {
  const [activeTab, setActiveTab] = useState<RealTabKey>("flow");
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
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  // 阶段九：跨工单质量待办（仅 OpenMES 登录会话可见）
  const [qualityTodo, setQualityTodo] = useState<QualityTodoItem[] | null>(null);
  const [qualityTodoLoading, setQualityTodoLoading] = useState(false);
  const [qualityTodoError, setQualityTodoError] = useState("");
  const [todoFocusIssueId, setTodoFocusIssueId] = useState("");
  const [sessionExpiringSoon, setSessionExpiringSoon] = useState(false);

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3000);
  };

  // 审批人来自 ERPNext/OpenMES 当前登录用户；页面不再让操作者自报角色。
  // 仅初始解析一次：登录/保存/清除会话路径各自显式刷新身份；
  // 依赖 identity 会因每次返回新对象引用而无限循环请求 identity/me。
  useEffect(() => {
    void getRealIdentity()
      .then(setIdentity)
      .catch((e) => setError(e instanceof Error ? e.message : "真实身份解析失败"));
  }, []);

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

  // 阶段十：OpenMES 会话 15 分钟 TTL，过期前 90 秒提醒重新登录，避免演示中断
  useEffect(() => {
    if (!hasOpenmesSession) {
      setSessionExpiringSoon(false);
      return;
    }
    const check = () => {
      const issued = getSessionIssuedAt();
      // 无签发时间的历史会话按保守口径立即提醒（重新登录总是安全的）
      setSessionExpiringSoon(issued === 0 || Date.now() - issued > 13.5 * 60 * 1000);
    };
    check();
    const timer = window.setInterval(check, 20000);
    return () => window.clearInterval(timer);
  }, [hasOpenmesSession]);

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

  // "去处置"：切到该工单的跟单质量视图并展开既有 NCR 操作面板（处置流程零改动）
  const goToQualityDispose = useCallback(async (item: QualityTodoItem) => {
    setLoading(true);
    setError("");
    try {
      // 跨工单跳转不猜报价审批状态：先反查工单关联的 ERP 订单与已持久化报价
      let quotationApproved = false;
      try {
        const orders = await api<WorkOrder[]>("/mes/work-orders?limit=100");
        setWorkOrders(orders);
        const linkedOrder = orders
          .find((wo) => String(wo.work_order_id) === String(item.work_order_id))
          ?.customer_order_no?.trim();
        if (linkedOrder) {
          const quotations = await api<Quotation[]>("/real-orders/quotations");
          quotationApproved = quotations.some(
            (q) => q.erp_draft_id?.trim() === linkedOrder && q.status === "APPROVED",
          );
        }
      } catch {
        // 反查失败时发运门禁按"未审批"口径显示；NCR 处置面板不受影响
      }
      const [track, qual, ship] = await Promise.all([
        api<TrackingInfo>(`/real-orders/mes/track/${item.work_order_id}`),
        api<QualityInfo>(`/real-orders/quality/package/${item.work_order_id}`),
        api<ShipGateInfo>(`/real-orders/ship-gate/${item.work_order_id}?quotation_approved=${quotationApproved}`),
      ]);
      setTracking(track);
      setQuality(qual);
      setShipGate(ship);
      setWorkOrderId(String(item.work_order_id));
      setStep(8);
      setTodoFocusIssueId(String(item.issue_id));
      void restoreNcrWorkflowStates(qual);
      notify(`已打开工单 ${item.work_order_no || item.work_order_id} 的 NCR 处置面板`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开质量处置面板失败");
    } finally {
      setLoading(false);
    }
  }, []);

  // 跳转后滚动并高亮目标 NCR 卡片（依赖 activeTab：去处置先切回订单流程页签，渲染后再滚动）
  useEffect(() => {
    if (!todoFocusIssueId) return;
    const el = document.getElementById(`ncr-issue-${todoFocusIssueId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      const timer = window.setTimeout(() => setTodoFocusIssueId(""), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [todoFocusIssueId, quality, step, activeTab]);




  const resetFlow = () => {
    setStep(1);
    setQuotation(null);
    setPlan(null);
    setSelectedOptionId("");
    setTracking(null);
    setQuality(null);
    setShipGate(null);
    resetNcrWorkflows();
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
          {hasOpenmesSession && sessionExpiringSoon && (
            <div className="session-expiry-warning">
              OpenMES 会话即将过期（15 分钟 TTL）——请在"会话设置"中重新登录，以继续审批与处置操作。
            </div>
          )}
          <div className="real-session-toolbar">
            <button className="button ghost session-toggle" onClick={() => setSessionSettingsOpen((open) => !open)}>
              {sessionSettingsOpen ? "收起会话设置 ▲" : "会话设置 ▼"}
            </button>
            {sessionSettingsOpen && (
              <div className="real-session-panel">
                <p>仅在当前浏览器会话内保存短期 Bearer 会话和本地写入门禁令牌，不写入项目配置或审计记录。</p>
                <div className="real-session-login">
                  <strong>OpenMES 账号登录（推荐）</strong>
                  <div className="real-session-login-row">
                    <input
                      value={loginUsername}
                      onChange={(e) => setLoginUsername(e.target.value)}
                      placeholder="OpenMES 用户名"
                      autoComplete="username"
                    />
                    <input
                      type="password"
                      value={loginPassword}
                      onChange={(e) => setLoginPassword(e.target.value)}
                      placeholder="OpenMES 密码"
                      autoComplete="current-password"
                    />
                    <button
                      className="button primary"
                      disabled={loginBusy || !loginUsername.trim() || !loginPassword}
                      onClick={() => {
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
                                : `已登录为 ${result.identity?.display_name ?? result.access_token.slice(0, 6) + "…"}（OpenMES 短时会话）`,
                            );
                          })
                          .catch((e) => setLoginError(e instanceof Error ? e.message : "OpenMES 登录失败"))
                          .finally(() => setLoginBusy(false));
                      }}
                    >
                      {loginBusy ? "登录中…" : "登录"}
                    </button>
                  </div>
                  <p className="real-session-hint">
                    登录走真实 OpenMES 认证接口，返回默认 15 分钟 TTL 的短时会话；登出只清除本浏览器会话，不吊销上游令牌。
                  </p>
                  {loginError && <p className="real-session-error">登录失败：{loginError}（请确认 OpenMES 的用户名和密码，默认账号是安装 OpenMES 时设置的 admin）</p>}
                </div>
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

      {/* 页签导航（阶段十信息架构优化：问答/流程/质量/记录分区，待办数徽标提醒） */}
      <div className="real-tabs" role="tablist">
        {TAB_ITEMS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={activeTab === t.key}
            className={`real-tab ${activeTab === t.key ? "active" : ""}`}
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
            {t.key === "quality" && hasOpenmesSession && (qualityTodo?.length ?? 0) > 0 && (
              <span className="real-tab-count">{qualityTodo?.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* 全局错误横幅：任何页签的操作失败都在此显示 */}
      {error && (
        <div className="error-banner">
          <span>操作失败</span> {error}
          <button onClick={() => setError("")}>×</button>
        </div>
      )}

      {/* 智能协同问答（阶段五-八：协调智能体 + 方案批准执行闭环；10.5 抽出组件） */}
      {activeTab === "assistant" && (
        <ErrorBoundary name="智能协同问答">
          <AssistantPanel
            identity={identity}
            currentErpDraftId={quotation?.erp_draft_id ?? ""}
            currentWorkOrderId={workOrderId}
            notify={notify}
            onError={setError}
          />
        </ErrorBoundary>
      )}

      {/* 阶段九：质量待办（跨工单 MRB 待办视角，仅登录会话可见；10.5 抽出组件） */}
      {activeTab === "quality" && (
        <ErrorBoundary name="质量中心">
        <QualityTodoPanel
          hasOpenmesSession={hasOpenmesSession}
          qualityTodo={qualityTodo}
          qualityTodoLoading={qualityTodoLoading}
          qualityTodoError={qualityTodoError}
          busy={loading}
          onRefresh={() => void loadQualityTodo()}
          onGoDispose={(item) => {
            setActiveTab("flow");
            void goToQualityDispose(item);
          }}
        />
        </ErrorBoundary>
      )}

      {/* 步骤指示器（订单流程页签） */}
      {activeTab === "flow" && (
        <>
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

      {/* Step 1: 报价输入 */}
      {step === 1 && (
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
      )}
      {/* Step 2: 报价详情 + 审批 */}
      {step >= 2 && quotation && (
        <QuotationReviewPanel
          step={step}
          quotation={quotation}
          loading={loading}
          onApprove={(approved) => void approveQuotation(approved)}
        />
      )}
      {/* Step 3: ERP 草稿 */}
      {step >= 3 && quotation && (
        <ErpDraftPanel
          step={step}
          quotation={quotation}
          loading={loading}
          onCreateDraft={() => void createErpDraft()}
        />
      )}
      {/* Step 4: 采购分析 */}
      {step >= 4 && quotation && (
        <ProcurementAnalyzePanel
          step={step}
          quotation={quotation}
          plan={plan}
          loading={loading}
          selectedOptionId={selectedOptionId}
          onSelectOption={setSelectedOptionId}
          onAnalyze={() => void analyzeProcurement()}
        />
      )}
      {/* Step 5: 方案审批 */}
      {step === 5 && plan && plan.net_requirement.has_shortage && (
        <PlanApprovalPanel
          plan={plan}
          loading={loading}
          hasSelectedOption={Boolean(selectedOptionId)}
          onApprove={(approved) => void approveProcurement(approved)}
        />
      )}
      {/* Step 6: PO 草稿 */}
      {step >= 6 && plan && plan.net_requirement.has_shortage && (
        <PoDraftPanel
          step={step}
          plan={plan}
          loading={loading}
          onCreatePo={() => void createPoDraft()}
        />
      )}
      {/* Step 7: 选择工单 + 加载跟单 */}
      {step >= 7 && (
        <WorkOrderSelectPanel
          step={step}
          quotation={quotation}
          workOrders={workOrders}
          workOrderId={workOrderId}
          onSelectWorkOrder={setWorkOrderId}
          loading={loading}
          onRefreshWorkOrders={() => void refreshWorkOrders()}
          onLoadTracking={() => void loadTracking()}
        />
      )}
      {/* Step 8: 跟单 + 质量 + 发运门禁（面板级降级：NCR 卡片渲染异常不影响流程其余部分） */}
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
          onCheckNcrClosure={(id) => void checkNcrClosure(id)}
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

      {/* Agent 运行记录（阶段三：持久化可查；10.5 抽出组件） */}
      {activeTab === "runs" && (
        <ErrorBoundary name="Agent 运行记录">
          <AgentRunsPanel />
        </ErrorBoundary>
      )}

      {toast && <div className="toast">✓ &nbsp;{toast}</div>}
      {loading && <div className="busy-indicator"><span />处理中</div>}
    </div>
  );
}
