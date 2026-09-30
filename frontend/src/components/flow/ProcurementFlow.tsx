// 采购环节面板（步骤 4-6：采购分析 / 方案审批 / PO 草稿；阶段十 10.5 从 RealBusinessPage 抽出）
import type { ProcurementPlan, Quotation } from "../../types/realBusiness";

export function ProcurementAnalyzePanel({
  step,
  quotation,
  plan,
  loading,
  selectedOptionId,
  onSelectOption,
  onAnalyze,
}: {
  step: number;
  quotation: Quotation;
  plan: ProcurementPlan | null;
  loading: boolean;
  selectedOptionId: string;
  onSelectOption: (optionId: string) => void;
  onAnalyze: () => void;
}) {
  return (
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
              onClick={onAnalyze}
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
                      onChange={() => onSelectOption(opt.option_id)}
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
  );
}

export function PlanApprovalPanel({
  plan,
  loading,
  hasSelectedOption,
  onApprove,
}: {
  plan: ProcurementPlan;
  loading: boolean;
  hasSelectedOption: boolean;
  onApprove: (approved: boolean) => void;
}) {
  return (
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
          onClick={() => onApprove(false)}
          disabled={loading || !hasSelectedOption}
        >
          驳回
        </button>
        <button
          className="button primary"
          onClick={() => onApprove(true)}
          disabled={loading || !hasSelectedOption}
        >
          {loading ? "处理中..." : "批准选中方案 →"}
        </button>
      </div>
    </section>
  );
}

export function PoDraftPanel({
  step,
  plan,
  loading,
  onCreatePo,
}: {
  step: number;
  plan: ProcurementPlan;
  loading: boolean;
  onCreatePo: () => void;
}) {
  return (
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
            onClick={onCreatePo}
            disabled={loading || plan.status !== "APPROVED"}
          >
            {loading ? "创建中..." : "创建 ERP 采购订单草稿 →"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
