import type { PpapJson } from "../types/ppap";
import { displaySystemText } from "../lib/system-labels";

function valueText(value: unknown): string {
  if (value === null) return "明确无值（null）";
  if (value === undefined) return "未提供";
  if (typeof value === "boolean") return value ? "是（true）" : "否（false）";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value === "" ? "空字符串" : displaySystemText(value);
  if (Array.isArray(value)) return `表格 / 序列（${value.length} 项，展开记录详情查看）`;
  return "结构化内容（展开记录详情查看）";
}

export default function PpapRecordSummary({ payload, labels }: { payload: PpapJson; labels: Record<string, string> }) {
  return <div className="ppap-record-summary">
    <dl>{Object.entries(payload).map(([field, value]) => <div key={field}><dt>{labels[field] || field}</dt><dd>{valueText(value)}</dd></div>)}</dl>
    {!Object.keys(payload).length && <p className="muted">未提供记录字段。</p>}
    <details><summary>记录详情</summary><pre>{displaySystemText(JSON.stringify(payload, null, 2))}</pre></details>
  </div>;
}
