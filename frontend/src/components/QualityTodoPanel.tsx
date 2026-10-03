// 阶段九：跨工单质量待办面板（从 RealBusinessPage 抽出，阶段十 10.5）
import type { QualityTodoItem } from "../api";

type Props = {
  hasOpenmesSession: boolean;
  qualityTodo: QualityTodoItem[] | null;
  qualityTodoLoading: boolean;
  qualityTodoError: string;
  busy: boolean;
  onRefresh: () => void;
  onGoDispose: (item: QualityTodoItem) => void;
};

export default function QualityTodoPanel({
  hasOpenmesSession,
  qualityTodo,
  qualityTodoLoading,
  qualityTodoError,
  busy,
  onRefresh,
  onGoDispose,
}: Props) {
  return (
    <section className="panel quality-todo-panel">
      <div className="panel-heading">
        <div>
          <h2>质量待办</h2>
          <p>跨工单的未关闭质量问题队列（OpenMES 真实记录，OPEN/ACKNOWLEDGED/RESOLVED 三态，CLOSED 不进待办）。点击"去处置"切换到订单流程页签中该工单的 NCR 审批处置面板。</p>
        </div>
        {hasOpenmesSession && (
          <button className="button ghost" onClick={onRefresh} disabled={qualityTodoLoading}>
            {qualityTodoLoading ? "加载中…" : "↻ 刷新待办"}
          </button>
        )}
      </div>
      {!hasOpenmesSession && (
        <p className="quality-todo-hint">
          登录后查看质量待办——请到左侧「系统连接」用 OpenMES 账号建立短期会话（仅当前浏览器会话生效）。
        </p>
      )}
      {hasOpenmesSession && qualityTodoError && (
        <div className="quality-todo-error">
          <strong>加载失败</strong> {qualityTodoError}
          <button className="button ghost" onClick={onRefresh}>重试</button>
        </div>
      )}
      {hasOpenmesSession && !qualityTodoError && qualityTodo && qualityTodo.length === 0 && (
        <p className="quality-todo-hint">当前没有未关闭质量问题。</p>
      )}
      {hasOpenmesSession && qualityTodo && qualityTodo.length > 0 && (
        <table className="quality-todo-table">
          <thead>
            <tr>
              <th>工单号</th>
              <th>标题</th>
              <th>严重度</th>
              <th>状态</th>
              <th>处置</th>
              <th>已报告</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {qualityTodo.map((item) => (
              <tr key={item.issue_id}>
                <td>{item.work_order_no || `#${item.work_order_id}`}</td>
                <td>{item.title}</td>
                <td>{item.severity || "—"}</td>
                <td>{item.status}</td>
                <td>{item.disposition || "未处置"}</td>
                <td className={typeof item.reported_days === "number" && item.reported_days > 3 ? "todo-overdue" : ""}>
                  {typeof item.reported_days === "number" ? `${item.reported_days} 天` : "—"}
                </td>
                <td>
                  <button className="button ghost" onClick={() => onGoDispose(item)} disabled={busy}>
                    去处置 →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
