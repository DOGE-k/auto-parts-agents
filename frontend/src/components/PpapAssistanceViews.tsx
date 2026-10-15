import { useMemo, useState } from "react";
import type { PpapArtifact, PpapElement, PpapIssue, PpapJson, PpapNavigation } from "../types/ppap";
import { displaySystemText } from "../lib/system-labels";

const ROLE_LABELS: Record<string, string> = {
  quality_manager: "质量", production_manager: "生产", purchase_manager: "采购", sales_manager: "销售",
};
const SIDE_LABELS: Record<string, string> = { LEFT: "左件", RIGHT: "右件", NONE: "不区分左右", BOTH: "左右共用" };
const DATASET_LABELS: Record<string, string> = {
  product_base: "产品基础信息", process_operation: "工序清单", process_relation: "工序关系",
  characteristic: "特性与规格", inspection_value: "检验原始值", msa_study: "测量系统研究",
  process_study: "过程研究", material_evidence: "材料与性能资料", packaging_equipment: "设备与包装",
  customer_requirement: "客户要求", change_record: "工程变更", physical_record: "实物记录", customer_handling: "客户处理要求",
};
const FIELD_LABELS: Record<string, string> = {
  value: "实测值", unit: "单位", lsl: "规格下限", usl: "规格上限", nominal: "标称值",
  measurements: "评价人 × 零件 × 测次原始数据", values: "原始样本序列", subgroups: "原始子组",
  characteristic_id: "特性编号", point: "测点编号", sample_no: "样本编号", missing_reason: "缺失原因",
  method: "计算方法", tolerance: "公差", study_id: "研究编号", name: "名称", note: "备注",
  part_no: "零件号", part_name: "零件名称", part_side: "零件侧别", customer_id: "客户编号", customer_name: "客户名称",
  drawing_version: "图纸版本", engineering_change_level: "工程变更等级", due_date: "期望完成日期",
  submission_level: "提交等级", submission_reason: "提交原因", requirement: "适用性要求", applicability_basis: "适用性依据",
  source_ref: "来源引用", source_type: "来源类型", source_id: "来源编号", document_version: "文件版本",
  confirmed_by: "确认人", confirmed_at: "确认时间", owner: "责任人", reviewer: "审核人",
  op_id: "工序编号", op_name: "工序名称", description: "说明", sequence: "顺序", special: "特殊特性",
  evidence_types: "所需证据类型", summary: "资料摘要", locator: "资料位置", report_no: "报告编号", spec_ref: "规格来源",
  data_mode: "任务模式", payload: "原始输入", content_hash: "内容指纹", source_version: "来源版本",
  read_status: "读取结果", truncated: "读取范围是否截断", element_ids: "绑定条目",
  compilation_engine_version: "编制器版本", export_version: "导出器版本", rule_hash: "验收规则指纹",
};
const SECTION_LABELS: Record<string, string> = {
  task: "任务信息", business_object: "业务关联", inputs: "确认输入", evidence: "来源证据",
  elements: "条目适用性", template: "模板", provenance: "来源记录", acceptance_rules: "验收规则",
  compilation_engine_version: "编制器版本", export_version: "导出器版本",
};
const CHANGE_LABELS: Record<string, string> = { added: "新增", removed: "移除", modified: "修改", unavailable: "无法还原历史原值" };
const asRecord = (value: unknown): PpapJson => value !== null && typeof value === "object" && !Array.isArray(value) ? value as PpapJson : {};
const records = (value: unknown): PpapJson[] => Array.isArray(value) ? value.map(asRecord) : [];
const text = (value: unknown): string => typeof value === "string" ? value : "";
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const fieldLabel = (path: string): string => FIELD_LABELS[path.split(".").at(-1) || ""] || path || "记录内容";
const roleLabel = (role: string): string => ROLE_LABELS[role] || role || "待明确岗位";

/** Uses only explicit server navigation targets. No field-path parsing or guessed editor destinations. */
function navigationTarget(value: unknown): PpapNavigation | null {
  const raw = asRecord(value);
  const kind = text(raw.kind);
  if (!["input", "evidence", "applicability", "task"].includes(kind)) return null;
  if (kind === "input" && !text(raw.dataset)) return null;
  if (kind === "applicability" && !text(raw.element_id)) return null;
  return raw as PpapNavigation;
}

