// @vitest-environment jsdom
// All records in this file are isolated UI test fixtures, never ERP/MES evidence.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PpapDetail, PpapDatasetContract, PpapTemplate } from "../../types/ppap";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const stubs = vi.hoisted(() => ({ list: vi.fn(), template: vi.fn(), detail: vi.fn(), inputs: vi.fn(), plan: vi.fn(), runs: vi.fn(), impact: vi.fn(), missing: vi.fn(),
  assistance: vi.fn(),
  create: vi.fn(), update: vi.fn(), applicability: vi.fn(), collect: vi.fn(), evidence: vi.fn(), input: vi.fn(), compile: vi.fn(),
  checks: vi.fn(), resolve: vi.fn(), submit: vi.fn(), decide: vi.fn(), archive: vi.fn(), customer: vi.fn(), download: vi.fn() }));
vi.mock("../../api", () => ({ getPpapTasks: stubs.list, getPpapTemplate: stubs.template, getPpapTask: stubs.detail,
  getPpapInputs: stubs.inputs, getPpapEvidencePlan: stubs.plan, getPpapRuns: stubs.runs, getPpapChangeImpact: stubs.impact, getPpapMissingItems: stubs.missing, getPpapInputAssistance: stubs.assistance, createPpapTask: stubs.create,
  updatePpapTask: stubs.update, confirmPpapApplicability: stubs.applicability, collectPpapEvidence: stubs.collect,
  registerPpapEvidence: stubs.evidence, savePpapInput: stubs.input, compilePpapTask: stubs.compile, checkPpapTask: stubs.checks,
  resolvePpapIssue: stubs.resolve, submitPpapReview: stubs.submit, decidePpapReview: stubs.decide, archivePpapTask: stubs.archive,
  recordPpapCustomerState: stubs.customer, downloadPpapArtifact: stubs.download }));
import PpapPanel from "../PpapPanel";

const descriptor = { template_id: "internal-default-ppap-v1", template_version: "1.0.0", template_source: "project_defined",
  display_name: "项目自定义模板", notice: "项目自定义模板，非客户模板", is_customer_template: false,
  elements: [], datasets: ["inspection_value", "process_study"], side_sensitive_datasets: ["inspection_value"], evidence_source_types: [] } as PpapTemplate;
