// MES 完工数据面板（信息架构改版 §七.6：原独立左侧入口并入「生产跟单」模块的二级视图）
// 只读 OpenMES production completions；本面板不写入 OpenMES，也不把完工记录混入 Mock 场景。
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ProductionCompletion } from "../api";

export default function MesCompletionsPanel() {
  const [rows, setRows] = useState<ProductionCompletion[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api<{ data: ProductionCompletion[]; meta?: Record<string, unknown> }>(
        "/integrations/openmes/production-completions",
      );
      setRows(result.data ?? []);
      setMeta(result.meta ?? null);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载 OpenMES 完工数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <div>
          <h2>OpenMES 完工记录</h2>
          <p>{meta?.count != null ? `返回 ${meta.count} 条` : `当前 ${rows.length} 条`}</p>
        </div>
        <div className="panel-heading-actions">
          <span className="badge green">只读连接</span>
          <button className="button ghost" disabled={loading} onClick={() => void refresh()}>
            {loading ? "读取中…" : "刷新数据"}
          </button>
        </div>
      </div>
      {error && <div className="error-banner"><span>读取失败</span> {error}<button onClick={() => setError("")}>×</button></div>}
      {rows.length === 0 ? (
        <div className="empty-state large">
          <span>▤</span>
          <h3>OpenMES 暂无完工记录</h3>
          <p>认证和读取接口已经成功；当前系统还没有已完工的生产批次。</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>工单</th><th>产品</th><th>完工数量</th><th>完工时间</th><th>状态</th><th>原始数据</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={String(row.id ?? row.work_order_id ?? row.order_no ?? index)}>
                  <td><strong>{row.order_no ?? row.work_order_no ?? row.id ?? "—"}</strong><small>{row.line?.name ?? row.line_name ?? ""}</small></td>
                  <td>{row.product_name ?? row.product_type?.name ?? row.product_code ?? "—"}</td>
                  <td>{row.produced_quantity ?? row.quantity ?? row.completed_quantity ?? "—"}</td>
                  <td>{row.completed_at ?? row.completed_at_at ?? row.finished_at ?? row.updated_at ?? "—"}</td>
                  <td><span className="badge green">{row.status ?? "completed"}</span></td>
                  <td><details><summary>查看 JSON</summary><pre>{JSON.stringify(row, null, 2)}</pre></details></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