export type PpapMissingItemsViewProps = {
  report: PpapJson | null;
  elements: PpapElement[];
  issues?: PpapIssue[];
  onNavigate: (target: PpapNavigation) => void;
  disabled?: boolean;
};

export function PpapMissingItemsView({ report, elements, issues = [], onNavigate, disabled = false }: PpapMissingItemsViewProps) {
  const [severity, setSeverity] = useState("BLOCKER");
  const [element, setElement] = useState("");
  const [role, setRole] = useState("");
  const findings = records(report?.findings);
  const blockers = findings.filter((finding) => finding.blocks_approval === true || finding.severity === "BLOCKER");
  const openIssues = issues.filter((issue) => issue.status === "OPEN");
  const persistedKeys = new Set(issues.map((issue) => text((issue as unknown as PpapJson).dedup_key)).filter(Boolean));
  const filtered = findings.filter((finding) =>
    (severity === "ALL" || finding.blocks_approval === true || finding.severity === "BLOCKER") &&
    (!element || (element === "__task__" ? !text(finding.element_id) : text(finding.element_id) === element)) &&
    (!role || text(finding.responsible_role) === role));
  const roles = Array.from(new Set(findings.map((finding) => text(finding.responsible_role)).filter(Boolean))).sort();
  const titleFor = (id: string) => elements.find((item) => item.element_id === id)?.title || id || "任务整体";
  const readiness = records(report?.readiness).filter((item) =>
    (!element || text(item.element_id) === element) &&
    (!role || elements.find((candidate) => candidate.element_id === text(item.element_id))?.responsible_role === role));
  return <section className="ppap-assistance ppap-missing-view" aria-label="缺项定位">
    <h3>缺项与补录位置</h3>
    <p className="ppap-muted">实时检查发现 {findings.length} 项，其中阻断 {blockers.length} 项；已登记未处理问题 {openIssues.length} 项。实时检查与问题记录分别统计。</p>
    {report === null ? <p className="ppap-muted">尚未取得实时检查结果。</p> : <>
      <div className="ppap-assistance-filters">
        <label>发现范围<select aria-label="缺项发现范围" value={severity} onChange={(event) => setSeverity(event.target.value)}>
          <option value="BLOCKER">阻断项</option><option value="ALL">全部发现</option>
        </select></label>
        <label>条目<select aria-label="缺项条目筛选" value={element} onChange={(event) => setElement(event.target.value)}>
          <option value="">全部条目</option><option value="__task__">任务整体</option>
          {elements.map((item) => <option key={item.element_id} value={item.element_id}>{item.title}</option>)}
        </select></label>
        <label>责任岗位<select aria-label="缺项岗位筛选" value={role} onChange={(event) => setRole(event.target.value)}>
          <option value="">全部岗位</option>{roles.map((item) => <option key={item} value={item}>{roleLabel(item)}</option>)}
        </select></label>
      </div>
      <p className="ppap-muted">当前显示 {filtered.length} 项</p>
      <div className="ppap-finding-list">{filtered.map((finding, index) => {
        const navigation = asRecord(finding.navigation);
        const target = navigationTarget(navigation);
        const dedupKey = text(finding.dedup_key);
        const registration = dedupKey ? persistedKeys.has(dedupKey) ? "已登记" : "未登记" : "登记状态待核对";
        const id = text(finding.element_id);
        return <article className="ppap-finding" key={`${dedupKey || text(finding.check_id)}-${index}`}>
          <div className="ppap-finding-heading"><strong>{displaySystemText(text(finding.title)) || "检查发现"}</strong>
            <span className="ppap-status">{finding.blocks_approval === true || finding.severity === "BLOCKER" ? "阻断" : finding.severity === "MAJOR" ? "提示" : "信息"}</span>
            <small>{registration}</small></div>
          <p>{displaySystemText(text(finding.detail)) || "检查结果未提供详细说明。"}</p>
          <p className="ppap-muted">{titleFor(id)} · {roleLabel(text(finding.responsible_role))}
            {text(finding.field_path) && <> · {fieldLabel(text(finding.field_path))}</>}</p>
          {(text(navigation.record_key) || text(navigation.part_side)) && <p className="ppap-muted">
            {text(navigation.record_key) && <>记录键：{text(navigation.record_key)} </>}
            {text(navigation.part_side) && <>侧别：{SIDE_LABELS[text(navigation.part_side)] || text(navigation.part_side)}</>}
          </p>}
          {text(finding.required_evidence) && <p>需要：{displaySystemText(text(finding.required_evidence))}</p>}
          {text(navigation.reason) && <p className="ppap-muted">{displaySystemText(text(navigation.reason))}</p>}
          {target ? <button type="button" className="button secondary" disabled={disabled} onClick={() => onNavigate(target)}>去补充</button>
            : <p className="ppap-confirmation-note">{text(navigation.kind) === "rules" ? "规则需业务确认" : "需人工确认补充位置"}</p>}
        </article>;
      })}</div>
      {filtered.length === 0 && <p className="ppap-muted">当前筛选范围没有发现。此结果不代表资料已获批准。</p>}
      {readiness.length > 0 && <details className="ppap-readiness"><summary>条目资料就绪情况（{readiness.length} 项）</summary>
        <div className="ppap-table-scroll"><table className="ppap-table"><thead><tr><th>条目</th><th>必需输入缺项</th><th>证据缺项</th><th>资料状态</th></tr></thead>
          <tbody>{readiness.map((item, index) => <tr key={`${text(item.element_id)}-${index}`}><td>{titleFor(text(item.element_id))}</td>
            <td>{strings(item.missing_inputs).map((id) => DATASET_LABELS[id] || id).join("、") || "未列出缺项"}</td>
            <td>{strings(item.missing_evidence).join("、") || "未列出缺项"}</td>
            <td>{item.data_ready === true ? "资料已具备，仍需检查审核" : item.data_ready === false ? "资料待补充" : "未提供状态"}</td>
          </tr>)}</tbody></table></div>
      </details>}
    </>}
  </section>;
}

