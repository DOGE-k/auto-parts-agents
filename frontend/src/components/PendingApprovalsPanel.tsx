// 待人工审批面板（评审意见④：审批与审计页应是审批工作台，而不只是运行记录）。
// 数据全部来自真实端点：报价与采购方案的 PENDING_APPROVAL 记录；点击定位到对应业务模块的审批视图。
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ProcurementPlan, Quotation } from "../types/realBusiness";

type Props = {
  onOpenQuotation: (quotationId: string) => void;
  onOpenPlan: (quotationId: string | undefined, planId: string) => void;
};

export default function PendingApprovalsPanel({ onOpenQuotation, onOpenPlan }: Props) {
  const [quotations, setQuotations] = useState<Quotation[] | null>(null);
  const [plans, setPlans] = useState<ProcurementPlan[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [qs, ps] = await Promise.all([
        api<Quotation[]>("/real-orders/quotations"),
        api<ProcurementPlan[]>("/real-orders/procurement/plans"),
      ]);
      setQuotations(qs);
      setPlans(ps);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "待审批数据加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pendingQuotations = (quotations ?? []).filter((q) => q.status === "PENDING_APPROVAL");
  const pendingPlans = (plans ?? []).filter((p) => p.status === "PENDING_APPROVAL");

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>待人工审批</h2>
          <p>停留在审批门禁的报价与供应商方案；批准与执行在对应业务模块完成。</p>
        </div>
        <div className="panel-heading-actions">
          <span className="badge amber-chip">{pendingQuotations.length + pendingPlans.length} 项待处理</span>
          <button className="button ghost" onClick={() => void load()}>↻ 刷新</button>
        </div>
      </div>
      {error && <div className="error-banner"><span>加载失败</span> {error}<button onClick={() => setError("")}>×</button></div>}
      {pendingQuotations.length === 0 && pendingPlans.length === 0 ? (
        <div className="empty-state compact">
          当前没有待审批的报价或采购方案。审批请求产生后会出现在这里。
        </div>
      ) : (
        <div className="attention-list">
          {pendingQuotations.map((q) => (
            <div className="attention-row" key={q.quotation_id}>
              <code>{q.quotation_id}</code>
              <span className="badge type-badge">报价审批</span>
              <span className="attention-meta">
                {q.item?.item_name ?? q.item?.item_code ?? "—"} · 数量 {q.quantity} · {q.total_price ? `${q.total_price} ${q.currency}` : "价格—"}
              </span>
              <button className="button ghost" onClick={() => onOpenQuotation(q.quotation_id)}>
                打开报价审批 →
              </button>
            </div>
          ))}
          {pendingPlans.map((p) => (
            <div className="attention-row" key={p.plan_id}>
              <code>{p.plan_id}</code>
              <span className="badge type-badge">方案审批</span>
              <span className="attention-meta">
                报价 {p.quotation_id} · {p.net_requirement?.shortage_count ?? "?"} 项缺料 · {p.supplier_options?.length ?? "?"} 个供应商选项
              </span>
              <button className="button ghost" onClick={() => onOpenPlan(p.quotation_id, p.plan_id)}>
                打开方案审批 →
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
