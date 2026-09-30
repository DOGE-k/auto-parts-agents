// 报价环节面板（步骤 1-3：报价分析 / 报价审批 / ERP 销售订单草稿；阶段十 10.5 从 RealBusinessPage 抽出）
import type { Customer, Item, Quotation } from "../../types/realBusiness";

export function QuotationInputStep({
  customers,
  items,
  selectedCustomer,
  onSelectCustomer,
  selectedItem,
  onSelectItem,
  quantity,
  onQuantityChange,
  deliveryDate,
  onDeliveryDateChange,
  loading,
  onGenerate,
}: {
  customers: Customer[];
  items: Item[];
  selectedCustomer: string;
  onSelectCustomer: (value: string) => void;
  selectedItem: string;
  onSelectItem: (value: string) => void;
  quantity: number;
  onQuantityChange: (value: number) => void;
  deliveryDate: string;
  onDeliveryDateChange: (value: string) => void;
  loading: boolean;
  onGenerate: () => void;
}) {
  return (
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
            onChange={(e) => onSelectCustomer(e.target.value)}
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
            onChange={(e) => onSelectItem(e.target.value)}
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
            onChange={(e) => onQuantityChange(Number(e.target.value))}
            min={1}
          />
        </div>
        <div className="form-group">
          <label>客户要求交期（必填）</label>
          <input
            type="date"
            value={deliveryDate}
            onChange={(e) => onDeliveryDateChange(e.target.value)}
          />
        </div>
      </div>
      <div className="form-actions">
        <button
          className="button primary"
          onClick={onGenerate}
          disabled={loading || !selectedCustomer || !selectedItem}
        >
          {loading ? "分析中..." : "生成报价方案 →"}
        </button>
      </div>
    </section>
  );
}

export function QuotationReviewPanel({
  step,
  quotation,
  loading,
  onApprove,
}: {
  step: number;
  quotation: Quotation;
  loading: boolean;
  onApprove: (approved: boolean) => void;
}) {
  return (
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
            onClick={() => onApprove(false)}
            disabled={loading}
          >
            驳回
          </button>
          <button
            className="button primary"
            onClick={() => onApprove(true)}
            disabled={loading}
          >
            {loading ? "处理中..." : "批准报价 →"}
          </button>
        </div>
      )}
    </section>
  );
}

export function ErpDraftPanel({
  step,
  quotation,
  loading,
  onCreateDraft,
}: {
  step: number;
  quotation: Quotation;
  loading: boolean;
  onCreateDraft: () => void;
}) {
  return (
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
            onClick={onCreateDraft}
            disabled={loading || quotation.status !== "APPROVED"}
          >
            {loading ? "创建中..." : "创建 ERP 销售订单草稿 →"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
