// 近 7 日协同调用趋势（前端优化清单任务4）：真实 agent_runs 按天分桶，
// 手写 SVG 柱图（工业蓝 token），不引入图表库；不足 200 条口径如实标注。
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

type RunRow = { started_at: string | null };

type DayBucket = { key: string; label: string; count: number };

const localDayKey = (iso: string): string => {
  // 后端存 UTC 时间（无时区后缀）；按本地时区归到正确的一天
  const withZone = /[zZ]$|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(withZone);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export default function AgentRunsTrend() {
  const [days, setDays] = useState<DayBucket[] | null>(null);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const runs = await api<RunRow[]>("/real-orders/agent-runs?limit=200");
      const rows = Array.isArray(runs) ? runs : [];
      setTruncated(rows.length >= 200);
      const buckets = new Map<string, number>();
      const labels: { key: string; label: string }[] = [];
      for (let i = 6; i >= 0; i--) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        buckets.set(key, 0);
        labels.push({ key, label: `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}` });
      }
      let count7 = 0;
      for (const r of rows) {
        if (!r.started_at) continue;
        const key = localDayKey(r.started_at);
        if (buckets.has(key)) {
          buckets.set(key, (buckets.get(key) ?? 0) + 1);
          count7 += 1;
        }
      }
      setDays(labels.map((l) => ({ ...l, count: buckets.get(l.key) ?? 0 })));
      setTotal(count7);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "趋势数据加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 手写 SVG 柱图：7 根柱，高度按 count/max 比例
  const W = 300;
  const H = 104;
  const labelH = 18;
  const plotH = H - labelH;
  const gap = 10;
  const barW = (W - gap * 8) / 7;
  const max = Math.max(1, ...(days ?? []).map((d) => d.count));
  const todayKey = (() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  })();

  return (
    <section className="panel trend-panel">
      <div className="panel-heading">
        <div>
          <h2>近 7 日协同调用</h2>
          <p>真实智能体运行记录按天统计{truncated ? "（仅取最近 200 条）" : ""}。</p>
        </div>
        <span className="badge blue-badge">{days === null ? "…" : `${total} 次`}</span>
      </div>
      {error ? (
        <div className="error-banner"><span>趋势加载失败</span> {error}</div>
      ) : days === null ? (
        <div className="empty-state compact">加载中…</div>
      ) : total === 0 ? (
        <div className="empty-state compact">近 7 日暂无调用记录。去「AI 协同问答」提一个问题试试。</div>
      ) : (
        <svg
          className="trend-chart"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`近 7 日协同调用共 ${total} 次`}
        >
          {days.map((d, i) => {
            const x = gap + i * (barW + gap);
            const h = Math.round((d.count / max) * (plotH - 18));
            const y = plotH - h;
            const isToday = d.key === todayKey;
            return (
              <g key={d.key}>
                {d.count > 0 && (
                  <text x={x + barW / 2} y={y - 5} textAnchor="middle" className="trend-count">{d.count}</text>
                )}
                <rect
                  x={x}
                  y={d.count > 0 ? y : plotH - 2}
                  width={barW}
                  height={d.count > 0 ? h : 2}
                  rx="2.5"
                  className={isToday ? "trend-bar today" : "trend-bar"}
                />
                <text x={x + barW / 2} y={H - 4} textAnchor="middle" className="trend-label">{d.label}</text>
              </g>
            );
          })}
        </svg>
      )}
    </section>
  );
}
