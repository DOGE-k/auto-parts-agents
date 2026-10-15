// @vitest-environment jsdom
// Isolated UI fixtures: no ERP/MES readers or real business records are used.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PpapDatasetContract, PpapDetail, PpapInput, PpapTemplate } from "../../types/ppap";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const stubs = vi.hoisted(() => ({
  list: vi.fn(), template: vi.fn(), detail: vi.fn(), inputs: vi.fn(), plan: vi.fn(), runs: vi.fn(), impact: vi.fn(), missing: vi.fn(), assistance: vi.fn(),
  create: vi.fn(), update: vi.fn(), applicability: vi.fn(), collect: vi.fn(), evidence: vi.fn(), input: vi.fn(), compile: vi.fn(), checks: vi.fn(),
  resolve: vi.fn(), submit: vi.fn(), decide: vi.fn(), archive: vi.fn(), customer: vi.fn(), download: vi.fn(),
}));
vi.mock("../../api", () => ({
  getPpapTasks: stubs.list, getPpapTemplate: stubs.template, getPpapTask: stubs.detail, getPpapInputs: stubs.inputs,
  getPpapEvidencePlan: stubs.plan, getPpapRuns: stubs.runs, getPpapChangeImpact: stubs.impact, getPpapMissingItems: stubs.missing,
  getPpapInputAssistance: stubs.assistance, createPpapTask: stubs.create, updatePpapTask: stubs.update,
  confirmPpapApplicability: stubs.applicability, collectPpapEvidence: stubs.collect, registerPpapEvidence: stubs.evidence,
  savePpapInput: stubs.input, compilePpapTask: stubs.compile, checkPpapTask: stubs.checks, resolvePpapIssue: stubs.resolve,
  submitPpapReview: stubs.submit, decidePpapReview: stubs.decide, archivePpapTask: stubs.archive,
  recordPpapCustomerState: stubs.customer, downloadPpapArtifact: stubs.download,
}));
import PpapPanel from "../PpapPanel";

