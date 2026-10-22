import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  archivePpapTask, checkPpapTask, collectPpapEvidence, compilePpapTask, confirmPpapApplicability,
  createPpapTask, decidePpapReview, downloadPpapArtifact, getPpapChangeImpact, getPpapMissingItems, getPpapEvidencePlan, getPpapInputs, getPpapInputAssistance,
  getPpapRuns, getPpapTask, getPpapTasks, getPpapTemplate, recordPpapCustomerState,
  registerPpapEvidence, resolvePpapIssue, savePpapInput, submitPpapReview, updatePpapTask,
} from "../api";
import type { PpapDatasetContract, PpapDetail, PpapJson, PpapList, PpapMutation, PpapPlan,
  PpapMode, PpapRequirement, PpapRun, PpapSide, PpapTaskFields, PpapTemplate, PpapInput, PpapInputAssistance, PpapNavigation, PpapSuggestion } from "../types/ppap";
import PpapStudyEditor from "./PpapStudyEditor";
import { parseStudyData, type PpapStudyField } from "./ppapStudyData";
import { PpapMissingItemsView, PpapVersionDiffView } from "./PpapAssistanceViews";
import { mergePpapSuggestions } from "../lib/ppap-prefill";
import { PPAP_DATASET_GROUPS, getDatasetGroup, getPpapWorkflow, type PpapStage } from "../lib/ppap-workflow";
import PpapRecordSummary from "./PpapRecordSummary";
import { displaySystemText } from "../lib/system-labels";

type Props = { hasProjectSession: boolean; actorId?: string; roles?: string[]; onLogin: () => void };
type Tab = PpapStage;
type InputSavePhase = "idle" | "saving" | "saved" | "saved_unverified" | "uncertain" | "error";
type InputSaveFeedback = { phase: InputSavePhase; message: string; label?: string; record?: PpapInput; count?: number; created?: boolean };
type InputSaveContext = { taskId: string; dataset: string; side: PpapSide; recordKey: string;
  payload: PpapJson; sourceRef: string; sourceType: string; receipt?: PpapMutation; created?: boolean };
const stableJson = (value: unknown): string => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const savedTime = (value: string) => {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : value;
};
const EMPTY_FIELDS: PpapTaskFields = { customer_id: "", customer_name: "", part_no: "", part_name: "",
  part_side: "NONE", drawing_version: "", engineering_change_level: "", submission_reason: "", submission_level: "", due_date: "" };
const BASE_LABELS: Record<keyof PpapTaskFields, string> = { customer_id: "客户编号", customer_name: "客户名称",
  part_no: "零件号", part_name: "零件名称", part_side: "零件侧别", drawing_version: "图纸版本",
  engineering_change_level: "工程变更等级", submission_reason: "提交原因", submission_level: "提交等级", due_date: "期望完成日期" };
const BUSINESS_LABELS: Record<string, string> = { work_order_no: "MES 工单号", work_order_id: "MES 工单 ID",
  quotation_id: "项目报价编号", erp_order_id: "ERP 销售订单号", issue_id: "关联质量问题 ID",
  work_order_batch_no: "生产批次", design_responsibility: "设计责任说明", customer_spec_reference: "客户要求文件编号" };
const INPUT_LABELS: Record<string, string> = { part_no: "零件号", part_name: "零件名称", customer_name: "客户名称",
  drawing_version: "图纸版本", engineering_change_level: "工程变更等级", revision_note: "版本说明",
  op_id: "工序编号", op_name: "工序名称", station: "工位", description: "说明", sequence: "顺序",
  from_op: "前工序编号", to_op: "后工序编号", relation_type: "关系类型", note: "备注",
  characteristic_id: "特性编号", name: "特性名称", characteristic_type: "特性类别", special: "特殊特性",
  unit: "单位", usl: "规格上限", lsl: "规格下限", nominal: "标称值", target: "目标值", drawing_location: "图纸位置",
  point: "测点编号", value: "实测值", sample_no: "样本编号", measured_by: "测量人", measured_at: "测量时间",
  missing_reason: "缺失原因", study_id: "研究编号", measurements: "评价人 × 零件 × 测次原始数据",
  tolerance: "公差", method: "研究方法", values: "原始样本序列", subgroups: "原始子组", sampling_note: "抽样说明",
  test_item: "试验项目", supplier: "供应商", lot_no: "材料批次", report_no: "报告编号", spec_ref: "规格来源",
  item: "设备或包装项目", quantity: "数量", owner: "责任人", requirement_text: "客户要求内容", source: "来源文件",
  clause: "条款", change_description: "变更内容", change_no: "变更编号", change_date: "变更日期", level: "变更等级",
  reason: "变更原因", record_type: "实物记录类别", storage_location: "存放位置", photo_refs: "照片引用列表",
  instruction: "客户处理要求", stage: "阶段" };
const REQUIREMENT_LABELS: Record<string, string> = { "": "待确认", submit: "提交", retain: "保留", on_request: "按要求提供", not_applicable: "不适用" };
const SIDE_LABELS = { NONE: "未区分左右", LEFT: "左件", RIGHT: "右件", BOTH: "左右共用（不混用单侧数据）" };
const STATES: Record<string, string> = { NOT_STARTED: "待启动", RUNNING: "运行中", WAITING_DATA: "等待资料",
  BLOCKED: "有阻断项", COMPILED: "已编制", FAILED: "失败", COMPLETED: "完成", DRAFT: "草稿",
  NOT_SUBMITTED: "未提交", SUBMITTED: "已提交", PENDING_REVIEW: "审核中", IN_REVIEW: "审核中",
  APPROVED: "内部批准", RETURNED: "退回", ARCHIVED: "已归档", ACCEPTED: "客户接受", REJECTED: "客户退回",
  SUPERSEDED: "已被新版本替代", APPLICABILITY_UNCONFIRMED: "适用性待确认", READY: "可编制",
  DRAFTED: "已生成草稿", REVIEWED: "已审核", NOT_APPLICABLE: "不适用", BLOCKED_MISSING_DATA: "缺必需资料",
  CONFLICT: "存在冲突", NEEDS_CONFIRMATION: "待确认规则", OPEN: "未处理", RESOLVED: "数据已修正", WAIVED: "人工接受并留痕" };
const COMPLEX_FIELDS = new Set(["measurements", "values", "subgroups", "photo_refs"]);
const TEST_BUSINESS_KEYS = new Set(["work_order_no", "work_order_id", "quotation_id", "erp_order_id", "issue_id", "work_order_batch_no"]);
const modeLabel = (mode: PpapMode) => mode === "TEST" ? "测试任务 · 不可提交客户" : "正式任务";
const stateLabel = (value: string) => STATES[value] || value || "未设置";
const jsonText = (value: unknown) => JSON.stringify(value, null, 2);

function BaseFields({ fields, onChange, disabled }: { fields: PpapTaskFields; onChange: (v: PpapTaskFields) => void; disabled: boolean }) {
  return <div className="ppap-form-grid">{(Object.keys(BASE_LABELS) as Array<keyof PpapTaskFields>).map((key) =>
    <label key={key}>{BASE_LABELS[key]}{["customer_name", "part_no", "part_name", "part_side"].includes(key) ? " *" : ""}
      {key === "part_side" ? <select value={fields.part_side} disabled={disabled} onChange={(e) => onChange({ ...fields, part_side: e.target.value as PpapSide })}>
        {Object.entries(SIDE_LABELS).map(([side, label]) => <option key={side} value={side}>{label}</option>)}
      </select> : <input disabled={disabled} type={key === "due_date" ? "date" : "text"} value={fields[key]}
        required={["customer_name", "part_no", "part_name"].includes(key)} onChange={(e) => onChange({ ...fields, [key]: e.target.value })} />}
    </label>)}</div>;
}