function ValueDisplay({ present, available, value, historical = false }: { present: unknown; available: unknown; value: unknown; historical?: boolean }) {
  if (available === false) return <span className="ppap-unavailable">{historical ? "历史未保存原值，无法还原" : "当前原值不可用"}</span>;
  if (present === false) return <span className="ppap-muted">未提供</span>;
  if (present !== true || available !== true) return <span className="ppap-unavailable">未提供可核对的原值状态</span>;
  if (value === null) return <span>明确为空（null）</span>;
  if (typeof value === "boolean") return <span>{value ? "是（true）" : "否（false）"}</span>;
  if (typeof value === "number") return <span>{Number.isFinite(value) ? String(value) : "无效数值（非有限）"}</span>;
  if (typeof value === "string") return <span className="ppap-diff-text">{value === "" ? "空字符串" : displaySystemText(value)}</span>;
  if (Array.isArray(value) || value !== null && typeof value === "object") return <details className="ppap-diff-value">
    <summary>{Array.isArray(value) ? `数组（${value.length} 项）` : "结构化内容"} · 展开完整内容</summary>
    <pre>{displaySystemText(JSON.stringify(value, null, 2))}</pre>
  </details>;
  return <span className="ppap-unavailable">未提供可展示的原值</span>;
}

function SourceDisplay({ value }: { value: unknown }) {
  const source = asRecord(value);
  if (Object.keys(source).length === 0) return text(value) ? <small>来源：{displaySystemText(text(value))}</small> : null;
  return <details className="ppap-diff-source"><summary>来源记录{text(source.source_ref) ? `：${displaySystemText(text(source.source_ref))}` : ""}</summary>
    <dl>{Object.entries(source).map(([key, item]) => <div key={key}><dt>{FIELD_LABELS[key] || key}</dt>
      <dd>{item === null ? "明确为空（null）" : displaySystemText(typeof item === "object" ? JSON.stringify(item) : String(item))}</dd></div>)}</dl>
  </details>;
}