const schema: Record<string, PpapDatasetContract> = {
  inspection_value: { label: "检验原始值", required: ["characteristic_id", "point"], optional: ["value", "unit"], numeric: ["value"], boolean: [] },
};
const descriptor: PpapTemplate = {
  template_id: "internal-default-ppap-v1", template_version: "1.0.0", template_source: "project_defined", display_name: "项目自定义模板",
  notice: "项目自定义模板，非客户模板", is_customer_template: false, elements: [], datasets: ["inspection_value"],
  side_sensitive_datasets: ["inspection_value"], evidence_source_types: [],
};
function taskDetail(id = "UI-FEEDBACK-TASK", part = "UI-FEEDBACK-L"): PpapDetail {
  return {
    ok: true, task: { data_mode: "TEST", task_id: id, customer_id: "UI-FEEDBACK-C", customer_name: "反馈测试客户", part_no: part,
      part_name: "反馈测试零件", part_side: "LEFT", drawing_version: "A", engineering_change_level: "A", submission_reason: "本地界面反馈测试",
      submission_level: "", due_date: "", owner: "test-quality", run_state: "WAITING_DATA", package_state: "NOT_SUBMITTED", customer_state: "NOT_SUBMITTED",
      template_id: descriptor.template_id, template_version: descriptor.template_version, template_source: "project_defined", business_object: {}, coverage: {},
      input_snapshot_hash: "ui-snapshot", state_note: "" },
    elements: [], inputs: [], evidence: [], issues: [], artifacts: [], reviews: [], runs: [], template: descriptor,
    applicability: { total: 0, confirmed: 0, unconfirmed_element_ids: [] },
  };
}
function savedInput(): PpapInput {
  return { input_id: "UI-FEEDBACK-I", dataset: "inspection_value", record_key: "CHAR-1|POINT-1|LEFT", part_side: "LEFT", element_id: "dimensional_results",
    payload: { characteristic_id: "CHAR-1", point: "POINT-1", value: 10.25, unit: "mm" }, source_type: "user_confirmation",
    source_ref: "UI-MEASUREMENT-SOURCE", confirmed_by: "test-quality", confirmed_at: "2026-10-14T08:30:00+00:00" };
}
// Mirrors upsert_input's public response: provenance/time come from subsequent GET, not invented POST fields.
function savedReceipt(input = savedInput()) {
  return { success: true, input_id: input.input_id, created: true, dataset: input.dataset, record_key: input.record_key,
    part_side: input.part_side, payload: input.payload, confirmed_by: input.confirmed_by, written: "local_ppap_store", read_back_verified: true };
}
function deferred<T>() {
  let resolve!: (result: T) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
let detail: PpapDetail;
const cleanups: Array<() => void> = [];
beforeEach(() => {
  detail = taskDetail();
  stubs.list.mockImplementation(async () => ({ items: [detail.task], total: 1, limit: 20, offset: 0 }));
  stubs.template.mockResolvedValue(descriptor); stubs.detail.mockImplementation(async () => detail);
  stubs.inputs.mockImplementation(async () => ({ items: detail.inputs, datasets: Object.keys(schema), contracts: schema }));
  stubs.plan.mockResolvedValue({ collectors: [], notice: "" }); stubs.runs.mockResolvedValue([]);
  stubs.impact.mockResolvedValue({ has_artifact: false, changed: false }); stubs.missing.mockResolvedValue({ findings: [], readiness: [] });
  stubs.assistance.mockImplementation(async (id, dataset, part_side) => ({ ok: true, read_only: true, task_id: id, data_mode: "TEST",
    dataset, part_side, suggestions: [], candidates: [], conflicts: [], notices: [] }));
});
afterEach(() => { cleanups.splice(0).reverse().forEach((cleanup) => cleanup()); vi.resetAllMocks(); });
async function mount() {
  const container = document.createElement("div"); document.body.appendChild(container);
  const root: Root = createRoot(container);
  await act(async () => { root.render(<PpapPanel hasProjectSession actorId="test-quality" roles={["quality_manager"]} onLogin={vi.fn()} />); });
  cleanups.push(() => { act(() => root.unmount()); container.remove(); });
  await act(async () => { container.querySelector<HTMLButtonElement>(".ppap-task-item")!.click(); });
  await act(async () => { container.querySelector<HTMLButtonElement>('button[aria-label="准备资料"]')!.click(); });
  await act(async () => { Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === "录入资料")!.click(); });
  return container;
}
function saveButton(container: HTMLElement) {
  return Array.from(container.querySelectorAll("button")).find((node) => /确认并保存输入|正在保存/.test(node.textContent || ""))!;
}
function inputArea(container: HTMLElement) { return saveButton(container).closest<HTMLElement>(".ppap-card")!; }
function statusText(container: HTMLElement) {
  return Array.from(inputArea(container).querySelectorAll('[role="status"]')).map((node) => node.textContent).join(" ");
}
function control(container: HTMLElement, label: string) {
  return Array.from(inputArea(container).querySelectorAll("label")).find((node) => node.firstChild?.textContent?.trim().startsWith(label))!
    .querySelector<HTMLInputElement>("input")!;
}
async function change(node: HTMLInputElement, value: string) {
  await act(async () => { Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!.call(node, value);
    node.dispatchEvent(new window.Event("input", { bubbles: true })); });
}
async function completeForm(container: HTMLElement) {
  await change(control(container, "来源引用"), "UI-MEASUREMENT-SOURCE"); await change(control(container, "特性编号"), "CHAR-1");
  await change(control(container, "测点编号"), "POINT-1"); await change(control(container, "实测值"), "10.25");
  await change(control(container, "单位"), "mm");
}
async function submit(container: HTMLElement) {
  await act(async () => { saveButton(container).closest("form")!.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
}
async function resolveSave(input = savedInput()) {
  detail = { ...detail, inputs: [input] };
  return savedReceipt(input);
}

describe("PPAP 输入确认的就近保存反馈", () => {
  it("待回读时切阶段不丢失上下文，锁住任务与分类并返回GET核对", async () => {
    stubs.input.mockImplementation(async () => {
      detail = { ...detail, inputs: [savedInput()] };
      throw Object.assign(new Error("已提交，回读暂不可用"), { status: 422, code: "read_back_failed" });
    });
    const container = await mount(); await completeForm(container); await submit(container);
    expect(container.querySelector<HTMLButtonElement>(".ppap-task-item")!.disabled).toBe(true);
    expect(container.querySelector<HTMLButtonElement>('[aria-label="检验与研究"]')!.disabled).toBe(true);
    await act(async () => { container.querySelector<HTMLButtonElement>('button[aria-label="编制与检查"]')!.click(); });
    expect(container.querySelector<HTMLElement>('[aria-label="准备资料工作区"]')!.hidden).toBe(true);
    const restore = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(node => node.textContent === "返回核对已保存记录")!;
    await act(async () => { restore.click(); });
    expect(container.querySelector<HTMLElement>('[aria-label="准备资料工作区"]')!.hidden).toBe(false);
    const retry = Array.from(inputArea(container).querySelectorAll<HTMLButtonElement>("button")).find(node => node.textContent === "重新读取已保存记录")!;
    await act(async () => { retry.click(); });
    expect(statusText(container)).toContain("保存成功"); expect(stubs.input).toHaveBeenCalledTimes(1);
  });
  it("提交期间显示进度并禁止重复提交；连续submit事件只发一次POST", async () => {
    const pending = deferred<ReturnType<typeof savedReceipt>>(); stubs.input.mockReturnValue(pending.promise);
    const container = await mount(); await completeForm(container); await submit(container);
    expect(saveButton(container).disabled).toBe(true);
    expect(saveButton(container).textContent).toMatch(/正在保存/);
    expect(statusText(container)).toMatch(/正在保存|保存中/);
    await submit(container); expect(stubs.input).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(await resolveSave()); });
    expect(statusText(container)).toMatch(/已保存|保存成功/);
  });

  it("成功后原位置显示回读结果、已确认条数，并展开和突出显示刚保存记录", async () => {
    stubs.input.mockImplementation(async () => resolveSave());
    const container = await mount(); await completeForm(container); await submit(container);
    expect(statusText(container)).toMatch(/已保存|保存成功/);
    const area = inputArea(container);
    const summary = Array.from(area.querySelectorAll("summary")).find((node) => node.textContent === "已确认输入（1 条）")!;
    expect(summary).toBeTruthy(); expect(summary.closest<HTMLDetailsElement>("details")!.open).toBe(true);
    const record = Array.from(area.querySelectorAll<HTMLElement>(".ppap-record")).find((node) => node.textContent?.includes("CHAR-1|POINT-1|LEFT"))!;
    expect(record.textContent).toContain("UI-MEASUREMENT-SOURCE"); expect(record.textContent).toContain("test-quality");
    expect(record.matches(".ppap-record-saved, [data-saved='true']")).toBe(true);
    expect(stubs.input).toHaveBeenCalledTimes(1);
  });

  it("POST成功而详情GET失败时明确已保存但回读失败，恢复按钮只重新GET", async () => {
    stubs.input.mockImplementation(async () => { detail = { ...detail, inputs: [savedInput()] };
      stubs.detail.mockRejectedValueOnce(new Error("详情读取超时")); return savedReceipt(); });
    const container = await mount(); await completeForm(container); await submit(container);
    const feedback = statusText(container);
    expect(feedback).toMatch(/已保存|保存成功|保存请求已成功/); expect(feedback).toMatch(/回读|重新读取/); expect(feedback).toContain("详情读取超时");
    expect(feedback).not.toMatch(/^保存失败/);
    const retry = Array.from(inputArea(container).querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === "重新读取已保存记录")!;
    expect(retry).toBeTruthy(); const readsBefore = stubs.detail.mock.calls.length;
    await act(async () => { retry.click(); });
    expect(stubs.detail.mock.calls.length).toBeGreaterThan(readsBefore); expect(stubs.input).toHaveBeenCalledTimes(1);
    expect(inputArea(container).textContent).toContain("已确认输入（1 条）");
    expect(statusText(container)).toMatch(/已保存|保存成功/);
  });

  it("POST被拒绝时在输入区域显示后端原因并保留来源及所有已填原始值", async () => {
    stubs.input.mockRejectedValue(new Error("同侧特性规格冲突，请修订原记录"));
    const container = await mount(); await completeForm(container); await submit(container);
    expect(inputArea(container).textContent).toContain("同侧特性规格冲突，请修订原记录");
    expect(inputArea(container).textContent).toMatch(/保存失败|未保存/);
    expect(control(container, "来源引用").value).toBe("UI-MEASUREMENT-SOURCE");
    expect(control(container, "特性编号").value).toBe("CHAR-1"); expect(control(container, "实测值").value).toBe("10.25");
    expect(saveButton(container).disabled).toBe(false); expect(stubs.input).toHaveBeenCalledTimes(1);
    expect(inputArea(container).textContent).toContain("已确认输入（0 条）");
  });

  it("原生必填校验拒绝提交时给出就近原因，不静默也不发送POST", async () => {
    const container = await mount(); await change(control(container, "特性编号"), "CHAR-1");
    await act(async () => { saveButton(container).click(); });
    expect(stubs.input).not.toHaveBeenCalled();
    expect(inputArea(container).textContent).toMatch(/来源引用.*(填写|必填|不能为空)|请(填写|补充或修正).*来源引用/);
    expect(control(container, "特性编号").value).toBe("CHAR-1");
  });

  it("成功后编辑字段立即提示未保存，已有保存记录仍显示原值", async () => {
    stubs.input.mockImplementation(async () => resolveSave());
    const container = await mount(); await completeForm(container); await submit(container);
    await change(control(container, "实测值"), "11.5");
    expect(statusText(container)).toMatch(/未保存|待保存/);
    expect(control(container, "实测值").value).toBe("11.5");
    const record = inputArea(container).querySelector<HTMLElement>(".ppap-record")!;
    expect(record.textContent).toContain("10.25"); expect(stubs.input).toHaveBeenCalledTimes(1);
  });

  it("保存中禁止切换任务，避免把旧请求回读附到另一任务", async () => {
    const another = taskDetail("UI-FEEDBACK-SECOND", "UI-FEEDBACK-R");
    stubs.list.mockImplementation(async () => ({ items: [detail.task, another.task], total: 2, limit: 20, offset: 0 }));
    const pending = deferred<ReturnType<typeof savedReceipt>>(); stubs.input.mockReturnValue(pending.promise);
    const container = await mount(); await completeForm(container); await submit(container);
    const secondButton = Array.from(container.querySelectorAll<HTMLButtonElement>(".ppap-task-item"))[1];
    expect(secondButton.disabled).toBe(true); await act(async () => { secondButton.click(); });
    expect(stubs.detail).not.toHaveBeenCalledWith("UI-FEEDBACK-SECOND");
    await act(async () => { pending.resolve(await resolveSave()); });
    expect(container.querySelector<HTMLButtonElement>(".ppap-task-item.selected")!.textContent).toContain("UI-FEEDBACK-L");
    expect(statusText(container)).toMatch(/已保存|保存成功/);
  });

  it("503响应标记提交结果未确认，先GET核对真实已落库记录而不重发POST", async () => {
    stubs.input.mockImplementation(async () => {
      // A server can commit before its response fails; the UI must not assume an absent write.
      detail = { ...detail, inputs: [savedInput()] };
      throw Object.assign(new Error("写入服务暂时不可用"), { status: 503, code: "service_unavailable" });
    });
    const container = await mount(); await completeForm(container); await submit(container);
    expect(inputArea(container).textContent).toMatch(/结果.*未确认|尚未确认/);
    expect(inputArea(container).textContent).toContain("写入服务暂时不可用");
    expect(control(container, "实测值").value).toBe("10.25");
    expect(saveButton(container).disabled).toBe(true); await submit(container);
    expect(stubs.input).toHaveBeenCalledTimes(1);
    const retry = Array.from(inputArea(container).querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === "重新读取已保存记录")!;
    expect(retry).toBeTruthy(); const readsBefore = stubs.detail.mock.calls.length;
    await act(async () => { retry.click(); });
    expect(stubs.detail.mock.calls.length).toBeGreaterThan(readsBefore); expect(stubs.input).toHaveBeenCalledTimes(1);
    expect(inputArea(container).textContent).toContain("已确认输入（1 条）");
    expect(statusText(container)).toMatch(/已保存|保存成功/);
  });

  it("后端read_back_failed明确已提交但回读未完成，保留输入且只通过GET恢复", async () => {
    stubs.input.mockImplementation(async () => {
      detail = { ...detail, inputs: [savedInput()] };
      throw Object.assign(new Error("本次写入已提交，但独立回读不一致，请按记录编号核对"), { status: 422, code: "read_back_failed" });
    });
    const container = await mount(); await completeForm(container); await submit(container);
    expect(inputArea(container).textContent).toMatch(/已提交|已保存|请求已成功/);
    expect(inputArea(container).textContent).toMatch(/回读.*未完成|回读不一致/);
    expect(inputArea(container).textContent).not.toContain("保存失败：");
    expect(saveButton(container).disabled).toBe(true);
    const retry = Array.from(inputArea(container).querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === "重新读取已保存记录")!;
    expect(retry).toBeTruthy(); await act(async () => { retry.click(); });
    expect(stubs.input).toHaveBeenCalledTimes(1); expect(statusText(container)).toMatch(/已保存|保存成功/);
  });

  it("修订已提交但回读失败时清除旧成功高亮，GET恢复后才标记新内容", async () => {
    stubs.input.mockImplementationOnce(async () => resolveSave());
    const container = await mount(); await completeForm(container); await submit(container);
    const area = inputArea(container);
    expect(area.querySelector("[data-saved='true']")).toBeTruthy();
    await change(control(container, "实测值"), "11.5");
    stubs.input.mockImplementationOnce(async () => {
      const updated = { ...savedInput(), payload: { ...savedInput().payload, value: 11.5 }, confirmed_at: "2026-10-14T09:00:00+00:00" };
      detail = { ...detail, inputs: [updated] };
      stubs.detail.mockRejectedValueOnce(new Error("修订后列表读取超时"));
      return { ...savedReceipt(updated), created: false };
    });
    await submit(container);
    expect(statusText(container)).toContain("保存请求已成功，但记录回读未完成");
    expect(area.querySelector("[data-saved='true'], .ppap-record-saved")).toBeNull();
    const previous = area.querySelector<HTMLElement>(".ppap-record")!;
    expect(previous.textContent).toContain("10.25"); expect(previous.textContent).not.toContain("11.5");
    const retry = Array.from(area.querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === "重新读取已保存记录")!;
    await act(async () => { retry.click(); });
    const confirmed = area.querySelector<HTMLElement>("[data-saved='true'].ppap-record-saved")!;
    expect(confirmed.textContent).toContain("11.5"); expect(confirmed.textContent).not.toContain("10.25");
    expect(statusText(container)).toContain("保存成功，已回读确认");
    expect(stubs.input).toHaveBeenCalledTimes(2); expect(area.textContent).toContain("已确认输入（1 条）");
  });

  it.each([
    { field: "source_ref", value: "OTHER-MEASUREMENT-SOURCE" },
    { field: "source_type", value: "synthetic_demo_only" },
    { field: "confirmed_by", value: "another-quality-user" },
  ] as const)("同ID同数值但回读$field与本次提交不一致时不得宣称保存已确认", async ({ field, value }) => {
    stubs.input.mockImplementation(async () => {
      detail = { ...detail, inputs: [{ ...savedInput(), [field]: value }] };
      return savedReceipt();
    });
    const container = await mount(); await completeForm(container); await submit(container);
    expect(statusText(container)).toContain("保存请求已成功，但记录回读未完成");
    expect(statusText(container)).not.toContain("保存成功，已回读确认");
    expect(inputArea(container).querySelector("[data-saved='true'], .ppap-record-saved")).toBeNull();
    expect(saveButton(container).disabled).toBe(true); expect(stubs.input).toHaveBeenCalledTimes(1);
    expect(control(container, "来源引用").value).toBe("UI-MEASUREMENT-SOURCE");
    detail = { ...detail, inputs: [savedInput()] };
    const retry = Array.from(inputArea(container).querySelectorAll<HTMLButtonElement>("button")).find((node) => node.textContent === "重新读取已保存记录")!;
    await act(async () => { retry.click(); });
    expect(statusText(container)).toContain("保存成功，已回读确认"); expect(stubs.input).toHaveBeenCalledTimes(1);
  });

  it("保存后的旧范围列表延迟返回不能覆盖用户新选择的任务范围", async () => {
    stubs.input.mockImplementation(async () => resolveSave());
    const container = await mount();
    const mode = container.querySelector<HTMLSelectElement>('[aria-label="任务模式筛选"]')!;
    const changeMode = async (value: string) => {
      await act(async () => { Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(mode, value);
        mode.dispatchEvent(new window.Event("change", { bubbles: true })); });
    };
    await changeMode("TEST"); await completeForm(container);
    const oldList = deferred<{ items: PpapDetail["task"][]; total: number; limit: number; offset: number }>();
    const formal = taskDetail("UI-FEEDBACK-FORMAL", "UI-FEEDBACK-FORMAL-PART"); formal.task.data_mode = "REAL";
    stubs.list.mockImplementation(async (_offset, _query, requestedMode) => requestedMode === "TEST"
      ? oldList.promise : { items: [formal.task], total: 1, limit: 20, offset: 0 });
    await submit(container);
    expect(statusText(container)).toContain("保存成功，已回读确认"); expect(mode.disabled).toBe(false);
    await changeMode("REAL");
    const list = container.querySelector<HTMLElement>(".ppap-task-list")!;
    expect(list.textContent).toContain("UI-FEEDBACK-FORMAL-PART"); expect(list.textContent).not.toContain("UI-FEEDBACK-L");
    await act(async () => { oldList.resolve({ items: [detail.task], total: 1, limit: 20, offset: 0 }); });
    expect(mode.value).toBe("REAL"); expect(list.textContent).toContain("UI-FEEDBACK-FORMAL-PART");
    expect(list.textContent).not.toContain("UI-FEEDBACK-L"); expect(stubs.input).toHaveBeenCalledTimes(1);
  });
});
