import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { api, agentNames, Approval, Case, Event, eventNames, Project, writeHeaders } from "./api";

type PageKey = "scenarios" | "dashboard" | "agents" | "plans" | "approvals" | "audit";
type Snapshot = Record<string, any>;

const navigation: { key: PageKey; label: string; icon: string }[] = [
  { key: "scenarios", label: "场景与回放", icon: "◫" },
  { key: "dashboard", label: "协同驾驶舱", icon: "▦" },
  { key: "agents", label: "Agent 工作台", icon: "◉" },
  { key: "plans", label: "方案对比", icon: "⇄" },
  { key: "approvals", label: "人工审批箱", icon: "✓" },
  { key: "audit", label: "审计与追溯", icon: "⌁" },
];

function App() {
  const [page, setPage] = useState<PageKey>("scenarios");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  const refreshProjects = useCallback(async () => {
    const data = await api<Project[]>("/projects");
    setProjects(data);
    if (!projectId && data.length) setProjectId(data[0].project_id);
  }, [projectId]);

  const refreshProject = useCallback(async (id = projectId) => {
    if (!id) return;
    const [projectSnapshot, timeline, pending, allAudit] = await Promise.all([
      api<Snapshot>(`/projects/${id}/snapshot`),
      api<Event[]>(`/projects/${id}/timeline`),
      api<Approval[]>("/approvals?status=pending"),
      api<any[]>(`/audit?correlation_id=${encodeURIComponent(projects.find((p) => p.project_id === id)?.correlation_id ?? "")}`),
    ]);
    setSnapshot(projectSnapshot);
    setEvents(timeline);
    setApprovals(pending);
    setAudit(allAudit);
  }, [projectId, projects]);

  const refreshAll = useCallback(async () => {
    try {
      await refreshProjects();
      if (projectId) await refreshProject(projectId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "连接本地服务失败");
    }
  }, [projectId, refreshProject, refreshProjects]);

  useEffect(() => {
    void refreshProjects().catch((e) => setError(e instanceof Error ? e.message : "连接本地服务失败"));
    void api("/health").then(() => setError("")).catch((e) => setError(e instanceof Error ? e.message : "连接本地服务失败"));
  }, []);

  useEffect(() => {
    if (!projectId) {
      setSnapshot(null);
      setEvents([]);
      return;
    }
    void refreshProject(projectId).catch((e) => setError(e instanceof Error ? e.message : "加载项目失败"));
  }, [projectId, refreshProject]);

  useEffect(() => {
    if (!projectId) return;
    const stream = new WebSocket(`ws://127.0.0.1:8000/api/projects/${projectId}/stream`);
    stream.onmessage = (message) => {
      try {
        const item = JSON.parse(message.data) as Event;
        if (item.event_id) {
          setEvents((current) => current.some((event) => event.event_id === item.event_id) ? current : [...current, item]);
          void refreshProject(projectId);
        }
      } catch { /* Ignore heartbeat frames. */ }
    };
    return () => stream.close();
  }, [projectId, refreshProject]);

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3200);
  };

  const runScenario = async (name: string) => {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ project_id: string }>(`/scenarios/${name}/run`, {
        method: "POST",
        headers: writeHeaders(),
        body: JSON.stringify({ seed: 20260923 }),
      });
      setProjectId(result.project_id);
      setPage("dashboard");
      await refreshProjects();
      notify(name === "normal_order" ? "正常订单演示已启动" : "缺料协作演示已启动");
    } catch (e) {
      setError(e instanceof Error ? e.message : "启动场景失败");
    } finally {
      setBusy(false);
    }
  };

  const resetProject = async () => {
    if (!projectId) return;
    setBusy(true);
    try {
      await api(`/projects/${projectId}/reset`, { method: "POST", headers: writeHeaders() });
      await refreshAll();
      notify("项目演示空间已复位");
    } catch (e) {
      setError(e instanceof Error ? e.message : "复位失败");
    } finally {
      setBusy(false);
    }
  };

  const decide = async (approvalId: string, decision: "approve" | "reject" | "request-data", body: any = {}) => {
    setBusy(true);
    try {
      const suffix = decision === "approve" ? "approve" : decision === "reject" ? "reject" : "request-data";
      await api(`/approvals/${approvalId}/${suffix}`, {
        method: "POST",
        headers: writeHeaders(),
        body: decision === "reject" ? undefined : JSON.stringify(body),
      });
      await refreshProjects();
      await refreshProject();
      notify(decision === "approve" ? "审批已批准" : decision === "reject" ? "审批已驳回" : "已记录补资料请求");
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批操作失败");
    } finally {
      setBusy(false);
    }
  };

  const selectedProject = projects.find((item) => item.project_id === projectId);
  const currentApprovals = useMemo(() => approvals.filter((item) => item.project_id === projectId), [approvals, projectId]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark">四</div>
          <div><strong>四智协作</strong><span>汽车零部件工厂</span></div>
        </div>
        <div className="sidebar-caption">工作空间</div>
        <nav className="nav-list">
          {navigation.map((item) => (
            <button key={item.key} className={`nav-item ${page === item.key ? "active" : ""}`} onClick={() => setPage(item.key)}>
              <span className="nav-icon">{item.icon}</span>{item.label}
              {item.key === "approvals" && currentApprovals.length > 0 && <span className="nav-count">{currentApprovals.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="mode-indicator"><i />本地 Mock 演示</div>
          <p>数据全部来自合成场景<br />不会连接真实 ERP 或 MES</p>
          <div className="version-tag">Runtime · 0.1.0</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>汽车零部件</span><b>/</b><strong>{navigation.find((item) => item.key === page)?.label}</strong></div>
          <div className="topbar-actions">
            <div className="connection"><i />Mock 服务</div>
            {projects.length > 0 && <select aria-label="当前项目" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {projects.map((item) => <option key={item.project_id} value={item.project_id}>{item.project_id} · {item.scenario === "normal_order" ? "正常订单" : "缺料协作"}</option>)}
            </select>}
            <button className="icon-button" title="刷新" onClick={() => void refreshAll()}>↻</button>
          </div>
        </header>

        {error && <div className="error-banner"><span>连接或操作失败</span> {error}<button onClick={() => setError("")}>×</button></div>}
        {page === "scenarios" && <ScenarioPage projects={projects} busy={busy} onRun={runScenario} onSelect={(id) => { setProjectId(id); setPage("dashboard"); }} onReset={resetProject} />}
        {page === "dashboard" && <DashboardPage snapshot={snapshot} events={events} onGoApprovals={() => setPage("approvals")} />}
        {page === "agents" && <AgentsPage snapshot={snapshot} events={events} />}
        {page === "plans" && <PlansPage snapshot={snapshot} />}
        {page === "approvals" && <ApprovalsPage approvals={currentApprovals} snapshot={snapshot} busy={busy} onDecide={decide} />}
        {page === "audit" && <AuditPage events={events} audit={audit} snapshot={snapshot} />}
      </main>
      {toast && <div className="toast">✓ &nbsp;{toast}</div>}
      {busy && <div className="busy-indicator"><span />处理中</div>}
      {!selectedProject && page !== "scenarios" && <div className="empty-overlay"><div className="empty-card"><span className="empty-symbol">◫</span><h2>先启动一个演示场景</h2><p>选择正常订单或缺料协作，生成带完整审计时间线的合成项目。</p><button className="button primary" onClick={() => setPage("scenarios")}>选择场景</button></div></div>}
    </div>
  );
}

function PageHeading({ eyebrow, title, subtitle, action }: { eyebrow: string; title: string; subtitle: string; action?: ReactNode }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>{action && <div>{action}</div>}</div>;
}

function ScenarioPage({ projects, busy, onRun, onSelect, onReset }: { projects: Project[]; busy: boolean; onRun: (name: string) => void; onSelect: (id: string) => void; onReset: () => void }) {
  return <div className="page-content">
    <PageHeading eyebrow="演示控制台" title="选择一个业务场景" subtitle="每次运行使用固定种子的合成数据。成本与 ETA 使用页面中可追溯的演示规则。" />
    <div className="scenario-grid">
      <article className="scenario-card normal-scenario">
        <div className="scenario-topline"><span className="scenario-icon blue">↗</span><span className="scenario-chip">主业务链</span></div>
        <h2>正常订单</h2><p>从询价与报价审批开始，经过订单发布、并行协作、质量与资料双门禁，完成发运和签收归档。</p>
        <div className="scenario-flow"><span>RFQ</span><b>→</b><span>报价</span><b>→</b><span>订单</span><b>→</b><span>发运</span></div>
        <button className="button primary" disabled={busy} onClick={() => onRun("normal_order")}>运行正常订单 <span>→</span></button>
      </article>
      <article className="scenario-card shortage-scenario">
        <div className="scenario-topline"><span className="scenario-icon amber">!</span><span className="scenario-chip amber-chip">异常协作链</span></div>
        <h2>关键物料缺料</h2><p>从正式物料需求识别净缺口，跟单按需召集采购，采购再请求报价评估成本，人工选择后生成 PO 草稿并重算 ETA。</p>
        <div className="scenario-flow"><span>缺料</span><b>→</b><span>采购</span><b>→</b><span>报价评估</span><b>→</b><span>新 ETA</span></div>
        <button className="button dark" disabled={busy} onClick={() => onRun("material_shortage")}>运行缺料协作 <span>→</span></button>
      </article>
    </div>
    <section className="section-block">
      <div className="section-title"><div><div className="eyebrow">本地演示记录</div><h2>最近项目</h2></div><span className="subtle">{projects.length} 个项目</span></div>
      {projects.length === 0 ? <div className="empty-state">还没有运行记录。先启动一个场景。</div> : <div className="project-list">
        {projects.map((project) => <button className="project-row" key={project.project_id} onClick={() => onSelect(project.project_id)}>
          <span className="project-avatar">{project.scenario === "normal_order" ? "正" : "缺"}</span><span className="project-main"><strong>{project.product_id}</strong><small>{project.project_id} · {project.customer_id}</small></span>
          <span className={`badge ${project.lifecycle_state === "DELIVERED" ? "green" : "blue-badge"}`}>{project.lifecycle_state}</span><span className="row-chevron">›</span>
        </button>)}
      </div>}
    </section>
    {projects.length > 0 && <div className="reset-row"><span>复位会清理所选项目的事件、审批、回放和审计演示数据。</span><button className="button ghost" onClick={onReset}>复位当前项目</button></div>}
  </div>;
}

function DashboardPage({ snapshot, events, onGoApprovals }: { snapshot: Snapshot | null; events: Event[]; onGoApprovals: () => void }) {
  if (!snapshot) return <EmptyPage title="没有选中的项目" />;
  const cases = (snapshot.cases ?? []) as Case[];
  const gates = snapshot.gates ?? {};
  const risk = snapshot.delivery_risk;
  return <div className="page-content">
    <PageHeading eyebrow="全局协同驾驶舱" title={snapshot.product_name ?? snapshot.product_id} subtitle={`${snapshot.project_id} · ${snapshot.customer_id} · correlation ${snapshot.correlation_id}`} action={<span className="badge synthetic">合成演示数据</span>} />
    <div className="kpi-grid">
      <Kpi label="生命周期" value={snapshot.lifecycle_state ?? "进行中"} icon="◷" tone="blue" />
      <Kpi label="在途事件" value={events.length} icon="⌁" tone="purple" />
      <Kpi label="待审批" value={snapshot.pending_approvals?.length ?? 0} icon="✓" tone="amber" onClick={onGoApprovals} />
      <Kpi label="交付风险" value={risk === undefined ? "待评估" : risk ? "需关注" : "当前可控"} icon="△" tone={risk ? "red" : "green"} />
    </div>
    <div className="dashboard-grid">
      <section className="panel timeline-panel"><div className="panel-heading"><div><h2>订单主线</h2><p>按业务事件查看当前推进情况</p></div><span className="live-tag"><i />实时</span></div><EventTimeline events={events} /></section>
      <section className="panel"><div className="panel-heading"><div><h2>双层状态</h2><p>操作状态与业务状态分别记录</p></div></div>
        <div className="case-list">{cases.map((item) => <CaseCard key={item.case_id} item={item} />)}</div>
      </section>
      <section className="panel collab-panel"><div className="panel-heading"><div><h2>动态协作拓扑</h2><p>Leader 与 Partner 随事件轮次变化</p></div></div>
        {(snapshot.collaboration_rounds ?? []).length === 0 ? <div className="empty-state compact">场景启动后显示实际协作轮次。</div> : <div className="round-list">{snapshot.collaboration_rounds.map((round: any) => <div className="round-row" key={round.round}><div className="round-number">0{round.round}</div><div className="round-detail"><div><AgentPill type={round.leader} /> <span className="role-label">Leader</span><span className="arrow">→</span><AgentPill type={round.partner} /> <span className="role-label">Partner</span></div><small>{round.capability}</small></div></div>)}</div>}
      </section>
      <section className="panel gate-panel"><div className="panel-heading"><div><h2>发运双门禁</h2><p>两个独立事实必须同时满足</p></div></div><Gate label="权威质量放行" value={gates.quality_released} /><Gate label="资料包人工批准" value={gates.document_package_approved} /><div className={`gate-result ${gates.quality_released && gates.document_package_approved ? "pass" : "blocked"}`}>{gates.quality_released && gates.document_package_approved ? "可以提交发运审批" : "门禁未齐，不进入发运审批"}</div></section>
    </div>
    <div className="demo-note"><span>ⓘ</span><div><strong>演示数据提示</strong><p>{snapshot.demo_notice}</p></div></div>
  </div>;
}

function AgentsPage({ snapshot, events }: { snapshot: Snapshot | null; events: Event[] }) {
  if (!snapshot) return <EmptyPage title="Agent 工作台" />;
  const cases = (snapshot.cases ?? []) as Case[];
  const latestPlan = snapshot.quote ?? snapshot.supply_options?.[0];
  return <div className="page-content">
    <PageHeading eyebrow="Agent 工作台" title="四个独立业务身份" subtitle="每个 Agent 只执行自己的白名单能力，跨 Agent 协作通过事件记录和动态协议角色呈现。" />
    <div className="agent-grid">{Object.entries(agentNames).map(([type, name]) => {
      const item = cases.find((current) => current.agent_type === type);
      const lastEvent = [...events].reverse().find((event) => event.source_agent === type || event.target_agent === type);
      return <article className="agent-card" key={type}><div className={`agent-avatar agent-${type}`}>{type === "quotation" ? "报" : type === "procurement" ? "采" : type === "tracking" ? "跟" : "质"}</div><div className="agent-title"><h3>{name}</h3><span className="agent-id">{type}-agent</span></div><div className="status-pair"><span><small>操作状态</small><b>{item?.operational_state ?? "READY"}</b></span><span><small>业务状态</small><b>{item?.business_state ?? "等待任务"}</b></span></div><p className="agent-objective">{item?.objective ?? "收到相关事件后按需参与，不主动空转。"}</p><div className="agent-last"><span>最近事件</span><b>{lastEvent ? eventNames[lastEvent.event_type] ?? lastEvent.event_type : "暂无"}</b></div></article>;
    })}</div>
    <section className="panel work-detail"><div className="panel-heading"><div><h2>当前计划与证据</h2><p>模型建议、Mock 数据和权威事实保持区分</p></div></div>
      {latestPlan ? <div className="detail-columns"><div><small>计划输出</small><pre>{JSON.stringify(latestPlan, null, 2)}</pre></div><div><small>数据源边界</small><p>当前环境为固定回放。ERP 与 MES 状态以 fixture 标明的 Mock 权威事件呈现；场景计算规则只用于演示。</p><div className="evidence-chips">{(snapshot.net_requirement?.evidence_ids ?? snapshot.quote?.evidence_ids ?? []).map((item: string) => <span key={item}>{item}</span>)}</div></div></div> : <div className="empty-state compact">尚无计划输出。</div>}
    </section>
  </div>;
}

function PlansPage({ snapshot }: { snapshot: Snapshot | null }) {
  if (!snapshot) return <EmptyPage title="方案对比" />;
  const options = snapshot.supply_options ?? [];
  return <div className="page-content">
    <PageHeading eyebrow="方案对比" title={options.length ? "缺料供应方案" : "报价与交期假设"} subtitle="显示成本、日期、风险和计算规则版本；选择供应商和执行草稿仍由人工审批。" />
    {options.length ? <div className="panel table-panel"><div className="panel-heading"><div><h2>供应方案比较</h2><p>缺口 {snapshot.net_requirement?.net_requirement} · 物料需求日 {snapshot.material_demand?.required_date}</p></div><span className="badge synthetic">固定演示公式</span></div><div className="table-wrap"><table><thead><tr><th>方案 / 供应商</th><th>缺口数量</th><th>单价</th><th>相对已接受报价影响</th><th>确认到货</th><th>质量风险</th><th>满足物料日</th></tr></thead><tbody>{options.map((option: any) => <tr key={option.option_id}><td><strong>{option.supplier_name}</strong><small>{option.option_id}</small></td><td>{snapshot.net_requirement?.net_requirement}</td><td>¥{option.unit_price}</td><td className={Number(option.cost_assessment.delta_for_shortage) > 0 ? "price-up" : "price-down"}>{Number(option.cost_assessment.delta_for_shortage) > 0 ? "+" : ""}¥{option.cost_assessment.delta_for_shortage}</td><td>{option.confirmed_delivery_date}<small>ETA {option.eta.eta_date}</small></td><td><RiskTag value={option.quality_risk} /></td><td><span className={`boolean ${option.meets_material_required_date ? "yes" : "no"}`}>{option.meets_material_required_date ? "符合" : "晚于需求日"}</span></td></tr>)}</tbody></table></div><div className="formula-note"><b>规则 {options[0]?.eta?.rule_version}</b><span>净需求 = 正式需求 − 合格可用库存 − 已确认在途；成本差额 = 缺口数量 ×（方案单价 − 已接受报价基准价）；ETA 使用日历天。</span></div></div> : <><div className="metric-row"><Kpi label="建议单位成本" value={`¥${snapshot.quote?.unit_cost ?? "—"}`} icon="¥" tone="blue" /><Kpi label="演示建议单价" value={`¥${snapshot.quote?.suggested_unit_price ?? "—"}`} icon="↗" tone="purple" /><Kpi label="毛利率" value={`${Number(snapshot.quote?.actual_gross_margin ?? 0) * 100}%`} icon="%" tone="green" /><Kpi label="预计 ETA" value={snapshot.eta?.eta_date ?? "—"} icon="◷" tone="amber" /></div><div className="formula-note wide"><b>{snapshot.quote?.rule_version ?? "演示规则"}</b><span>{snapshot.quote?.calculation_note ?? "报价成本和 ETA 仅基于场景 fixture 中标明的合成数据。"}</span></div></>}
    <div className="policy-card"><span>⌘</span><div><strong>订单期成本评估独立于已接受报价</strong><p>缺料场景使用单独的 cost_assessment_id 记录新增成本影响。供应方案审批不会修改原报价状态。</p></div><Badge value={snapshot.cost_assessment?.quote_status ?? snapshot.quote_status ?? "待报价"} /></div>
  </div>;
}

function ApprovalsPage({ approvals, snapshot, busy, onDecide }: { approvals: Approval[]; snapshot: Snapshot | null; busy: boolean; onDecide: (id: string, decision: "approve" | "reject" | "request-data", body?: any) => void }) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [requestText, setRequestText] = useState<Record<string, string>>({});
  return <div className="page-content">
    <PageHeading eyebrow="人工审批箱" title="需要人工决定的动作" subtitle="每张审批绑定对象版本、快照哈希与规则版本；执行前会再次检查版本。" action={<span className="badge amber-chip">{approvals.length} 项待处理</span>} />
    {approvals.length === 0 ? <div className="empty-state large"><span>✓</span><h3>当前没有待审批项</h3><p>{snapshot ? "后续需要人工决定的方案会显示在这里。" : "先启动演示场景。"}</p></div> : <div className="approval-list">{approvals.map((item) => {
      const payload = item.action_payload_json ?? {};
      const options = payload.available_option_ids ?? [];
      const selectedOption = selected[item.approval_id] ?? "";
      const needsChoice = item.action_type === "supplier_selection";
      const optionRows = snapshot?.supply_options ?? [];
      return <article className="approval-card" key={item.approval_id}><div className="approval-card-top"><div><span className="approval-icon">✓</span><span className="eyebrow">{actionName(item.action_type)}</span></div><span className="badge amber-chip">等待人工批准</span></div><h2>{item.object_id}</h2><p className="approval-impact">{approvalImpact(item, snapshot)}</p>
        {needsChoice && <div className="choice-list">{optionRows.filter((option: any) => options.includes(option.option_id)).map((option: any) => <label className={`choice-row ${selectedOption === option.option_id ? "chosen" : ""}`} key={option.option_id}><input type="radio" name={item.approval_id} value={option.option_id} checked={selectedOption === option.option_id} onChange={() => setSelected((current) => ({ ...current, [item.approval_id]: option.option_id }))} /><span className="choice-body"><strong>{option.supplier_name}</strong><small>¥{option.unit_price}/单位 · ETA {option.eta.eta_date} · 成本差额 ¥{option.cost_assessment.delta_for_shortage}</small></span><span className={`badge ${option.quality_risk === "low" ? "green" : "amber-chip"}`}>{option.quality_risk === "low" ? "低质量风险" : "中质量风险"}</span></label>)}</div>}
        <div className="approval-meta"><div><small>对象版本</small><b>v{item.object_version}</b></div><div><small>规则版本</small><b>{item.rule_version}</b></div><div className="hash"><small>快照哈希</small><code>{item.snapshot_hash.slice(0, 22)}…</code></div></div>
        <details className="approval-details"><summary>查看计划和证据</summary><p>计划 ID：{payload.plan_id}</p><p>输入快照：{payload.input_hash}</p><p>使用证据：{(snapshot?.net_requirement?.evidence_ids ?? snapshot?.quote?.evidence_ids ?? []).join("、") || "Mock 场景证据"}</p></details>
        <div className="request-data-row"><input value={requestText[item.approval_id] ?? ""} onChange={(event) => setRequestText((current) => ({ ...current, [item.approval_id]: event.target.value }))} placeholder="补资料说明（可选）" /><button className="button ghost" disabled={busy} onClick={() => onDecide(item.approval_id, "request-data", { requested_fields: ["补充资料"], message: requestText[item.approval_id] || "请补充审批所需资料。" })}>请求补资料</button></div>
        <div className="approval-actions"><button className="button danger-ghost" disabled={busy} onClick={() => onDecide(item.approval_id, "reject")}>驳回</button><button className="button primary" disabled={busy || (needsChoice && !selectedOption)} onClick={() => onDecide(item.approval_id, "approve", needsChoice ? { selected_option_id: selectedOption } : {})}>批准并继续 <span>→</span></button></div>
      </article>;
    })}</div>}
  </div>;
}

function AuditPage({ events, audit, snapshot }: { events: Event[]; audit: any[]; snapshot: Snapshot | null }) {
  const traceId = snapshot?.trace_id;
  return <div className="page-content">
    <PageHeading eyebrow="审计与追溯" title="完整的事件因果链" subtitle="按 correlation_id 查看业务事实，再按 trace_id 关联协作、工具、审批和回放。" action={<div className="trace-chip">trace_id <code>{traceId ?? "—"}</code></div>} />
    <div className="audit-stats"><Kpi label="业务事件" value={events.length} icon="⌁" tone="blue" /><Kpi label="审计记录" value={audit.length} icon="▤" tone="purple" /><Kpi label="审批快照" value={snapshot?.pending_approvals?.length ?? 0} icon="✓" tone="amber" /></div>
    <div className="audit-grid"><section className="panel"><div className="panel-heading"><div><h2>事件与证据</h2><p>事实只追加，不覆盖</p></div></div><EventTimeline events={events} expanded /></section><section className="panel"><div className="panel-heading"><div><h2>运行审计</h2><p>工具输入输出使用 SHA-256 摘要</p></div></div>{audit.length === 0 ? <div className="empty-state compact">暂无审计动作</div> : <div className="audit-list">{audit.map((item) => <div className="audit-row" key={item.audit_id}><div className="audit-check">✓</div><div><strong>{item.action}</strong><small>{item.actor} · {new Date(item.created_at).toLocaleString("zh-CN")}</small><code>in {item.input_hash?.slice(0, 18)}…</code><code>out {item.output_hash?.slice(0, 18)}…</code></div><Badge value={item.result} /></div>)}</div>}</section></div>
  </div>;
}

function EventTimeline({ events, expanded = false }: { events: Event[]; expanded?: boolean }) {
  if (!events.length) return <div className="empty-state compact">等待首条事件</div>;
  return <div className={`event-list ${expanded ? "expanded" : ""}`}>{events.map((event) => <div className="event-row" key={event.event_id}><div className="event-rail"><span className={`event-dot ${event.event_type.includes("RISK") || event.event_type.includes("SHORTAGE") ? "risk" : event.event_type.includes("APPROVED") || event.event_type === "QUALITY_RELEASED" ? "success" : ""}`} /><i /></div><div className="event-copy"><div className="event-title"><strong>{eventNames[event.event_type] ?? event.event_type}</strong><time>{new Date(event.occurred_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time></div><div className="event-subline"><code>{event.event_id}</code><span>{event.source_agent ? agentNames[event.source_agent] : "Mock 系统 / 演示操作"}{event.target_agent ? ` → ${agentNames[event.target_agent]}` : ""}</span></div>{expanded && <><p>{JSON.stringify(event.payload)}</p><div className="evidence-chips">{event.evidence_ids?.map((item) => <span key={item}>{item}</span>)}</div></>}</div></div>)}</div>;
}

function CaseCard({ item }: { item: Case }) {
  return <div className="case-row"><AgentPill type={item.agent_type} /><div className="case-state"><strong>{agentNames[item.agent_type]}</strong><small>v{item.version} · {item.business_state}</small></div><Badge value={item.operational_state} /></div>;
}

function AgentPill({ type }: { type: string }) {
  return <span className={`agent-pill pill-${type}`}>{agentNames[type] ?? type}</span>;
}

function Gate({ label, value }: { label: string; value: boolean }) {
  return <div className="gate-row"><span className={`gate-check ${value ? "on" : ""}`}>{value ? "✓" : "·"}</span><span>{label}</span><b className={value ? "text-green" : "text-muted"}>{value ? "已满足" : "等待"}</b></div>;
}

function Kpi({ label, value, icon, tone, onClick }: { label: string; value: string | number; icon: string; tone: string; onClick?: () => void }) {
  return <button className={`kpi-card ${onClick ? "clickable" : ""}`} onClick={onClick}><span className={`kpi-icon ${tone}`}>{icon}</span><span className="kpi-label">{label}</span><strong>{value}</strong></button>;
}

function Badge({ value }: { value: string }) {
  const lower = value.toLowerCase();
  const tone = ["approved", "success", "done", "completed", "delivered", "closed", "archived"].some((item) => lower.includes(item)) ? "green" : ["exception", "rejected", "blocked", "risk"].some((item) => lower.includes(item)) ? "red-badge" : ["pending", "approval", "awaiting", "waiting"].some((item) => lower.includes(item)) ? "amber-chip" : "blue-badge";
  return <span className={`badge ${tone}`}>{value}</span>;
}

function RiskTag({ value }: { value: string }) {
  return <span className={`risk-tag ${value === "low" ? "low" : "medium"}`}>{value === "low" ? "低" : value === "medium" ? "中" : value}</span>;
}

function EmptyPage({ title }: { title: string }) {
  return <div className="page-content"><PageHeading eyebrow="工作空间" title={title} subtitle="选择一个本地演示项目后，这里会显示对应内容。" /><div className="empty-state large"><span>◫</span><h3>还没有项目</h3><p>进入场景选择，启动一条合成业务链。</p></div></div>;
}

function actionName(type: string) {
  return ({ quote_approval: "报价批准", sales_order_release: "销售订单发布", supplier_selection: "供应商方案选择", po_draft_approval: "PO 草稿审批", document_package_approval: "质量资料包审核", shipment_release: "发运审批" } as Record<string, string>)[type] ?? type;
}

function approvalImpact(item: Approval, snapshot: Snapshot | null) {
  if (item.action_type === "quote_approval") return `建议单价 ¥${snapshot?.quote?.suggested_unit_price ?? "—"}，订单量 ${snapshot?.quote?.order_quantity ?? "—"}。批准后才进入销售订单发布审批。`;
  if (item.action_type === "supplier_selection") return `需要从两种合成供应方案中人工选择。缺口 ${snapshot?.net_requirement?.net_requirement ?? "—"}；原报价状态保持 ${snapshot?.cost_assessment?.quote_status ?? "ACCEPTED"}。`;
  if (item.action_type === "po_draft_approval") return "确认所选方案的 MockERP 采购订单草稿。审批后会记录供应商到货日并重算 ETA。";
  if (item.action_type === "document_package_approval") return "确认资料包清单和证据完整性。批准资料包不会产生 QUALITY_RELEASED，也不能代替 MockMES 质量放行。";
  if (item.action_type === "shipment_release") return "发运前会重新检查 MockMES 质量放行和人工批准的资料包两个门禁。";
  return "该操作将按当前对象版本在本地 Mock 环境继续场景。";
}

export default App;
