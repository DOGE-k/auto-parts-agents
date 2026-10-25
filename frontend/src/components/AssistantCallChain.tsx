// 调用链可视化（前端优化清单任务5）：把一轮问答的真实调用过程画成垂直链路——
// 调用方岗位 → 被调岗位（pill 配色）+ 技能中文名 + 状态 + 耗时；返回摘要在第二行。
// 实参与原始 skill_id 保留在悬停提示里，不丢事实；纯展示组件，不改问答流程。
import { agentRunTypeNames } from "../api";
import type { AssistantCallStep } from "../api";
import { displaySystemText } from "../lib/system-labels";
import { summarizeSkillResult } from "../lib/result-summary";

// 技能中文名（与后端 coordinator.py 技能注册一一对应；未登记的 skill_id 原样显示，不编造翻译）
// harness 步骤 E：导出供 AssistantTimeline 复用，保持单一来源
export const skillNames: Record<string, string> = {
  "quotation.get_real": "查询报价",
  "quotation.analyze_real": "报价分析",
  "quotation.assess_cost_impact": "加急成本评估",
  "quotation.find_real_by_erp_order": "按订单查报价",
  "quotation.list_real_sales_orders": "查销售订单列表",
  "procurement.analyze_real": "采购分析",
  "procurement.assess_combination": "组合方案评估",
  "procurement.find_real_plan_by_quotation": "按报价查方案",
  "procurement.get_real_plan": "查询采购方案",
  "procurement.inventory_overview": "库存总览",
  "tracking.track_real": "生产进度跟踪",
  "tracking.find_real_by_no": "按工单号查询",
  "tracking.list_real_work_orders": "查工单列表",
  "tracking.lookup_order_link": "查订单关联",
  "tracking.assess_delivery_impact": "交付影响评估",
  "tracking.check_real_ship_gate": "发运门禁检查",
  "quality.get_real_package": "查询质量资料包",
  "quality.list_open_issues": "查未关闭问题",
  "quality.assess_quality_impact": "质量影响评估",
  "quality.check_issue_closure": "问题闭环检查",
  "quality.ppap_find_tasks": "PPAP 任务查找",
  "quality.ppap_get_applicability": "PPAP 适用性判定",
  "quality.ppap_get_status": "PPAP 状态查询",
  "quality.ppap_explain_missing": "PPAP 缺失说明",
  "quality.ppap_evidence_plan": "PPAP 证据计划",
  "quality.ppap_get_versions": "PPAP 版本查询",
};

export default function AssistantCallChain({ steps, live }: { steps: AssistantCallStep[]; live?: boolean }) {
  if (!steps.length) return null;
  return (
    <div className={`call-chain${live ? " live" : ""}`}>
      {steps.map((step) => {
        const skillLabel = skillNames[step.skill_id] ?? step.skill_id;
        const calleeName = displaySystemText(agentRunTypeNames[step.callee] ?? step.callee);
        const callerName = displaySystemText(agentRunTypeNames[step.caller] ?? step.caller);
        const argsText = JSON.stringify(step.arguments ?? {});
        const rawSummary = step.result_summary ?? "";
        // 任务 7：成功步骤按 skill_id 提取关键字段转一句中文；失败步骤/提取不到的
        // 技能保留 JSON 原文（完整原文同样在悬停提示里）。
        const readable =
          step.status === "ok" ? summarizeSkillResult(step.skill_id, rawSummary) : null;
        const summary = readable ?? displaySystemText(rawSummary);
        return (
          <div
            key={step.seq}
            className={`call-chain-step ${step.status}`}
            title={`第 ${step.seq} 步 · ${callerName} 调用 ${calleeName}${summary ? `\n返回：${summary}` : ""}\n技能：${step.skill_id}\n实参：${argsText}`}
          >
            <span className="call-chain-seq">{step.seq}</span>
            <div className="call-chain-main">
              <div className="call-chain-line">
                <span className={`call-chain-agent agent-${step.callee}`}>{calleeName}</span>
                <code className="call-chain-skill">{skillLabel}</code>
                <span className={`call-chain-status ${step.status}`}>{step.status === "ok" ? "✓ 成功" : "✗ 失败"}</span>
                <span className="call-chain-ms">{step.elapsed_ms}ms</span>
              </div>
              {summary && <div className="call-chain-result">{summary}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
