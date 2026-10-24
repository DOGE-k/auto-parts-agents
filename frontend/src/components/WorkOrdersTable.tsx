// OpenMES 生产工单全量列表（只读；完工/生产中/待产状态与交期一览）。
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { displaySystemText } from "../lib/system-labels";
import type { WorkOrder } from "../types/realBusiness";

const statusTone = (status: string): string => {
  const s = status.toUpperCase();
  if (s === "DONE" || s === "COMPLETED") return "badge green";
  if (s === "IN_PROGRESS") return "badge blue-badge";
  if (s === "CANCELLED" || s === "CLOSED") return "badge red-badge";
  return "badge";
};

const statusLabel = (status: string): string => {
  const s = status.toUpperCase();
  if (s === "DONE" || s === "COMPLETED") return "已完工";
  if (s === "IN_PROGRESS") return "生产中";
  if (s === "PENDING") return "待产";
  if (s === "CANCELLED") return "已取消";
  if (s === "CLOSED") return "已关闭";
  return status;
};

export default function WorkOrdersTable() {
  const [orders, setOrders] = useState<WorkOrder[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await api<WorkOrder[]>("/mes/work-orders?limit=20");
      setOrders(Array.isArray(rows) ? rows : []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "工单加载失败");
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
          <h2>生产工单</h2>
          <p>来自 OpenMES 的真实工单（最近 20 条）；关联 ERP 销售订单号精确匹配。</p>
        </div>
        <div className="panel-heading-actions">
          <span className="badge blue-badge">{orders?.length ?? "…"} 张</span>
          <button className="button ghost" disabled={loading} onClick={() => void load()}>↻ 刷新</button>
        </div>
      </div>
      {error ? (
        <div className="error-banner"><span>工单加载失败</span> {displaySystemText(error)}<button onClick={() => setError("")}>×</button></div>
      ) : orders === null ? (
        <div className="empty-state compact">加载中…</div>
      ) : orders.length === 0 ? (
        <div className="empty-state compact">MES 中暂无工单记录。</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>工单号</th>
                <th>关联销售订单</th>
                <th>产品</th>
                <th>产线</th>
                <th>数量</th>
                <th>状态</th>
                <th>交期</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((w) => (
                <tr key={w.work_order_id}>
                  <td><strong>{w.work_order_no}</strong></td>
                  <td>{w.customer_order_no || <span className="text-faint">未关联</span>}</td>
                  <td>{w.product_name || w.product_id || "—"}</td>
                  <td>{w.line_name || "—"}</td>
                  <td>{Number(w.quantity || 0).toLocaleString()}</td>
                  <td><span className={statusTone(w.status)}>{statusLabel(w.status)}</span></td>
                  <td>{w.due_date?.slice(0, 10) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