const schema: Record<string, PpapDatasetContract> = {
  inspection_value: { label: "检验原始值", required: ["characteristic_id", "point"], optional: ["value", "unit"], numeric: ["value"], boolean: [] },
  process_study: { label: "过程研究", required: ["study_id", "characteristic_id"], optional: ["values", "unit"], numeric: [], boolean: [] },
};
function taskDetail(): PpapDetail {
  return { ok: true, task: { data_mode: "REAL", task_id: "UI-TEST-PPAP", customer_id: "UI-TEST-C", customer_name: "界面测试客户", part_no: "UI-TEST-L",
    part_name: "界面测试左件", part_side: "LEFT", drawing_version: "A", engineering_change_level: "A", submission_reason: "界面测试",
    submission_level: "", due_date: "", owner: "test-quality", run_state: "WAITING_DATA", package_state: "NOT_SUBMITTED", customer_state: "NOT_SUBMITTED",
    template_id: descriptor.template_id, template_version: "1.0.0", template_source: "project_defined", business_object: {}, coverage: {}, input_snapshot_hash: "test-snapshot", state_note: "" },
    elements: [{ element_id: "design_records", title: "设计记录", requirement: "", applicability_basis: "", suggested_requirement: "retain",
      applicability_condition: "按客户要求", status: "APPLICABILITY_UNCONFIRMED", status_reason: "待人工确认", compilation_method: "reference_document",
      required_inputs: [], evidence_types: ["user_upload"], responsible_role: "quality_manager", check_result: {}, notes: "" }],
    inputs: [], evidence: [
      { evidence_id: "UI-TEST-E1", source_type: "openmes_inspection_record", source_id: "UI-TEST-1", locator: "", summary: "合法空记录", read_status: "EMPTY", review_state: "UNVERIFIED", truncated: false, content_hash: "", collected_by: "test-quality", collected_at: "" },
      { evidence_id: "UI-TEST-E2", source_type: "erp_sales_order", source_id: "UI-TEST-2", locator: "", summary: "读取失败", read_status: "ERROR", review_state: "UNVERIFIED", truncated: true, content_hash: "", collected_by: "test-quality", collected_at: "", error: { code: "timeout" } },
    ], issues: [], artifacts: [], reviews: [], runs: [], template: descriptor, applicability: { total: 1, confirmed: 0, unconfirmed_element_ids: ["design_records"] } };
}
let detail: PpapDetail;
const cleanups: Array<() => void> = [];
beforeEach(() => {
  detail = taskDetail();
  stubs.list.mockImplementation(async () => ({ items: [detail.task], total: 1, limit: 20, offset: 0 }));
  stubs.template.mockResolvedValue(descriptor); stubs.detail.mockImplementation(async () => detail);
  stubs.inputs.mockResolvedValue({ items: [], datasets: Object.keys(schema), contracts: schema });
  stubs.plan.mockResolvedValue({ collectors: [], notice: "" }); stubs.runs.mockResolvedValue([]);
  stubs.impact.mockResolvedValue({ has_artifact: false, changed: false }); stubs.missing.mockResolvedValue({ findings: [], readiness: [] });
  stubs.assistance.mockImplementation(async (_taskId, dataset, part_side) => ({ ok: true, read_only: true,
    task_id: detail.task.task_id, data_mode: detail.task.data_mode, dataset, part_side, suggestions: [], candidates: [], conflicts: [], notices: [] }));
  for (const mutation of [stubs.create, stubs.update, stubs.applicability, stubs.collect, stubs.evidence, stubs.input, stubs.compile,
    stubs.checks, stubs.resolve, stubs.submit, stubs.decide, stubs.archive, stubs.customer, stubs.download]) mutation.mockResolvedValue({ success: true });
});
afterEach(() => { cleanups.splice(0).reverse().forEach((cleanup) => cleanup()); vi.resetAllMocks(); });
async function mount(logged = true, roles = ["quality_manager"]) {
  const container = document.createElement("div"); document.body.appendChild(container);
  const root: Root = createRoot(container); const login = vi.fn();
  await act(async () => { root.render(<PpapPanel hasProjectSession={logged} actorId="test-quality" roles={roles} onLogin={login} />); });
  cleanups.push(() => { act(() => root.unmount()); container.remove(); });
  return { container, login };
}
const button = (container: HTMLElement, text: string) => Array.from(container.querySelectorAll("button")).find((candidate) => candidate.getAttribute("aria-label") === text || candidate.textContent === text)!;
async function click(container: HTMLElement, text: string) { await act(async () => { button(container, text).click(); }); }
function field(container: HTMLElement, label: string) {
  return Array.from(container.querySelectorAll("label")).find((node) => node.firstChild?.textContent?.trim().startsWith(label))!.querySelector("input,textarea,select") as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
}
async function change(node: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  await act(async () => {
    const prototype = node instanceof window.HTMLSelectElement ? window.HTMLSelectElement.prototype : node instanceof window.HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(node, value);
    node.dispatchEvent(new window.Event(node instanceof window.HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}
async function openTask(container: HTMLElement, step = "准备资料", preparation = "录入资料") {
  await act(async () => { container.querySelector<HTMLButtonElement>(".ppap-task-item")!.click(); });
  await click(container, step);
  if (step === "准备资料") await click(container, preparation);
}

describe("PPAP 四阶段工作台", () => {
  it("未取得契约的资料目标明确报错，不猜表单或自动保存", async () => {
    stubs.missing.mockResolvedValue({ findings: [{ title: "UI-UNKNOWN-TARGET", severity: "BLOCKER", blocks_approval: true, navigation: { kind: "input", dataset: "unknown_contract" } }] });
    const { container } = await mount(); await openTask(container, "编制与检查"); await click(container, "去补充");
    expect(container.querySelector('[role="alert"]')!.textContent).toContain("目标资料类型尚未取得");
    expect(button(container, "编制与检查").getAttribute("aria-pressed")).toBe("true"); expect(stubs.input).not.toHaveBeenCalled();
  });
  it("切阶段保留非法原始样本草稿，修正前不能切数据集或保存", async () => {
    detail.inputs.push({ input_id: "UI-STUDY", dataset: "process_study", record_key: "UI-STUDY-K", part_side: "LEFT", element_id: "", payload: { study_id: "UI-STUDY", values: [1, 2] }, source_type: "user_confirmation", source_ref: "UI-SOURCE", confirmed_by: "test-quality", confirmed_at: "" });
    const { container } = await mount(); await openTask(container);
    await change(container.querySelector<HTMLSelectElement>('[aria-label="数据集"]')!, "process_study"); await click(container, "载入修订");
    const sample = container.querySelector<HTMLInputElement>(".ppap-study-table input")!;
    await change(sample, "非法样本"); expect(sample.getAttribute("aria-invalid")).toBe("true");
    await click(container, "编制与检查"); await click(container, "准备资料");
    expect(container.querySelector<HTMLInputElement>(".ppap-study-table input")!.value).toBe("非法样本");
    await change(container.querySelector<HTMLSelectElement>('[aria-label="数据集"]')!, "inspection_value");
    expect(container.querySelector<HTMLSelectElement>('[aria-label="数据集"]')!.value).toBe("process_study");
    expect(container.textContent).toContain("请先修正当前表格中的非法原始值"); expect(stubs.input).not.toHaveBeenCalled();
    await click(container, "清除当前录入草稿");
    expect(container.querySelector<HTMLInputElement>(".ppap-study-table input")!.value).toBe("1");
    expect(container.querySelector<HTMLInputElement>(".ppap-study-table input")!.getAttribute("aria-invalid")).toBe("false");
    expect(container.querySelector<HTMLButtonElement>('.ppap-summary button')!.disabled).toBe(false);
  });
  it("确认要求与录入表单分阶段可见，浏览不会把要求置为已确认", async () => {
    const { container } = await mount(); await openTask(container, "确认要求");
    const requirements = container.querySelector<HTMLElement>('[aria-label="确认要求工作区"]')!;
    const preparation = container.querySelector<HTMLElement>('[aria-label="准备资料工作区"]')!;
    expect(requirements.hidden).toBe(false); expect(preparation.hidden).toBe(true);
    await click(container, "准备资料"); expect(requirements.hidden).toBe(true); expect(preparation.hidden).toBe(false);
    expect(detail.applicability.confirmed).toBe(0); expect(stubs.applicability).not.toHaveBeenCalled();
  });
  it.each([
    { kind: "task", stage: "requirements" }, { kind: "applicability", element_id: "design_records", stage: "requirements" },
    { kind: "evidence", element_id: "design_records", stage: "evidence" }, { kind: "input", dataset: "process_study", stage: "inputs" },
  ])("缺项$kind只导航到指定阶段与子区，不自动写入", async target => {
    stubs.missing.mockResolvedValue({ findings: [{ title: "UI-WORKFLOW-FINDING", severity: "BLOCKER", blocks_approval: true, element_id: "design_records", navigation: target }] });
    const { container } = await mount(); await openTask(container, "编制与检查"); await click(container, "去补充");
    if (target.stage === "requirements") expect(container.querySelector<HTMLElement>('[aria-label="确认要求工作区"]')!.hidden).toBe(false);
    else {
      expect(container.querySelector<HTMLElement>('[aria-label="准备资料工作区"]')!.hidden).toBe(false);
      const name = target.stage === "evidence" ? "来源资料" : "录入资料";
      expect(button(container, name).getAttribute("aria-pressed")).toBe("true");
      if (target.stage === "inputs") expect(container.querySelector<HTMLSelectElement>('[aria-label="数据集"]')!.value).toBe("process_study");
    }
    expect(stubs.input).not.toHaveBeenCalled(); expect(stubs.applicability).not.toHaveBeenCalled(); expect(stubs.evidence).not.toHaveBeenCalled();
  });
  it("资料分类往返保留本任务草稿，未确认保存前不写入", async () => {
    schema.product_base = { label: "产品基础信息", required: ["part_no"], optional: [], numeric: [], boolean: [] };
    const { container } = await mount(); await openTask(container);
    await change(field(container, "来源引用"), "UI-UNSAVED-SOURCE"); await change(field(container, "特性编号"), "UI-UNSAVED-CHAR");
    await click(container, "基础与客户要求"); await click(container, "检验与研究");
    expect(field(container, "来源引用").value).toBe("UI-UNSAVED-SOURCE"); expect(field(container, "特性编号").value).toBe("UI-UNSAVED-CHAR");
    expect(stubs.input).not.toHaveBeenCalled(); delete schema.product_base;
  });
  it("来源与操作记录在任意阶段可打开，编制/审核仍分别标示", async () => {
    const { container } = await mount(); await openTask(container);
    for (const stage of ["确认要求", "准备资料", "编制与检查", "审核与归档"]) {
      await click(container, stage); await click(container, "来源与操作记录"); expect(container.textContent).toContain("编制运行与来源追溯");
      await click(container, "来源与操作记录");
    }
    expect(stubs.compile).not.toHaveBeenCalled(); expect(stubs.decide).not.toHaveBeenCalled();
  });
  it("资料变化的草稿显示不可审核原因，并禁止提交", async () => {
    detail.applicability = { total: 1, confirmed: 1, unconfirmed_element_ids: [] };
    detail.artifacts.push({ artifact_id: "UI-TEST-A", scope: "PACKAGE", element_id: "", version: 1, kind: "xlsx", file_name: "UI-TEST.xlsx", status: "DRAFT", content_hash: "UI-H", input_snapshot_hash: "UI-S", template_version: "1", byte_size: 1, frozen_at: "", created_by: "test-quality", created_at: "" });
    stubs.impact.mockResolvedValue({ ok: true, has_artifact: true, comparison_artifact_id: "UI-TEST-A", changed: true });
    const { container } = await mount(); await openTask(container, "审核与归档");
    expect(container.textContent).toContain("资料已变化，需重新编制"); expect(button(container, "提交 V1 审核").disabled).toBe(true);
    await click(container, "提交 V1 审核"); expect(stubs.submit).not.toHaveBeenCalled();
  });
});
