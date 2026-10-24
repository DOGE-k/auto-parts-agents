// ERP 销售订单全量列表（只读展示数据规模与状态分布；写入仍走报价-审批流程）。
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { displaySystemText } from "../lib/system-labels";

type SalesOrderRow = {
  order_id: string;
  customer_name: string;
  transaction_date: string;
  delivery_date: string;
  status: string;
  currency: string;
  total: string;
  total_qty: string;
};

const statusTone = (status: string): string => {
  if (status === "Draft") return "badge amber-chip";
  if (status === "Completed") return "badge green";
  if (status.startsWith("To ")) return "badge blue-badge";
  return "badge";
};

const statusLabel = (status: string): string => {
  if (status === "Draft") return "草稿";
  if (status === "To Deliver and Bill") return "待发货开票";
  if (status === "To Bill") return "待开票";
  if (status === "To Deliver") return "待发货";
  if (status === "Completed") return "已完成";
  if (status === "Cancelled") return "已取消";
  return status;
};

export default function SalesOrdersTable() {
  const [orders, setOrders] = useState<SalesOrderRow[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<{ orders: SalesOrderRow[] }>("/real-orders/erp/sales-orders?limit=50");
      setOrders(data.orders ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "销售订单加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>ERP 销售订单</h2>
          <p>来自 ERPNext 的真实订单记录（最近 50 条）；草稿单仍需走报价审批后写入。</p>
        </div>
        <div className="panel-heading-actions">
          <span className="badge blue-badge">{orders?.length ?? "…"} 张</span>
          <button className="button ghost" disabled={loading} onClick={() => void load()}>↻ 刷新</button>
        </div>
      </div>
      {error ? (
        <div className="error-banner"><span>订单加载失败</span> {displaySystemText(error)}<button onClick={() => setError("")}>×</button></div>
      ) : orders === null ? (
        <div className="empty-state compact">加载中…</div>
      ) : orders.length === 0 ? (
        <div className="empty-state compact">ERP 中暂无销售订单记录。</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>订单号</th>
                <th>客户</th>
                <th>下单日期</th>
                <th>交期</th>
                <th>数量</th>
                <th>金额</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td><strong>{o.order_id}</strong></td>
                  <td>{o.customer_name || "—"}</td>
                  <td>{o.transaction_date || "—"}</td>
                  <td>{o.delivery_date || "—"}</td>
                  <td>{Number(o.total_qty || 0).toLocaleString()}</td>
                  <td>{o.currency || "CNY"} {Number(o.total || 0).toLocaleString()}</td>
                  <td><span className={statusTone(o.status)}>{statusLabel(o.status)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