export type PpapVersionDiffViewProps = {
  impact: PpapJson | null;
  artifacts: PpapArtifact[];
  elements?: PpapElement[];
  compareArtifactId: string;
  onCompare: (id: string) => void;
  loading?: boolean;
};

export function PpapVersionDiffView({ impact, artifacts, elements = [], compareArtifactId, onCompare, loading = false }: PpapVersionDiffViewProps) {
  const packages = useMemo(() => artifacts.filter((artifact) => artifact.scope === "PACKAGE").sort((a, b) => b.version - a.version), [artifacts]);
  const changes = records(impact?.changes);
  const reasons = records(impact?.impact_reasons);
  const affected = strings(impact?.affected_elements);
  const titleFor = (id: string) => elements.find((item) => item.element_id === id)?.title || id;
  return <section className="ppap-assistance ppap-version-diff" aria-label="版本修改对照">
    <h3>版本修改对照</h3>
    <label>对照版本<select aria-label="PPAP 对照版本" value={compareArtifactId} disabled={loading || packages.length === 0} onChange={(event) => onCompare(event.target.value)}>
      <option value="">最新编制版本</option>{packages.map((artifact) => <option key={artifact.artifact_id} value={artifact.artifact_id}>V{artifact.version} · {artifact.created_at || "未提供编制时间"}</option>)}
    </select></label>
    <p className="ppap-muted">比较当前已登记资料与所选版本。此操作不刷新远端资料，不修改输入，也不生成新版本。</p>
    {loading ? <p>正在读取对照结果…</p> : impact === null ? <p className="ppap-muted">尚未取得对照结果。</p>
      : impact.has_artifact === false ? <p className="ppap-muted">尚无编制版本。当前资料可以录入，编制后才能进行版本对照。</p>
        : <>
          <p>{changes.length > 0 ? `发现 ${changes.length} 处字段变化` : impact.changed === true ? "来源快照已变化，但未提供可核对的字段级差异。" : "未发现已登记资料的变化。"}</p>
          {text(impact.notice) && <p className="ppap-muted">{displaySystemText(text(impact.notice))}</p>}
          {changes.length > 0 && <div className="ppap-table-scroll"><table className="ppap-table ppap-diff-table"><thead><tr><th>记录与字段</th><th>所选版本</th><th>当前资料</th></tr></thead>
            <tbody>{changes.map((change, index) => <tr key={`${text(change.section)}-${text(change.record_key)}-${text(change.field_path)}-${index}`}>
              <td><strong>{fieldLabel(text(change.field_path))}</strong>
                <small>{SECTION_LABELS[text(change.section)] || text(change.section) || "来源快照"}
                  {text(change.dataset) && <> · {DATASET_LABELS[text(change.dataset)] || text(change.dataset)}</>}</small>
                {text(change.record_key) && <small>记录键：{text(change.record_key)}</small>}
                {text(change.part_side) && <small>侧别：{SIDE_LABELS[text(change.part_side)] || text(change.part_side)}</small>}
                {text(change.element_id) && <small>条目：{titleFor(text(change.element_id))}</small>}
                {text(change.input_id) && <small>记录编号：{text(change.input_id)}</small>}
                <small>{CHANGE_LABELS[text(change.change_type)] || text(change.change_type)}</small>
                <small className="ppap-diff-path">{text(change.field_path)}</small>
              </td>
              <td><ValueDisplay present={change.old_present} available={change.old_available} value={change.old_value} historical /><SourceDisplay value={change.old_source} /></td>
              <td><ValueDisplay present={change.new_present} available={change.new_available} value={change.new_value} /><SourceDisplay value={change.new_source} /></td>
            </tr>)}</tbody></table></div>}
          {affected.length > 0 && <div className="ppap-diff-affected"><strong>受影响条目</strong><ul>{affected.map((id) => <li key={id}>{titleFor(id)}
            {reasons.filter((reason) => text(reason.element_id) === id).map((reason, index) => <span key={index}>：{displaySystemText(text(reason.reason))}</span>)}</li>)}</ul></div>}
        </>}
  </section>;
}
