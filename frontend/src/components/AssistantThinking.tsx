// 协调者思考过程块（harness 改造步骤 B）：deepseek-flash 混合推理模型的
// reasoning_content 原文展示——加载中逐轮滑出（live），完成后折叠保留。
// 数据全部来自后端 reasoning_rounds 真实透传，前端不改写不截断。
export type ReasoningRound = { round: number; text: string };

export default function AssistantThinking({
  rounds,
  live = false,
}: {
  rounds: ReasoningRound[];
  live?: boolean;
}) {
  if (!rounds.length) return null;
  if (live) {
    return (
      <div className="assistant-thinking live">
        {rounds.map((r) => (
          <details key={r.round} open className="assistant-thinking-round">
            <summary>
              <span className="assistant-thinking-dot" aria-hidden="true" />
              思考过程 · 第 {r.round} 轮
            </summary>
            <p>{r.text}</p>
          </details>
        ))}
      </div>
    );
  }
  return (
    <details className="assistant-thinking">
      <summary>
        思考过程（{rounds.length} 轮，模型真实推理原文，点击展开）
      </summary>
      {rounds.map((r) => (
        <div key={r.round} className="assistant-thinking-round">
          <small>第 {r.round} 轮</small>
          <p>{r.text}</p>
        </div>
      ))}
    </details>
  );
}
