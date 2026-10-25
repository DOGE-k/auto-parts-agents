// 交错执行时间轴（harness 改造步骤 E）：思考块与调用卡按真实发生顺序交错——
// 每轮思考（reasoning）→ 该轮的智能体调用卡（step）→ … → 最终答案（由面板渲染）。
// 加载态 live：思考块展开、卡片实时点亮；完成态：思考折叠、卡片可展开核对实参与返回原文。
import type { AssistantCallStep } from "../api";
import { agentRunTypeNames } from "../api";
import { displaySystemText } from "../lib/system-labels";
import { summarizeSkillResult } from "../lib/result-summary";
import { skillNames } from "./AssistantCallChain";

export type TimelineItem =
  | { kind: "reasoning"; round: number; text: string }
  | { kind: "step"; step: AssistantCallStep };

/** 把 reasoning_rounds 与 call_chain（步骤带 round）交错成时间轴条目。 */
export function buildTimeline(
  reasoning: { round: number; text: string }[],
  steps: AssistantCallStep[],
): TimelineItem[] {
  const items: TimelineItem[] = [];
  const reasoningByRound = new Map(reasoning.map((r) => [r.round, r]));
  const stepsByRound = new Map<number, AssistantCallStep[]>();
  const legacySteps: AssistantCallStep[] = [];
  for (const step of steps) {
    if (typeof step.round === "number") {
      const bucket = stepsByRound.get(step.round);
      if (bucket) bucket.push(step);
      else stepsByRound.set(step.round, [step]);
    } else {
      legacySteps.push(step);
    }
  }
  const rounds = [...new Set([...reasoningByRound.keys(), ...stepsByRound.keys()])].sort((a, b) => a - b);
  for (const round of rounds) {
    const r = reasoningByRound.get(round);
    if (r) items.push({ kind: "reasoning", round, text: r.text });
    for (const step of stepsByRound.get(round) ?? []) items.push({ kind: "step", step });
  }
  for (const step of legacySteps) items.push({ kind: "step", step });
  return items;
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function StepCard({ step }: { step: AssistantCallStep }) {
  const skillLabel = skillNames[step.skill_id] ?? step.skill_id;
  const calleeName = displaySystemText(agentRunTypeNames[step.callee] ?? step.callee);
  const readable =
    step.status === "ok" ? summarizeSkillResult(step.skill_id, step.result_summary) : null;
  const args = Object.entries(step.arguments ?? {});
  return (
    <details className={`timeline-step-card ${step.status}`}>
      <summary className="timeline-step-row">
        <span className="call-chain-seq">{step.seq}</span>
        <span className={`call-chain-agent agent-${step.callee}`}>{calleeName}</span>
        <code className="call-chain-skill">{skillLabel}</code>
        <span className={`call-chain-status ${step.status}`}>{step.status === "ok" ? "✓ 成功" : "✗ 失败"}</span>
        <span className="call-chain-ms">{step.elapsed_ms}ms</span>
      </summary>
      <div className="timeline-step-body">
        {readable && <p className="timeline-step-brief">{readable}</p>}
        {args.length > 0 && (
          <div className="timeline-args">
            <small>实参</small>
            <ul>
              {args.map(([name, value]) => (
                <li key={name}>
                  <code>{name}</code>
                  <span>{displaySystemText(typeof value === "string" ? value : JSON.stringify(value))}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="timeline-raw">
          <small>返回原文（截断 JSON，完整事实见运行记录）</small>
          <pre>{prettyJson(step.result_summary ?? "")}</pre>
        </div>
      </div>
    </details>
  );
}

export default function AssistantTimeline({
  items,
  live = false,
}: {
  items: TimelineItem[];
  live?: boolean;
}) {
  if (!items.length) return null;
  return (
    <div className={`assistant-timeline${live ? " live" : ""}`}>
      {items.map((item, i) =>
        item.kind === "reasoning" ? (
          <details key={`r-${item.round}-${i}`} open={live} className="assistant-thinking-round timeline-thinking">
            <summary>
              {live && <span className="assistant-thinking-dot" aria-hidden="true" />}
              思考过程 · 第 {item.round} 轮
            </summary>
            <p>{item.text}</p>
          </details>
        ) : (
          <StepCard key={`s-${item.step.seq}-${i}`} step={item.step} />
        ),
      )}
    </div>
  );
}