export default function PpapPanel({ hasProjectSession, actorId = "", roles, onLogin }: Props) {
  const canonicalRoles = (roles || []).map((role) => role.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""));
  const canWrite = hasProjectSession && canonicalRoles.some((role) => ["quality_manager", "administrator", "system_manager"].includes(role));
  const [list, setList] = useState<PpapList | null>(null);
  const [template, setTemplate] = useState<PpapTemplate | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<PpapDetail | null>(null);
  const [contracts, setContracts] = useState<Record<string, PpapDatasetContract>>({});
  const [plan, setPlan] = useState<PpapPlan | null>(null);
  const [runs, setRuns] = useState<PpapRun[]>([]);
  const [changeImpact, setChangeImpact] = useState<PpapJson | null>(null);
  const [currentImpact, setCurrentImpact] = useState<PpapJson | null>(null);
  const [missing, setMissing] = useState<PpapJson | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [modeFilter, setModeFilter] = useState<PpapMode | "">("REAL");
  const [tab, setTab] = useState<Tab>("prepare");
  const [showTaskList, setShowTaskList] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const [preparationSection, setPreparationSection] = useState<"inputs" | "evidence">("inputs");
  const [showCreate, setShowCreate] = useState(false);
  const [createFields, setCreateFields] = useState<PpapTaskFields>({ ...EMPTY_FIELDS });
  const [createMode, setCreateMode] = useState<PpapMode>("REAL");
  const [editFields, setEditFields] = useState<PpapTaskFields>({ ...EMPTY_FIELDS });
  const [businessFields, setBusinessFields] = useState<Record<string, string>>({});
  const [decisions, setDecisions] = useState<Record<string, { requirement: PpapRequirement; basis: string }>>({});
  const [evidenceFields, setEvidenceFields] = useState({ source_type: "user_upload", source_id: "", locator: "", summary: "", document_version: "" });
  const [evidenceElementIds, setEvidenceElementIds] = useState<string[]>([]);
  const [dataset, setDataset] = useState("");
  const [inputFields, setInputFields] = useState<Record<string, string>>({});
  const [nullFields, setNullFields] = useState<Set<string>>(new Set());
  const [sourceRef, setSourceRef] = useState("");
  const [recordKey, setRecordKey] = useState("");
  const [inputSide, setInputSide] = useState<PpapSide>("NONE");
  const [inputSourceType, setInputSourceType] = useState("user_confirmation");
  const [assistance, setAssistance] = useState<PpapInputAssistance | null>(null);
  const [assistanceError, setAssistanceError] = useState("");
  const [assistanceLoading, setAssistanceLoading] = useState(false);
  const [recognitionRevision, setRecognitionRevision] = useState(0);
  const [inputEditorRevision, setInputEditorRevision] = useState(0);
  const [inputSaveFeedback, setInputSaveFeedback] = useState<InputSaveFeedback>({ phase: "idle", message: "尚未确认保存，请核对资料后提交。" });
  const [inputEdited, setInputEdited] = useState(false);
  const [savedInputsOpen, setSavedInputsOpen] = useState(false);
  const [highlightedInputId, setHighlightedInputId] = useState("");
  const savedInputsRef = useRef<HTMLDetailsElement>(null);
  const inputSaveContext = useRef<InputSaveContext | null>(null);
  const inputSaveLock = useRef(false);
  const inputSaveGeneration = useRef(0);
  const inputDrafts = useRef(new Map<string, { fields: Record<string, string>; nulls: Set<string>; sourceRef: string; recordKey: string;
    sourceType: string; origins: Record<string, PpapSuggestion>; cleared: Set<string>; edited: boolean }>());
  const initializedTask = useRef("");
  const [autoOrigins, setAutoOrigins] = useState<Record<string, PpapSuggestion>>({});
  const [clearedFields, setClearedFields] = useState<Set<string>>(new Set());
  const [compareArtifactId, setCompareArtifactId] = useState("");
  const [compareLoading, setCompareLoading] = useState(false);
  const [navigationNote, setNavigationNote] = useState("");
  const formRef = useRef<HTMLDivElement>(null);
  const evidenceRef = useRef<HTMLDivElement>(null);
  const taskRef = useRef<HTMLDetailsElement>(null);
  const applicabilityRef = useRef<HTMLDetailsElement>(null);
  const currentForm = useRef({ inputFields, nullFields, autoOrigins, clearedFields, edited: inputEdited, recordKey });
  currentForm.current = { inputFields, nullFields, autoOrigins, clearedFields, edited: inputEdited, recordKey };
  const compareRequest = useRef(0);
  const [issueId, setIssueId] = useState("");
  const [issueNote, setIssueNote] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [customerState, setCustomerState] = useState("SUBMITTED");
  const [customerSource, setCustomerSource] = useState("");
  const [customerNote, setCustomerNote] = useState("");
  const currentSelection = useRef(selectedId);
  currentSelection.current = selectedId;
  const listRequest = useRef(0);

  const loadList = useCallback(async () => {
    const request = ++listRequest.current;
    const result = await getPpapTasks(offset, query, modeFilter);
    if (request === listRequest.current) setList(result);
  }, [offset, query, modeFilter]);
  useEffect(() => {
    let alive = true;
    const request = ++listRequest.current;
    setLoading(true); setError("");
    Promise.all([getPpapTasks(offset, query, modeFilter), getPpapTemplate()]).then(([tasks, descriptor]) => {
      if (alive) { if (request === listRequest.current) setList(tasks); setTemplate(descriptor); }
    }).catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : "PPAP 任务读取失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [offset, query, modeFilter, hasProjectSession]);

  const loadDetail = useCallback(async (taskId: string) => {
    compareRequest.current += 1; setCompareLoading(false);
    const [result, inputs, evidencePlan, history, impact, gaps] = await Promise.all([getPpapTask(taskId), getPpapInputs(taskId), getPpapEvidencePlan(taskId), getPpapRuns(taskId), getPpapChangeImpact(taskId), getPpapMissingItems(taskId)]);
    if (currentSelection.current !== taskId) return;
    setDetail(result); setContracts(inputs.contracts); setPlan(evidencePlan); setRuns(history);
    setChangeImpact(impact); setCurrentImpact(impact); setMissing(gaps); setCompareArtifactId("");
    const firstOpen = initializedTask.current !== taskId;
    if (firstOpen) {
      initializedTask.current = taskId;
      setTab(getPpapWorkflow(result, gaps, impact).next.stage); setShowTaskList(false);
    }
    const taskFields = { ...EMPTY_FIELDS };
    for (const key of Object.keys(taskFields) as Array<keyof PpapTaskFields>) Object.assign(taskFields, { [key]: result.task[key] });
    setEditFields(taskFields);
    const links: Record<string, string> = {};
    for (const key of Object.keys(BUSINESS_LABELS)) links[key] = String(result.task.business_object[key] ?? "");
    setBusinessFields(links);
    setDecisions(Object.fromEntries(result.elements.map((element) => [element.element_id, { requirement: element.requirement, basis: element.applicability_basis }])));
    setDataset((old) => old && inputs.contracts[old] ? old : inputs.datasets[0] || "");
    if (firstOpen || (!currentForm.current.edited && !currentForm.current.recordKey && !inputSaveContext.current)) setInputSide(result.task.part_side);
  }, []);
  useEffect(() => {
    setDetail(null); setPlan(null); setRuns([]); setChangeImpact(null); setCurrentImpact(null); setMissing(null); setInputFields({}); setNullFields(new Set()); setEvidenceElementIds([]); setSourceRef(""); setRecordKey(""); setIssueId("");
    inputDrafts.current.clear(); setPreparationSection("inputs"); setShowHistory(false);
    setEvidenceFields({ source_type: "user_upload", source_id: "", locator: "", summary: "", document_version: "" });
    setReviewComment(""); setCustomerSource(""); setCustomerNote(""); setIssueNote("");
    setAssistance(null); setAssistanceError(""); setAutoOrigins({}); setClearedFields(new Set());
    setInputSourceType("user_confirmation"); setNavigationNote(""); setCompareArtifactId(""); compareRequest.current += 1;
    inputSaveGeneration.current += 1;
    inputSaveContext.current = null; setSavedInputsOpen(false); setHighlightedInputId(""); setInputEdited(false);
    setInputSaveFeedback({ phase: "idle", message: "尚未确认保存，请核对资料后提交。" });
    if (!selectedId) return;
    setLoading(true); setError("");
    void loadDetail(selectedId).catch((e: unknown) => setError(e instanceof Error ? e.message : "PPAP 详情读取失败"))
      .finally(() => setLoading(false));
  }, [selectedId, loadDetail, hasProjectSession]);

  const selectedCharacteristic = inputFields.characteristic_id || "";
  useEffect(() => {
    if (!selectedId || !detail || !dataset || !contracts[dataset]) return;
    let alive = true;
    setAssistanceLoading(true); setAssistanceError("");
    void getPpapInputAssistance(selectedId, dataset, inputSide, selectedCharacteristic, recordKey).then((result) => {
      if (!alive || currentSelection.current !== selectedId) return;
      setAssistance(result);
      if (!canWrite || recordKey) return;
      const draft = currentForm.current;
      const schema = contracts[dataset];
      const merged = mergePpapSuggestions(draft.inputFields, draft.nullFields, draft.autoOrigins,
        result.suggestions, [...schema.required, ...schema.optional], draft.clearedFields);
      setInputFields(merged.fields); setAutoOrigins(merged.origins);
      // A specification source is not the source of new measurement readings.
      // Only task metadata has a complete source for a new product-base record.
      if (dataset === "product_base" && result.suggestions.length) {
        setSourceRef((old) => old || result.suggestions[0].source_ref);
        setInputSourceType(result.suggestions[0].source_type);
      }
    }).catch((cause: unknown) => {
      if (alive) { setAssistance(null); setAssistanceError(cause instanceof Error ? cause.message : "已有资料识别失败"); }
    }).finally(() => { if (alive) setAssistanceLoading(false); });
    return () => { alive = false; };
  }, [selectedId, detail, dataset, inputSide, selectedCharacteristic, recordKey, contracts, canWrite, recognitionRevision]);

  function changeInputField(field: string, value: string) {
    setInputEdited(true);
    if (field === "characteristic_id") {
      const draft = currentForm.current;
      const cleaned = mergePpapSuggestions(draft.inputFields, draft.nullFields, draft.autoOrigins, [], Object.keys(draft.inputFields));
      setInputFields({ ...cleaned.fields, [field]: value }); setAutoOrigins({});
    } else setInputFields((old) => ({ ...old, [field]: value }));
    setAutoOrigins((old) => { const next = { ...old }; delete next[field]; return next; });
    setClearedFields((old) => { const next = new Set(old); if (value === "") next.add(field); else next.delete(field); return next; });
  }
  function resetInputForm(nextDataset: string, side = detail?.task.part_side || "NONE") {
    if (inputSaveLock.current || inputSaveContext.current) return;
    setInputSaveFeedback({ phase: "idle", message: "新增记录尚未确认保存。" }); setInputEdited(false); setHighlightedInputId("");
    setRecognitionRevision((old) => old + 1);
    setInputEditorRevision((old) => old + 1);
    setDataset(nextDataset); setInputSide(side); setInputFields({}); setNullFields(new Set());
    setRecordKey(""); setSourceRef(""); setInputSourceType("user_confirmation"); setAutoOrigins({});
    setClearedFields(new Set()); setAssistance(null); setAssistanceError("");
  }
  function restoreInputDraft(stored: NonNullable<ReturnType<typeof inputDrafts.current.get>>) {
    setInputFields(stored.fields); setNullFields(stored.nulls); setSourceRef(stored.sourceRef); setRecordKey(stored.recordKey);
    setInputSourceType(stored.sourceType); setAutoOrigins(stored.origins); setClearedFields(stored.cleared); setInputEdited(stored.edited);
    setInputSaveFeedback({ phase: "idle", message: "已恢复本任务当前侧别的录入草稿；尚未提交的修改需确认保存。" });
  }
  function switchInputDataset(nextDataset: string, side = inputSide) {
    if (inputSaveLock.current || inputSaveContext.current || busy || (nextDataset === dataset && side === inputSide)) return;
    if (formRef.current?.querySelector('[aria-invalid="true"], .ppap-study-json textarea:invalid')) {
      setInputSaveFeedback({ phase: "error", message: "请先修正当前表格中的非法原始值，再切换资料。填写内容已保留。" }); return;
    }
    inputDrafts.current.set(`${dataset}|${inputSide}`, { fields: inputFields, nulls: nullFields, sourceRef, recordKey,
      sourceType: inputSourceType, origins: autoOrigins, cleared: clearedFields, edited: inputEdited });
    const stored = inputDrafts.current.get(`${nextDataset}|${side}`);
    resetInputForm(nextDataset, side);
    if (stored) restoreInputDraft(stored);
  }
  function loadInputRevision(input: PpapInput) {
    if (inputSaveLock.current || inputSaveContext.current) return;
    if (inputEdited && (input.dataset !== dataset || input.record_key !== recordKey || input.part_side !== inputSide)) {
      setInputSaveFeedback({ phase: "error", message: "请先保存或清除当前录入草稿，再载入另一条记录。当前内容已保留。" }); return;
    }
    setInputSaveFeedback({ phase: "idle", message: "已载入原记录；本次修订需确认保存。" }); setInputEdited(false); setHighlightedInputId("");
    setRecognitionRevision((old) => old + 1);
    setInputEditorRevision((old) => old + 1);
    setDataset(input.dataset); setInputSide(input.part_side); setRecordKey(input.record_key); setSourceRef(input.source_ref);
    setInputSourceType(input.source_type); setAutoOrigins({}); setClearedFields(new Set()); setAssistance(null);
    setNullFields(new Set(Object.entries(input.payload).filter(([, value]) => value === null).map(([key]) => key)));
    setInputFields(Object.fromEntries(Object.entries(input.payload).map(([key, value]) => [key,
      typeof value === "object" && value !== null ? jsonText(value) : value === null ? "" : String(value)])));
  }
  async function compareVersion(artifactId: string) {
    const request = ++compareRequest.current, taskId = selectedId;
    setCompareArtifactId(artifactId); setCompareLoading(true);
    try {
      const result = await getPpapChangeImpact(taskId, artifactId);
      if (request === compareRequest.current && currentSelection.current === taskId) setChangeImpact(result);
    } catch (cause: unknown) { if (request === compareRequest.current) setError(cause instanceof Error ? cause.message : "版本对照读取失败"); }
    finally { if (request === compareRequest.current) setCompareLoading(false); }
  }
  function navigateToMissing(target: PpapNavigation) {
    if (inputSaveLock.current || inputSaveContext.current) {
      setTab("prepare"); setPreparationSection("inputs"); setNavigationNote("请先核对本次保存结果，再定位其他资料。"); return;
    }
    if (!["input", "evidence", "applicability", "task"].includes(target.kind)) {
      setNavigationNote(target.reason || "该项需要业务确认，请按缺项依据补充"); return;
    }
    if (target.kind === "input" && (!target.dataset || !contracts[target.dataset])) {
      setError(target.reason || "目标资料类型尚未取得，不能猜测补录位置；请重新读取当前任务核对。"); return;
    }
    setTab(target.kind === "task" || target.kind === "applicability" ? "requirements" : "prepare");
    setPreparationSection(target.kind === "evidence" ? "evidence" : "inputs");
    setNavigationNote(target.reason || "已定位待补资料，请核对来源后确认保存");
    if (target.kind === "input" && target.dataset && contracts[target.dataset]) {
      const existing = detail?.inputs.find((input) => target.input_id ? input.input_id === target.input_id :
        input.dataset === target.dataset && !!target.record_key && input.record_key === target.record_key &&
        (!target.part_side || input.part_side === target.part_side));
      if (existing) loadInputRevision(existing);
      else switchInputDataset(target.dataset, target.part_side || detail?.task.part_side || "NONE");
      requestAnimationFrame(() => { formRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
        const controls = formRef.current?.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("[data-ppap-field]");
        Array.from(controls || []).find((control) => control.dataset.ppapField === target.field)?.focus(); });
    } else {
      const destination = target.kind === "evidence" ? evidenceRef : target.kind === "applicability" ? applicabilityRef : taskRef;
      requestAnimationFrame(() => { if (destination.current instanceof HTMLDetailsElement) destination.current.open = true;
        destination.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }); });
    }
  }

  async function perform(action: () => Promise<PpapMutation | void>, message: string) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    let completed = false;
    try {
      const result = await action();
      completed = true;
      setNotice(`${message}${result?.idempotent_replay ? "（复用既有运行，未重复生成）" : ""}${result?.separation_warning ? "；编制人与审核人相同，已留痕" : ""}`);
      await loadList();
      if (currentSelection.current) await loadDetail(currentSelection.current);
    } catch (e) {
      const reason = e instanceof Error ? e.message : "PPAP 操作失败";
      if (completed) setNotice(`${message}；页面读取未完成：${reason}。请重新读取当前任务后核对。`);
      else if ((e as { code?: string })?.code === "read_back_failed") setNotice(`操作已提交，回读未完成：${reason}。请先读取当前任务核对，避免重复提交。`);
      else setError(reason);
    }
    finally { setBusy(false); }
  }
  const inputNeedsReadback = inputSaveFeedback.phase === "saved_unverified" || inputSaveFeedback.phase === "uncertain";
  const hasUnsavedInputs = inputEdited || [...inputDrafts.current.values()].some(draft => draft.edited);
  const taskNavigationDisabled = busy || inputNeedsReadback || hasUnsavedInputs;
  const writeDisabled = busy || !canWrite || inputNeedsReadback;
  const inputDisabled = writeDisabled || inputNeedsReadback;
  const isTestTask = detail?.task.data_mode === "TEST";
  const contract = contracts[dataset];
  const artifacts = detail?.artifacts.filter((a) => a.scope === "PACKAGE") || [];
  const blockers = detail?.issues.filter((issue) => issue.status === "OPEN" && issue.blocks_approval) || [];
  const submittedReview = detail?.reviews.slice().reverse().find((review) => review.decision === "SUBMITTED");
  const workflow = detail ? getPpapWorkflow(detail, missing, currentImpact) : null;
  const group = getDatasetGroup(dataset);
  const reviewUnavailable = workflow?.reviewReasons || [];
  const displayedInputs = detail?.inputs.filter(input => input.dataset === dataset) || [];
  function artifactReviewReasons(artifactId: string, deciding = false): string[] {
    if (!workflow || !detail) return ["尚未取得任务及检查结果"];
    const reasons: string[] = [];
    if (workflow.pendingRequirements) reasons.push(`适用性待确认 ${workflow.pendingRequirements} 项`);
    if (workflow.realtimeBlockers === null) reasons.push("实时检查结果尚未取得");
    else if (workflow.realtimeBlockers) reasons.push(`实时检查存在 ${workflow.realtimeBlockers} 项阻断`);
    if (workflow.registeredBlockers) reasons.push(`已登记问题仍有 ${workflow.registeredBlockers} 项阻断`);
    const impact = currentImpact?.comparison_artifact_id === artifactId ? currentImpact : changeImpact?.comparison_artifact_id === artifactId ? changeImpact : null;
    if (impact?.ok !== true || typeof impact.changed !== "boolean") reasons.push("当前资料与该版本是否一致尚未核对");
    else if (impact.changed) reasons.push("资料已变化，需重新编制");
    if (!deciding && ["PENDING_REVIEW", "IN_REVIEW"].includes(detail.task.package_state)) reasons.push("已有版本正在内部审核");
    return reasons;
  }
  const stepLabels: Array<[Tab, string, string]> = [
    ["requirements", "确认要求", workflow?.pendingRequirements ? `待确认 ${workflow.pendingRequirements} 项` : "要求已确认"],
    ["prepare", "准备资料", `已保存 ${detail?.inputs.length || 0} 条输入`],
    ["compile", "编制与检查", workflow?.latestPackage ? `V${workflow.latestPackage.version} · ${stateLabel(workflow.latestPackage.status)}` : "尚未生成"],
    ["review", "审核与归档", stateLabel(detail?.task.package_state || "NOT_SUBMITTED")],
  ];

  async function readSavedInput(context: InputSaveContext, generation: number): Promise<void> {
    const refreshed = await getPpapTask(context.taskId);
    if (generation !== inputSaveGeneration.current || currentSelection.current !== context.taskId) return;
    if (!refreshed.ok || refreshed.task.task_id !== context.taskId) throw new Error("任务回读结果不完整");
    const receipt = context.receipt;
    const returnedId = typeof receipt?.input_id === "string" ? receipt.input_id : "";
    const returnedKey = typeof receipt?.record_key === "string" ? receipt.record_key : context.recordKey;
    const expectedPayload = receipt?.payload && typeof receipt.payload === "object" ? receipt.payload : context.payload;
    const candidates = refreshed.inputs.filter((input) => input.dataset === context.dataset && input.part_side === context.side &&
      input.source_ref === context.sourceRef && input.source_type === context.sourceType &&
      (typeof receipt?.confirmed_by !== "string" || input.confirmed_by === receipt.confirmed_by) &&
      (returnedId ? input.input_id === returnedId : returnedKey ? input.record_key === returnedKey :
        input.source_ref === context.sourceRef && input.source_type === context.sourceType));
    const saved = candidates.length === 1 && stableJson(candidates[0].payload) === stableJson(expectedPayload) ? candidates[0] : null;
    setDetail(refreshed); setSavedInputsOpen(true);
    if (!saved) throw new Error("当前列表中未找到与本次提交内容一致的唯一记录，请核对已保存记录。");
    setHighlightedInputId(saved.input_id); setRecordKey(saved.record_key); setSourceRef(saved.source_ref); setInputSourceType(saved.source_type);
    setAutoOrigins({}); setClearedFields(new Set());
    setNullFields(new Set(Object.entries(saved.payload).filter(([, value]) => value === null).map(([key]) => key)));
    setInputFields(Object.fromEntries(Object.entries(saved.payload).map(([key, value]) => [key,
      value === null ? "" : typeof value === "object" ? jsonText(value) : String(value)])));
    setInputEdited(false); inputSaveContext.current = null;
    inputDrafts.current.delete(`${context.dataset}|${context.side}`);
    setInputSaveFeedback({ phase: "saved", message: "保存成功，已回读确认。资料变更后需重新编制。",
      label: contracts[context.dataset]?.label || context.dataset, record: saved, count: refreshed.inputs.length, created: context.created });
    // Auxiliary refresh failures must not turn a verified save into a failed write.
    void Promise.allSettled([getPpapMissingItems(context.taskId), getPpapChangeImpact(context.taskId), loadList()]).then(([gaps, impact]) => {
      if (generation !== inputSaveGeneration.current || currentSelection.current !== context.taskId) return;
      if (gaps.status === "fulfilled") setMissing(gaps.value);
      if (impact.status === "fulfilled") { setChangeImpact(impact.value); setCurrentImpact(impact.value); }
    });
  }

  async function retryInputReadback() {
    const context = inputSaveContext.current;
    if (!context || inputSaveLock.current || busy) return;
    inputSaveLock.current = true; setBusy(true);
    const generation = inputSaveGeneration.current, previousPhase = inputSaveFeedback.phase;
    setInputSaveFeedback((old) => ({ ...old, phase: "saving", message: "正在重新读取已保存记录，不会再次提交保存。" }));
    try { await readSavedInput(context, generation); }
    catch (cause: unknown) {
      if (generation === inputSaveGeneration.current && currentSelection.current === context.taskId) {
        setInputSaveFeedback((old) => ({ ...old, phase: previousPhase === "uncertain" ? "uncertain" : "saved_unverified",
          message: `记录回读未完成：${cause instanceof Error ? cause.message : "读取失败"}。请重新读取后核对，避免重复提交。` }));
      }
    } finally { inputSaveLock.current = false; setBusy(false); }
  }

  async function saveConfirmedInput(context: InputSaveContext) {
    if (inputSaveLock.current || busy || inputSaveContext.current) return;
    const generation = inputSaveGeneration.current;
    inputSaveLock.current = true; inputSaveContext.current = context; setBusy(true); setError(""); setNotice("");
    setCurrentImpact(null);
    setHighlightedInputId("");
    setInputSaveFeedback({ phase: "saving", label: contracts[context.dataset]?.label || context.dataset, message: "正在保存，请稍候…" });
    try {
      const receipt = await savePpapInput(context.taskId, {dataset: context.dataset, payload: context.payload,
        part_side: context.side, record_key: context.recordKey, source_type: context.sourceType, source_ref: context.sourceRef});
      if (generation !== inputSaveGeneration.current || currentSelection.current !== context.taskId) return;
      context.receipt = receipt; context.created = typeof receipt.created === "boolean" ? receipt.created : undefined;
      try { await readSavedInput(context, generation); }
      catch (cause: unknown) {
        if (generation === inputSaveGeneration.current && currentSelection.current === context.taskId) {
          setInputSaveFeedback({phase: "saved_unverified", label: contracts[context.dataset]?.label || context.dataset,
            message: `保存请求已成功，但记录回读未完成：${cause instanceof Error ? cause.message : "读取失败"}。请重新读取后核对，避免重复提交。`});
        }
      }
    } catch (cause: unknown) {
      if (generation !== inputSaveGeneration.current || currentSelection.current !== context.taskId) return;
      const problem = cause as { code?: string; status?: number };
      const message = cause instanceof Error ? cause.message : "请求失败";
      if (problem?.code === "read_back_failed") {
        setInputSaveFeedback({phase: "saved_unverified", message: `本次写入已提交，但记录回读未完成：${message}。请重新读取后核对。`});
      } else if (cause instanceof TypeError || cause instanceof SyntaxError || (problem?.status || 0) >= 500) {
        setInputSaveFeedback({phase: "uncertain", message: `保存结果未确认：${message}。请先重新读取记录，避免重复提交。`});
      } else {
        inputSaveContext.current = null;
        setInputSaveFeedback({phase: "error", message: `保存失败：${message}。当前填写内容已保留。`});
      }
    } finally { inputSaveLock.current = false; setBusy(false); }
  }

  function submitInput(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inputSaveLock.current || busy || inputSaveContext.current) return;
    if (!canWrite) { setInputSaveFeedback({phase: "error", message: "请先登录具有质量管理权限的项目账号。"}); return; }
    if (!event.currentTarget.checkValidity()) {
      const invalid = Array.from(event.currentTarget.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input,select,textarea")).filter((control) => !control.validity.valid);
      const labels = invalid.slice(0, 3).map((control) => INPUT_LABELS[control.dataset.ppapField || ""] || control.getAttribute("aria-label") || control.closest("label")?.firstChild?.textContent?.trim().replace(/\s*\*$/, "") || "标记字段");
      setInputSaveFeedback({phase: "error", message: `尚未提交，请补充或修正：${labels.join("、")}。`});
      event.currentTarget.reportValidity(); return;
    }
    if (!contract || !sourceRef.trim()) { setInputSaveFeedback({phase: "error", message: "尚未提交，请选择数据集并填写有效来源引用。"}); return; }
    const payload: PpapJson = {};
    try {
      for (const field of [...contract.required, ...contract.optional]) {
        if (nullFields.has(field)) { payload[field] = null; continue; }
        const value = inputFields[field] ?? "";
        if (value === "") continue;
        if (COMPLEX_FIELDS.has(field)) {
          const parsed = parseStudyData(field as PpapStudyField, value);
          if (!parsed.supported && parsed.invalid) throw new Error(parsed.error);
          payload[field] = JSON.parse(value);
        }
        else if (contract.numeric.includes(field)) {
          const number = Number(value);
          if (!Number.isFinite(number)) throw new Error(`${INPUT_LABELS[field] || field}必须填写有效数值`);
          payload[field] = number;
        } else if (contract.boolean.includes(field)) payload[field] = value === "true";
        else payload[field] = value;
      }
    } catch (e) { setInputSaveFeedback({phase: "error", message: `尚未提交：${e instanceof Error ? e.message : "原始数据格式不正确"}`}); return; }
    void saveConfirmedInput({ taskId: selectedId, dataset, side: inputSide, recordKey: recordKey.trim(), payload,
      sourceRef: sourceRef.trim(), sourceType: inputSourceType });
  }

  return <section className="ppap-panel" aria-label="PPAP 编制工作台">
    <div className="ppap-heading"><div><h2>PPAP 编制</h2><p className="muted">整理来源资料、生成草稿、检查缺项和管理审核版本</p></div>
      <button className="button" disabled={writeDisabled || hasUnsavedInputs} onClick={() => setShowCreate((v) => !v)}>新建 PPAP 任务</button></div>
    <details className="ppap-boundary"><summary>模板与使用范围</summary><div className="ppap-notice">{template?.notice || "当前仅支持项目自定义内部模板，客户模板及业务规则待确认。"}<br />
      <span>模板来源：{template?.template_source || "project_defined"}；未经确认的阈值保持待确认（NEEDS_CONFIRMATION），不产生合格结论。</span><p>当前操作账号：{actorId || "未登录"}；{canWrite ? "具有质量管理权限" : "当前角色无质量管理权限，只可查询"}。操作只保存到本项目 PPAP 数据，不修改 ERP/MES。</p></div></details>
    {!hasProjectSession ? <div className="ppap-notice"><span>编制、输入确认、审核和归档需要项目账号登录。当前查询权限由服务端身份校验决定。</span>
      <button className="button secondary" onClick={onLogin}>去登录</button></div> :
      !canWrite && <p className="muted">当前角色无质量管理权限，只可查询。</p>}
    {(error || notice) && <div className="ppap-toast-slot" aria-live="polite">
      <div className={`ppap-toast ${error ? "ppap-toast-error" : "ppap-toast-ok"}`} role={error ? "alert" : "status"}>
        <span>{displaySystemText(error || notice || "")}</span>
        <button type="button" aria-label="关闭提示" onClick={() => { setError(""); setNotice(""); }}>×</button>
      </div>
    </div>}
    {showCreate && <form className="card ppap-card" onSubmit={(event) => {
      event.preventDefault();
      void perform(async () => {
        const result = await createPpapTask({ ...createFields, mode: createMode });
        if (result.task) { currentSelection.current = result.task.task_id; setSelectedId(result.task.task_id); }
        setModeFilter(createMode); setOffset(0);
        setShowCreate(false); setCreateFields({ ...EMPTY_FIELDS }); setCreateMode("REAL"); return result;
      }, "任务已授权启动；请确认业务关联与条目适用性");
    }}><h3>任务基础信息</h3><label>任务模式<select aria-label="新建任务模式" value={createMode} disabled={writeDisabled} onChange={(e) => setCreateMode(e.target.value as PpapMode)}>
      <option value="REAL">正式任务（实际资料）</option><option value="TEST">测试任务（本地功能测试）</option></select></label>
      <p className={createMode === "TEST" ? "ppap-danger" : "muted"}>模式创建后不可切换。{createMode === "TEST" ? "测试任务可编制、内部审核和归档；不能关联真实 ERP/MES、执行真实取证或登记客户处理。测试产物不能用于正式任务。" : "正式任务只允许实际资料与明确来源；演示、模拟或测试数据须另建测试任务。"}</p>
      <BaseFields fields={createFields} onChange={setCreateFields} disabled={writeDisabled} />
      <p className="muted">负责人及操作身份取自当前项目会话。左右件各自建立任务，不根据名称或列表顺序自动匹配。</p>
      <button className="button" disabled={writeDisabled}>确认启动任务</button></form>}
    {(!detail || showTaskList) && <div className="ppap-task-picker-heading"><div><h3>选择要编制的任务</h3><p className="muted">按零件号或任务范围筛选；左右件各自独立成任务。</p></div>
      {detail && <button type="button" className="button secondary" disabled={taskNavigationDisabled} onClick={() => setShowTaskList(false)}>收起任务列表</button>}</div>}
    <div hidden={!!selectedId && !showTaskList} className="ppap-task-browser">
    <div className="ppap-search" role="search">
      <label className="ppap-search-field">按零件号查询<input placeholder="例如 BD-2401 / TEST-PPAP-…" value={filter} onChange={(e) => setFilter(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { setOffset(0); setQuery(filter); } }} /></label>
      <label className="ppap-search-field ppap-search-scope">任务范围<select aria-label="任务模式筛选" value={modeFilter} disabled={taskNavigationDisabled} onChange={(e) => { setOffset(0); setModeFilter(e.target.value as PpapMode | ""); }}>
        <option value="REAL">正式任务</option><option value="TEST">测试任务</option><option value="">全部任务</option></select></label>
      <div className="ppap-search-actions">
        <button className="button" disabled={taskNavigationDisabled} onClick={() => { setOffset(0); setQuery(filter); }}>查询</button>
        <button className="button secondary" disabled={taskNavigationDisabled || loading} onClick={() => void perform(async () => { await loadList(); }, "任务列表已刷新")}>{loading ? "读取中…" : "刷新列表"}</button>
      </div>
    </div>
    {loading && <p className="ppap-loading" role="status">正在读取 PPAP 资料…</p>}
    {list && <div className="ppap-task-list">
      {!list.items.length && <div className="ppap-empty"><strong>当前查询范围内没有 PPAP 任务</strong><p className="muted">{modeFilter === "TEST" ? "测试任务需先运行种子脚本创建；也可切换「全部任务」查看正式任务。" : "可切换任务范围，或新建 PPAP 任务。"}</p></div>}
      {list.items.map((task) => {
        const blocking = task.blocking_issue_count;
        return <button key={task.task_id} className={`ppap-task-item ${selectedId === task.task_id ? "selected" : ""}`}
          aria-pressed={selectedId === task.task_id} disabled={taskNavigationDisabled} onClick={() => { setSelectedId(task.task_id); setTab("prepare"); setShowTaskList(false); }}>
          <span className="ppap-task-item-main">
            <span className="ppap-task-item-title"><strong>{task.part_no}</strong><span className="ppap-chip">{SIDE_LABELS[task.part_side]}</span>
              <span className={`ppap-chip ${task.data_mode === "TEST" ? "ppap-chip-test" : "ppap-chip-real"}`}>{modeLabel(task.data_mode)}</span></span>
            <span className="ppap-task-item-sub">{task.part_name} · {task.customer_name}</span>
            <span className="ppap-task-item-id">{task.task_id}</span>
          </span>
          <span className="ppap-task-item-status">
            <span className={`ppap-state-dot ppap-state-${String(task.run_state).toLowerCase()}`} aria-hidden="true" />
            <span>{stateLabel(task.run_state)}</span>
            <small>{stateLabel(task.package_state)}</small>
            {/* 未运行过检查时阻断数为 0 只代表"还没查"，不能显示成"无阻断"。 */}
            {!task.checks_run ? <span className="ppap-chip ppap-chip-idle">尚未检查</span>
              : <span className={`ppap-chip ${blocking ? "ppap-chip-block" : "ppap-chip-ok"}`}>{blocking ? `阻断 ${blocking}` : "无阻断"}</span>}
          </span>
        </button>;
      })}
      <div className="ppap-actions ppap-list-footer"><span className="muted">共 {list.total} 项；当前 {offset + (list.items.length ? 1 : 0)}–{offset + list.items.length}</span>
        <button className="button secondary" disabled={taskNavigationDisabled || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button>
        <button className="button secondary" disabled={taskNavigationDisabled || offset + list.items.length >= list.total} onClick={() => setOffset(offset + 20)}>下一页</button></div>
    </div>}
    </div>
    {detail && <>
      <div className="card ppap-card ppap-summary"><div className="ppap-heading"><div><h3>{detail.task.part_name}</h3><p className="muted">{detail.task.part_no} · {SIDE_LABELS[detail.task.part_side]} · 图纸 {detail.task.drawing_version || "待补"}</p></div><div className="ppap-actions"><strong className={isTestTask ? "ppap-mode-test" : "ppap-mode-real"}>{modeLabel(detail.task.data_mode)}</strong><button type="button" className="button secondary" disabled={taskNavigationDisabled} onClick={() => setShowTaskList(value => !value)}>切换任务</button></div></div>
        <div className="ppap-workflow-facts"><span>当前编制：{stateLabel(detail.task.run_state)}</span><span>内部审核：{stateLabel(detail.task.package_state)}</span><span>客户处理：{stateLabel(detail.task.customer_state)}</span></div>
        <details><summary>任务详情</summary><p className="muted">{detail.task.task_id} · {detail.task.template_source}</p><p>提交原因：{detail.task.submission_reason || "待确认"}</p><p>提交等级：{detail.task.submission_level || "待确认"}</p><p className="muted">模式创建后不可切换。内部批准与客户接受分别记录；PPAP 不改变 NCR 关闭条件及发运规则。</p>{isTestTask && <p className="ppap-danger">测试任务不执行真实取证或客户登记，内部批准与归档均为测试记录；产物不能用于正式任务或客户提交。</p>}</details>
      </div>
      <nav className="ppap-step-navigation" aria-label="PPAP 任务步骤">{stepLabels.map(([key, label, status], index) => <button type="button" key={key} aria-label={label} aria-pressed={tab === key} disabled={busy} className={`ppap-step-button ${tab === key ? "active" : ""}`} onClick={() => setTab(key)}><span className="ppap-step-number" aria-hidden="true">{index + 1}</span><span>{label}<small>{status}</small></span></button>)}</nav>
      <div className="ppap-next-action" role="status"><div>
        <strong>{inputNeedsReadback ? "请先核对本次保存结果" : hasUnsavedInputs ? "有录入修改尚未保存" : `建议下一步：${workflow?.next.label}`}</strong>
        <p>{inputNeedsReadback ? "保存结果还未确认；请重新读取，避免重复提交。当前任务和录入草稿已保留。" : hasUnsavedInputs ? "请确认保存，或明确清除该录入草稿后再切换任务；已保存的业务记录不会被清除。" : workflow?.next.description}</p>
      </div><button type="button" className="button" disabled={busy} onClick={() => {
        if (inputNeedsReadback || hasUnsavedInputs) {
          setTab("prepare"); setPreparationSection("inputs");
          if (!inputNeedsReadback && !inputEdited) {
            const cached = [...inputDrafts.current.entries()].find(([, draft]) => draft.edited);
            if (cached) {
              if (cached[0] === `${dataset}|${inputSide}`) restoreInputDraft(cached[1]);
              else switchInputDataset(cached[0].split("|")[0], cached[0].split("|")[1] as PpapSide);
            }
          }
        } else if (workflow?.next.navigation) navigateToMissing(workflow.next.navigation);
        else if (workflow) {
          setTab(workflow.next.stage);
          // 进入"确认要求"后必须把适用性表格滚到视野内，否则长页面上点击看起来毫无反应。
          if (workflow.next.stage === "requirements") {
            requestAnimationFrame(() => {
              if (applicabilityRef.current instanceof HTMLDetailsElement) applicabilityRef.current.open = true;
              applicabilityRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
            });
          }
        }
      }}>{inputNeedsReadback ? "返回核对已保存记录" : hasUnsavedInputs ? "返回未保存的录入" : workflow?.next.label}</button></div>
      <section className="ppap-stage-panel" aria-label="确认要求工作区" hidden={tab !== "requirements"}><div className="ppap-stage-intro"><h3>确认要求</h3><p>明确零件、图纸版本、提交原因，以及本次需要提交或留存哪些文件。</p></div>
        {navigationNote && <p className="ppap-notice" role="status">{navigationNote}</p>}
        <details ref={taskRef} className="card ppap-card"><summary>任务信息与业务关联</summary>
          <form onSubmit={(event) => { event.preventDefault(); void perform(() => updatePpapTask(selectedId, { ...editFields, ...businessFields }), "任务信息已更新"); }}>
            <BaseFields fields={editFields} onChange={setEditFields} disabled={writeDisabled} />
            <h4>已确认的业务关联</h4><div className="ppap-form-grid">{Object.entries(BUSINESS_LABELS).map(([key, label]) => <label key={key}>{label}
              <input disabled={writeDisabled || (isTestTask && TEST_BUSINESS_KEYS.has(key))} value={businessFields[key] || ""} onChange={(e) => setBusinessFields({ ...businessFields, [key]: e.target.value })} /></label>)}</div>
            {isTestTask && <p className="ppap-danger">测试任务禁止关联真实 ERP/MES 业务对象。</p>}
            <button className="button" disabled={writeDisabled}>保存已确认关联</button></form>
        </details>
        <details ref={applicabilityRef} className="card ppap-card" open={!!workflow?.pendingRequirements}><summary>条目适用性确认（已确认 {detail.applicability.confirmed}/{detail.applicability.total} 项）</summary><p className="muted">建议值只作参考；请逐项选择并说明依据，不适用的条目必须填写依据。</p>
          <div className="ppap-applicability-toolbar">
            <span className="muted">本页已选择 <strong>{Object.values(decisions).filter((v) => v.requirement !== "").length}</strong> 项</span>
            <div className="ppap-actions">
              <button type="button" className="button secondary" disabled={writeDisabled}
                onClick={() => setDecisions(Object.fromEntries(detail.elements.map((element) => [element.element_id,
                  { requirement: element.suggested_requirement as PpapRequirement, basis: decisions[element.element_id]?.basis || `采用建议值 ${REQUIREMENT_LABELS[element.suggested_requirement] || ""}（仍需人工确认）` }])))
                }>填入全部建议值</button>
              <button type="button" className="button secondary" disabled={writeDisabled}
                onClick={() => setDecisions({})}>清空本页选择</button>
            </div>
          </div>
          <div className="ppap-table-scroll"><table className="ppap-table"><thead><tr><th>条目 / 适用条件</th><th>适用性</th><th>依据</th><th>编制状态</th></tr></thead>
            <tbody>{detail.elements.map((element) => <tr key={element.element_id} className={decisions[element.element_id]?.requirement ? "ppap-row-chosen" : ""}><td><strong>{element.title}</strong><small>{element.applicability_condition || element.notes}</small>
              <small>建议：{REQUIREMENT_LABELS[element.suggested_requirement] || "待确认"}</small></td><td>
              <select aria-label={`${element.title}适用性`} value={decisions[element.element_id]?.requirement || ""} disabled={writeDisabled}
                onChange={(e) => setDecisions({ ...decisions, [element.element_id]: { ...decisions[element.element_id], requirement: e.target.value as PpapRequirement } })}>
                {Object.entries(REQUIREMENT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
              <td><textarea aria-label={`${element.title}适用依据`} rows={2} value={decisions[element.element_id]?.basis || ""} disabled={writeDisabled}
                onChange={(e) => setDecisions({ ...decisions, [element.element_id]: { ...decisions[element.element_id], basis: e.target.value } })} /></td>
              <td>{stateLabel(element.status)}<small>{element.status_reason}</small></td></tr>)}</tbody></table></div>
          <div className="ppap-sticky-actions">
            <span className="muted">已确认 {detail.applicability.confirmed}/{detail.applicability.total}；未确认条目会继续阻断批准。</span>
          <button className="button" aria-label="确认当前选择（将提交本页已选择的适用性）" disabled={writeDisabled || !Object.values(decisions).some((v) => v.requirement !== "")} onClick={() => {
            const chosen = Object.entries(decisions).filter(([, value]) => value.requirement !== "").map(([element_id, value]) => ({ element_id, ...value }));
            if (chosen.some((v) => v.requirement === "not_applicable" && !v.basis.trim())) { setError("不适用的条目必须填写依据"); return; }
            void perform(() => confirmPpapApplicability(selectedId, chosen), "适用性已确认；尚未选择的条目仍待确认");
          }}>{busy ? "正在提交…" : `确认当前选择（${Object.values(decisions).filter((v) => v.requirement !== "").length} 项）`}</button>
          </div>
        </details>
      </section>
      <section className="ppap-stage-panel" aria-label="准备资料工作区" hidden={tab !== "prepare"}><div className="ppap-stage-intro"><h3>准备资料</h3><p>选择资料类别补录，或引用已取得的原始文件；保存后再编制与检查。</p></div>
        {navigationNote && <details className="ppap-navigation-note"><summary>定位依据</summary><p className="muted">{navigationNote}</p></details>}
        <nav className="ppap-preparation-tabs" aria-label="资料准备入口"><button type="button" className={`button ${preparationSection === "inputs" ? "" : "secondary"}`} aria-label="录入资料" aria-pressed={preparationSection === "inputs"} disabled={busy} onClick={() => setPreparationSection("inputs")}>录入资料</button><button type="button" className={`button ${preparationSection === "evidence" ? "" : "secondary"}`} aria-label="来源资料" aria-pressed={preparationSection === "evidence"} disabled={busy} onClick={() => setPreparationSection("evidence")}>来源资料</button></nav>
        <div hidden={preparationSection !== "evidence"}>
        <div ref={evidenceRef} className="card ppap-card"><div className="ppap-heading"><h3>跨岗取证</h3><button className="button" disabled={writeDisabled || isTestTask}
          onClick={() => void perform(() => collectPpapEvidence(selectedId), "取证运行完成，请核对来源三态和覆盖范围")}>按已授权计划取证</button></div>
          <p className="muted">有效空结果（EMPTY）表示来源无记录，不代表资料齐全；读取失败（ERROR）需处理后重试。</p>
          {isTestTask && <p className="ppap-danger">测试任务不执行真实取证，请登记本地测试资料。</p>}
          {plan && <details><summary>查看取证计划</summary><ul>{plan.collectors.map((collector) => <li key={collector.collector_id}><strong>{displaySystemText(collector.label)}</strong>：
            {collector.resolvable ? "可读取" : `需补业务关联：${collector.missing_business_keys.join("、")}`} {displaySystemText(collector.note)}</li>)}</ul></details>}
          <div className="ppap-table-scroll"><table className="ppap-table"><thead><tr><th>来源 / 记录</th><th>读取结果</th><th>内容与范围</th><th>追溯</th></tr></thead><tbody>
            {detail.evidence.map((evidence) => <tr key={evidence.evidence_id}><td>{displaySystemText(evidence.source_type)}<small>{displaySystemText(evidence.source_id)}</small></td>
              <td><strong className={evidence.read_status === "ERROR" ? "ppap-danger" : ""}>{evidence.read_status === "OK" ? "已读取 OK" : evidence.read_status === "EMPTY" ? "无有效记录 EMPTY" : "读取失败 ERROR"}</strong>
                <small>{evidence.review_state}</small></td><td>{displaySystemText(evidence.summary)}<small>绑定条目：{evidence.element_ids?.length ? evidence.element_ids.map((id) => detail.elements.find((element) => element.element_id === id)?.title || id).join("、") : "未绑定（不计条目资料就绪）"}</small>{evidence.truncated && <strong className="ppap-danger">读取范围截断，不能视为完整</strong>}
                {evidence.error && <pre>{displaySystemText(jsonText(evidence.error))}</pre>}</td><td>{evidence.collected_by}<small>{displaySystemText(evidence.locator)}</small><small className="ppap-hash">{evidence.content_hash || "内容未校验"}</small></td></tr>)}</tbody></table></div>
          {!detail.evidence.length && <p className="muted">尚未登记证据，不能据此判断资料完整。</p>}
          <details><summary>登记原件或引用已批准包</summary><form onSubmit={(event) => { event.preventDefault(); void perform(() => registerPpapEvidence(selectedId, { ...evidenceFields, element_ids: evidenceElementIds }), "证据已登记，请核对内容校验状态及绑定条目"); }}>
            <div className="ppap-form-grid"><label>来源类别<select disabled={writeDisabled} value={evidenceFields.source_type} onChange={(e) => setEvidenceFields({ ...evidenceFields, source_type: e.target.value })}>
              <option value="user_upload">原始文件登记</option><option value="internal_ppap_package">既有内部批准包引用</option></select></label>
              <label>来源编号<input required disabled={writeDisabled} value={evidenceFields.source_id} onChange={(e) => setEvidenceFields({ ...evidenceFields, source_id: e.target.value })} /></label>
              <label>受控存储相对路径 / 外部编号 / 产物 ID<input required disabled={writeDisabled} value={evidenceFields.locator} onChange={(e) => setEvidenceFields({ ...evidenceFields, locator: e.target.value })} /></label>
              <label>原件文档版本（设计原件必填）<input aria-label="原件文档版本" disabled={writeDisabled} value={evidenceFields.document_version} onChange={(e) => setEvidenceFields({ ...evidenceFields, document_version: e.target.value })} /></label>
              <label className="ppap-wide">资料说明<textarea required disabled={writeDisabled} value={evidenceFields.summary} onChange={(e) => setEvidenceFields({ ...evidenceFields, summary: e.target.value })} /></label></div>
            <p className="muted">此处登记已有文件引用；外部编号只登记来源，未经内容校验不能作为已取得原件。不会生成签字、资质或工程批准。</p>
            <fieldset className="ppap-evidence-bindings"><legend>该原件实际对应的条目</legend><p className="muted">逐项勾选实际对应的条目；未选可登记，但不计任何条目的就绪或来源满足。</p>
              <div className="ppap-checkbox-grid">{detail.elements.map((element) => <label key={element.element_id}><input type="checkbox" disabled={writeDisabled}
                aria-label={`原件绑定${element.title}`} checked={evidenceElementIds.includes(element.element_id)} onChange={(event) => setEvidenceElementIds((old) => event.target.checked ? [...old, element.element_id] : old.filter((id) => id !== element.element_id))} />{element.title}</label>)}</div>
            </fieldset>
            <button className="button" disabled={writeDisabled}>确认登记证据</button></form></details>
        </div>
        </div>
        <div hidden={preparationSection !== "inputs"} className="ppap-input-workspace">
          <nav className="ppap-input-categories" aria-label="资料类别">{PPAP_DATASET_GROUPS.map(item => <button type="button" key={item.id} aria-label={item.label} aria-pressed={group === item.id} disabled={busy || inputNeedsReadback || !item.datasets.some(name => contracts[name])} onClick={() => { const first = item.datasets.find(name => contracts[name]); if (first) switchInputDataset(first); }}><strong>{item.label}</strong><small>{item.description}</small><span>已保存 {detail.inputs.filter(input => item.datasets.includes(input.dataset)).length} 条</span></button>)}</nav>
        <div ref={formRef} className="card ppap-card" onInput={() => setInputEdited(true)}><h3>工程与检验输入确认</h3><p className="muted">先识别任务和同侧已确认资料，再确认补充内容；实测值与判定规则须有实际来源。</p>
          <button type="button" className="button secondary" disabled={inputDisabled} onClick={() => resetInputForm(dataset)}>新增一条记录</button>
          {inputEdited && <button type="button" className="button secondary" disabled={inputDisabled} onClick={() => { inputDrafts.current.delete(`${dataset}|${inputSide}`); const saved = detail.inputs.find(input => input.dataset === dataset && input.part_side === inputSide && input.record_key === recordKey); if (saved) loadInputRevision(saved); else resetInputForm(dataset, inputSide); }}>清除当前录入草稿</button>}
          {assistanceLoading && <p className="muted" role="status">正在识别已有资料…</p>}
          {assistanceError && <p className="ppap-danger" role="alert">识别失败：{displaySystemText(assistanceError)}；请核对来源，不能据此判断没有资料。</p>}
          {assistance && <div className="ppap-recognition">
            {assistance.notices.map((message, index) => <p className="muted" key={index}>{displaySystemText(message)}</p>)}
            {!!assistance.conflicts.length && <p className="ppap-danger">已有资料存在冲突，已停止相关预填，请核对原记录。</p>}
            {assistance.candidates.length > 0 && <label>从已确认特性选择<select aria-label="从已确认特性选择" value={selectedCharacteristic} disabled={inputDisabled || !!recordKey}
              onChange={(event) => changeInputField("characteristic_id", event.target.value)}><option value="">请选择特性</option>
              {assistance.candidates.map((candidate) => <option key={candidate.input_id} value={candidate.characteristic_id} disabled={!!candidate.conflict}>
                {candidate.characteristic_id} · {candidate.name} · {SIDE_LABELS[candidate.part_side]}{candidate.conflict ? "（冲突）" : ""}</option>)}
            </select></label>}
            {!!assistance.suggestions.length && <p className="muted">识别结果只填入空字段；保存前请核对。原始检测数据的来源仍须填写。</p>}
          </div>}
          <form noValidate onSubmit={submitInput} aria-describedby="ppap-input-save-feedback" aria-busy={inputSaveFeedback.phase === "saving"}><div className="ppap-form-grid"><label>资料类型<select aria-label="数据集" value={dataset} disabled={busy || inputNeedsReadback} onChange={(e) => switchInputDataset(e.target.value)}>
            {Object.entries(contracts).map(([name, schema]) => <option key={name} value={name}>{schema.label}</option>)}</select></label>
            <label>零件侧别<select aria-label="数据侧别" value={inputSide} disabled={inputDisabled || !!recordKey} onChange={(e) => {
              resetInputForm(dataset, e.target.value as PpapSide);
            }}>
              {Object.entries(SIDE_LABELS).map(([side, label]) => <option key={side} value={side}>{label}</option>)}</select></label>
            <label>来源引用 *<input required disabled={inputDisabled} value={sourceRef} onChange={(e) => { setSourceRef(e.target.value); setInputEdited(true); }} /></label>
            <details className="ppap-wide"><summary>记录标识与来源详情</summary><label>记录键（可留空，由系统按实际内容生成）<input disabled={inputDisabled} value={recordKey} onChange={(e) => { setRecordKey(e.target.value); setInputEdited(true); }} /></label><p className="muted">来源类别：{inputSourceType}；修订原记录沿用原标识。</p></details>
            {recordKey && <p className="muted ppap-wide">正在修订已保存记录；不自动带入其他记录的测量值。</p>}
            {contract && [...contract.required, ...contract.optional].map((field) => <div key={field} className={`ppap-input-field ${COMPLEX_FIELDS.has(field) ? "ppap-wide" : ""}`}><label>
              {INPUT_LABELS[field] || field}{contract.required.includes(field) ? " *" : ""}
              {COMPLEX_FIELDS.has(field) ? null :
                contract.boolean.includes(field) ? <select data-ppap-field={field} aria-label={INPUT_LABELS[field] || field} required={contract.required.includes(field)} disabled={inputDisabled || nullFields.has(field)} value={inputFields[field] || ""} onChange={(e) => changeInputField(field, e.target.value)}>
                  <option value="">未填写</option><option value="true">是</option><option value="false">否</option></select> :
                  <input data-ppap-field={field} aria-label={INPUT_LABELS[field] || field} required={contract.required.includes(field)} disabled={inputDisabled || nullFields.has(field)} type={contract.numeric.includes(field) ? "number" : "text"} step="any"
                    value={inputFields[field] || ""} onChange={(e) => changeInputField(field, e.target.value)} />}</label>
              {COMPLEX_FIELDS.has(field) && <PpapStudyEditor key={`${dataset}|${recordKey}|${inputSide}|${inputEditorRevision}|${field}`} field={field as "measurements" | "values" | "subgroups" | "photo_refs"} value={inputFields[field] || ""}
                onChange={(value) => changeInputField(field, value)} disabled={inputDisabled || nullFields.has(field)} />}
              {autoOrigins[field] && <small className="ppap-field-source">已识别，待确认 · 来源：{displaySystemText(autoOrigins[field].source_ref)} · {displaySystemText(autoOrigins[field].source_type)}</small>}
              {contract.optional.includes(field) && <button className="ppap-null-button" type="button" disabled={inputDisabled} aria-pressed={nullFields.has(field)}
                aria-label={`${INPUT_LABELS[field] || field}确认无值`} onClick={() => { setInputEdited(true); setNullFields((old) => { const next = new Set(old); if (next.has(field)) next.delete(field); else next.add(field); return next; }); }}>
                {nullFields.has(field) ? "已确认无值（null），点击恢复填写" : "确认此项无值"}</button>}</div>)}</div>
            <button className="button" disabled={inputDisabled || !contract || assistanceLoading}>{inputSaveFeedback.phase === "saving" ? "正在保存…" : "确认并保存输入"}</button></form>
          <div id="ppap-input-save-feedback" className={`ppap-save-feedback ppap-save-${inputEdited && inputSaveFeedback.phase === "saved" ? "idle" : inputSaveFeedback.phase}`}
            role={inputSaveFeedback.phase === "error" ? "alert" : "status"} aria-live={inputSaveFeedback.phase === "error" ? "assertive" : "polite"} aria-atomic="true">
            <strong>{inputSaveFeedback.phase === "saving" ? "正在处理" : inputEdited && inputSaveFeedback.phase === "saved" ? "有修改尚未保存" :
              inputSaveFeedback.phase === "saved" ? "保存成功" : inputSaveFeedback.phase === "error" ? "未保存" :
                inputNeedsReadback ? "请核对保存结果" : inputEdited ? "当前填写尚未保存" : "待确认"}</strong>
            <p>{inputEdited && inputSaveFeedback.phase === "saved" ? "下方记录是上次已保存内容；当前修改需再次确认保存。" : inputSaveFeedback.message}</p>
            {inputSaveFeedback.record && <div className="ppap-save-receipt">
              <span>{inputSaveFeedback.created === true ? "已新增" : inputSaveFeedback.created === false ? "已修订" : "已确认"}：{inputSaveFeedback.label} · {SIDE_LABELS[inputSaveFeedback.record.part_side]}</span>
              <span>记录键：{inputSaveFeedback.record.record_key}</span>
              <span>确认人：{inputSaveFeedback.record.confirmed_by} · 保存时间：{savedTime(inputSaveFeedback.record.confirmed_at)}</span>
              <span>已确认输入共 {inputSaveFeedback.count} 条。</span>
            </div>}
            {inputNeedsReadback && <button type="button" className="button secondary" disabled={busy} onClick={() => void retryInputReadback()}>重新读取已保存记录</button>}
            {inputSaveFeedback.phase === "saved" && <button type="button" className="button secondary" onClick={() => { setSavedInputsOpen(true); savedInputsRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }}>查看本次保存记录</button>}
          </div>
          <details ref={savedInputsRef} open={savedInputsOpen} onToggle={(event) => setSavedInputsOpen(event.currentTarget.open)} aria-label="已确认输入列表"><summary>已确认输入（{displayedInputs.length} 条）</summary><p className="muted">显示当前数据集；全任务共 {detail.inputs.length} 条输入。</p>{displayedInputs.map((input) => <div key={input.input_id} className={`ppap-record${input.input_id === highlightedInputId ? " ppap-record-saved" : ""}`} data-saved={input.input_id === highlightedInputId ? "true" : undefined}>
            <strong>{contracts[input.dataset]?.label || input.dataset} · {SIDE_LABELS[input.part_side]}</strong><span>{input.record_key}</span>
            {input.input_id === highlightedInputId && <span className="ppap-saved-badge">本次已保存并回读</span>}
            <small>来源：{input.source_ref || "缺失"} · 确认人：{input.confirmed_by} · {savedTime(input.confirmed_at)}</small><PpapRecordSummary payload={input.payload} labels={INPUT_LABELS} />
            <button className="button secondary" disabled={inputDisabled} onClick={() => loadInputRevision(input)}>载入修订</button>
          </div>)}</details>
        </div>
        </div>
        <div className="ppap-stage-footer"><span className="muted">资料未齐也可生成草稿查看缺项，草稿仍需检查与审核。</span><button type="button" className="button" disabled={busy} onClick={() => setTab("compile")}>进入编制与检查 →</button></div>
      </section>
      {tab === "compile" && <>
        <div className="card ppap-card"><h3>编制与一致性检查</h3><p className="muted">生成未批准草稿；缺项保留为空并列入清单。同一输入快照重复编制复用既有运行，修改输入后生成新版本。</p>
          <div className="ppap-actions"><button className="button" disabled={writeDisabled} onClick={() => void perform(() => compilePpapTask(selectedId), "编制运行完成，请查看阻断项及版本")}>请求编制草稿</button>
            <button className="button secondary" disabled={writeDisabled} onClick={() => void perform(() => checkPpapTask(selectedId), "一致性检查已更新")}>运行一致性检查</button></div>
          <div className="ppap-compile-result"><strong>{workflow?.latestPackage ? `最新资料包 V${workflow.latestPackage.version}` : "尚未生成资料包"}</strong><span>运行：{stateLabel(detail.task.run_state)} · 实时阻断：{workflow?.realtimeBlockers ?? "尚未读取"}</span><p className="muted">有阻断的草稿仍可查看和下载；生成草稿不代表获得批准。</p></div>
        </div>
        <div className="card ppap-card"><h3>缺项与冲突</h3><p className="muted">数据修正后应重新检查。人工接受会保留记录，重新检出时重开；接受记录不能直接代替必需资料。</p>
          <PpapMissingItemsView report={missing} elements={detail.elements} issues={detail.issues} onNavigate={navigateToMissing} disabled={writeDisabled} />
          <details><summary>已登记问题与处理记录（{detail.issues.length} 项）</summary>
          {!detail.issues.length && <p>当前没有持久化检查记录；请查看实时缺项分析，并结合取证覆盖和适用性确认判断完整性。</p>}
          {detail.issues.map((issue) => <div key={issue.issue_id} className={`ppap-record ${issue.blocks_approval && issue.status === "OPEN" ? "ppap-blocker" : ""}`}>
            <strong>{displaySystemText(issue.title)}</strong><div>{issue.severity} · {stateLabel(issue.status)}{issue.blocks_approval && " · 阻断批准"} · {issue.element_id}</div>
            <p>{displaySystemText(issue.detail)}</p><small>依据：{displaySystemText(issue.basis)}；责任岗位：{issue.responsible_role}</small>{issue.required_evidence && <p>待补证据：{displaySystemText(issue.required_evidence)}</p>}
            {issue.resolution_note && <p>处理说明：{issue.resolution_note}</p>}
            {issue.status === "OPEN" && <button className="button secondary" disabled={writeDisabled} onClick={() => { setIssueId(issue.issue_id); setIssueNote(""); }}>处理此项</button>}
            {issueId === issue.issue_id && <div><label>修正或接受依据<textarea aria-label="问题处理说明" value={issueNote} disabled={writeDisabled} onChange={(e) => setIssueNote(e.target.value)} /></label>
              <div className="ppap-actions"><button className="button" disabled={writeDisabled || !issueNote.trim()} onClick={() => void perform(() => resolvePpapIssue(selectedId, issue.issue_id, "RESOLVED", issueNote), "处理记录已保存；请重新检查数据")}>确认数据已修正</button>
                <button className="button secondary" disabled={writeDisabled || !issueNote.trim()} onClick={() => void perform(() => resolvePpapIssue(selectedId, issue.issue_id, "WAIVED", issueNote), "人工接受依据已留痕")}>人工接受并留痕</button></div></div>}
          </div>)}
          </details>
        </div>
        <div className="card ppap-card"><details><summary>条目草稿、检查依据与版本修改对照</summary>
          <PpapVersionDiffView impact={changeImpact} artifacts={artifacts} elements={detail.elements} compareArtifactId={compareArtifactId} onCompare={(id) => void compareVersion(id)} loading={compareLoading} />
          <h4>条目内容预览</h4>{detail.elements.map((element) => <details key={element.element_id} className="ppap-record"><summary>{element.title} · {stateLabel(element.status)}</summary>
            <p>{element.status_reason}</p><p className="muted">必需输入：{element.required_inputs.join("、") || "按条目证据与基础信息编制"}；责任岗位：{element.responsible_role}</p>
            <pre>{displaySystemText(jsonText(element.content || {}))}</pre><details><summary>检查依据</summary><pre>{displaySystemText(jsonText(element.check_result))}</pre></details></details>)}
        </details></div>
      </>}
      {tab === "review" && <>
        <div className="card ppap-card"><h3>产物版本与内部审核</h3><p className="muted">提交审核须无阻断且输入快照一致。批准后该版本冻结，后续变化生成新草稿。</p>
          {detail.task.package_state === "RETURNED" && <div className="ppap-review-reasons"><strong>该版本已退回修改</strong><p>{[...detail.reviews].reverse().find(review => review.decision === "RETURNED")?.comments || "请展开审核记录核对退回依据。"}</p><button type="button" className="button secondary" disabled={busy} onClick={() => setTab("prepare")}>按意见准备资料</button></div>}
          {reviewUnavailable.length > 0 && workflow?.latestPackage?.status === "DRAFT" && <div className="ppap-review-reasons"><strong>当前草稿尚不能提交审核</strong><ul>{reviewUnavailable.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
          <label>审核 / 归档意见<textarea value={reviewComment} disabled={writeDisabled} onChange={(e) => setReviewComment(e.target.value)} /></label>
          {!artifacts.length && <p>尚无资料包产物，请先请求编制。</p>}
          {[...artifacts].sort((a, b) => b.version - a.version).map((artifact) => <details key={artifact.artifact_id} className="ppap-record" open={artifact.artifact_id === workflow?.latestPackage?.artifact_id}><summary>V{artifact.version} · {stateLabel(artifact.status)}</summary><strong>{artifact.file_name}</strong>
            <div>{stateLabel(artifact.status)}{artifact.frozen_at && ` · 已冻结 ${artifact.frozen_at}`}</div><details><summary>文件与来源指纹</summary><small className="ppap-hash">内容指纹：{artifact.content_hash}</small><small className="ppap-hash">输入快照：{artifact.input_snapshot_hash}</small></details><div className="ppap-actions">
              <button className="button secondary" disabled={busy} onClick={() => void perform(() => downloadPpapArtifact(selectedId, artifact.artifact_id, artifact.file_name), "下载完成，文件对应所选版本")}>下载 V{artifact.version}</button>
              {artifact.status === "DRAFT" && <button className="button" disabled={writeDisabled || artifactReviewReasons(artifact.artifact_id).length > 0}
                onClick={() => void perform(() => submitPpapReview(selectedId, artifact.artifact_id, reviewComment), "该版本已提交内部审核")}>提交 V{artifact.version} 审核</button>}
              {artifact.status === "DRAFT" && <button type="button" className="button secondary" disabled={busy} onClick={() => void compareVersion(artifact.artifact_id)}>核对 V{artifact.version} 资料一致性</button>}
              {artifact.status === "APPROVED" && <button className="button" disabled={writeDisabled} onClick={() => void perform(() => archivePpapTask(selectedId, artifact.artifact_id, reviewComment), "已批准版本已归档")}>归档 V{artifact.version}</button>}
            </div>{artifact.status === "DRAFT" && artifactReviewReasons(artifact.artifact_id).length > 0 && <p className="ppap-danger">{artifactReviewReasons(artifact.artifact_id).join("；")}</p>}</details>)}
          {blockers.length > 0 && <p className="ppap-danger">存在 {blockers.length} 个未处理阻断项，不能提交批准。</p>}
          {submittedReview && ["PENDING_REVIEW", "IN_REVIEW"].includes(detail.task.package_state) && <div className="ppap-record"><h4>审核 V{submittedReview.artifact_version}</h4>
            <p>审核人来自当前会话：{actorId || "尚未登录"}。{submittedReview.author === actorId && "编制人与审核人相同，决定会记录岗位分离提示。"}</p>
            <div className="ppap-actions"><button className="button" disabled={writeDisabled || artifactReviewReasons(submittedReview.artifact_id, true).length > 0 || !reviewComment.trim()} onClick={() => void perform(() => decidePpapReview(selectedId, submittedReview.review_id, "APPROVED", reviewComment), "审核决定已记录，产物版本已冻结")}>批准此版本</button>
              <button className="button secondary" disabled={writeDisabled || !reviewComment.trim()} onClick={() => void perform(() => decidePpapReview(selectedId, submittedReview.review_id, "RETURNED", reviewComment), "已退回，意见保留在审核记录")}>退回修改</button></div></div>}
          <details><summary>审核记录（{detail.reviews.length} 条）</summary>{detail.reviews.map((review) => <div key={review.review_id} className="ppap-record"><strong>V{review.artifact_version} · {stateLabel(review.decision)}</strong>
            <span>审核人：{review.reviewer} · {review.created_at}</span><p>{review.comments}</p>{review.separation_warning && <p className="ppap-danger">编制人与审核人相同（{review.separation_warning}），未声明满足岗位分离。</p>}</div>)}</details>
        </div>
        <details className="card ppap-card"><summary>客户实际处理</summary><form className="card ppap-card" onSubmit={(event) => { event.preventDefault(); void perform(() => recordPpapCustomerState(selectedId, customerState, customerSource, customerNote), "客户处理记录已登记"); }}>
          <h3>客户处理记录</h3><p className="muted">内部批准后依据真实提交 / 退回 / 接受记录人工登记，内部批准不代表客户接受。</p>
          {isTestTask && <p className="ppap-danger">测试任务不能登记正式客户提交、接受或退回状态。</p>}
          <div className="ppap-form-grid"><label>实际状态<select value={customerState} disabled={writeDisabled || isTestTask} onChange={(e) => setCustomerState(e.target.value)}>
            <option value="SUBMITTED">已提交客户</option><option value="REJECTED">客户退回</option><option value="ACCEPTED">客户接受</option></select></label>
            <label>实际处理记录引用 *<input required disabled={writeDisabled || isTestTask} value={customerSource} onChange={(e) => setCustomerSource(e.target.value)} /></label>
            <label className="ppap-wide">说明<textarea value={customerNote} disabled={writeDisabled || isTestTask} onChange={(e) => setCustomerNote(e.target.value)} /></label></div>
          <button className="button" disabled={writeDisabled || isTestTask || !["APPROVED", "ARCHIVED"].includes(detail.task.package_state)}>登记客户处理状态</button>
        </form></details>
      </>}
      <div className="ppap-stage-footer"><button type="button" className="button secondary" aria-expanded={showHistory} onClick={() => setShowHistory(value => !value)}>来源与操作记录</button><button type="button" className="button secondary" disabled={busy} onClick={() => void perform(async () => { await loadDetail(selectedId); }, "当前任务已重新读取")}>重新读取当前任务</button></div>
      {showHistory && <div className="card ppap-card"><h3>编制运行与来源追溯</h3><p className="muted">运行完成不代表审核批准。{isTestTask ? "当前仅显示本地测试任务记录，不代表实际业务或客户状态。" : "当前显示正式任务中的运行及取证调用。"}</p>
        <details><summary>取证覆盖与任务留痕</summary><pre>{displaySystemText(jsonText(detail.task.coverage))}</pre></details>
        {!runs.length && <p>尚无编制运行记录。</p>}{runs.map((run) => <details key={run.run_id} className="ppap-record"><summary>{run.run_id} · {stateLabel(run.status)} · {run.started_at}</summary>
          <p>请求人：{run.requested_by}；{run.idempotent_replay ? "幂等复用" : "编制运行"}</p><small className="ppap-hash">快照：{run.input_snapshot_hash}</small>
          <h4>有序执行步骤</h4><pre>{displaySystemText(jsonText(run.steps || []))}</pre><h4>实际调用链</h4><pre>{displaySystemText(jsonText(run.call_chain || []))}</pre>
          <h4>运行摘要</h4><pre>{displaySystemText(jsonText(run.summary))}</pre>{run.error && <pre className="ppap-danger">{displaySystemText(jsonText(run.error))}</pre>}
        </details>)}
      </div>}
    </>}
  </section>;
}
