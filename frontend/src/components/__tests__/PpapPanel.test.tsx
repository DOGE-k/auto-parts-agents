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

describe("PPAP 工作台权限、取证和业务流程", () => {
  it("自动预填同侧唯一特性但不生成实测值、不保存，也不冒用规格来源作检测来源", async () => {
    stubs.assistance.mockImplementation(async (_id, dataset, part_side) => ({ ok: true, read_only: true,
      task_id: detail.task.task_id, data_mode: "REAL", dataset, part_side, candidates: [], conflicts: [], notices: [],
      suggestions: [{field: "characteristic_id", value: "C-1", origin: "confirmed_input", source_ref: "SPEC-1", source_type: "user_confirmation", input_id: "CHAR-1", record_key: "CHAR-K"},
        {field: "unit", value: "mm", origin: "confirmed_input", source_ref: "SPEC-1", source_type: "user_confirmation", input_id: "CHAR-1", record_key: "CHAR-K"}] }));
    const {container} = await mount(); await openTask(container);
    expect(field(container, "特性编号").value).toBe("C-1"); expect(field(container, "单位").value).toBe("mm");
    expect(field(container, "实测值").value).toBe(""); expect(field(container, "来源引用").value).toBe("");
    expect(container.textContent).toContain("来源：SPEC-1"); expect(stubs.input).not.toHaveBeenCalled();
    await change(field(container, "单位"), "cm"); await change(field(container, "特性编号"), "C-2");
    expect(field(container, "单位").value).toBe("cm");
    await click(container, "新增一条记录");
    expect(field(container, "单位").value).toBe("mm");
    expect(field(container, "特性编号").value).toBe("C-1");
    await act(async () => { container.querySelector<HTMLButtonElement>('[aria-label="单位确认无值"]')!.click(); });
    await change(field(container, "特性编号"), "C-3");
    expect(container.querySelector('[aria-label="单位确认无值"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(stubs.input).not.toHaveBeenCalled();
  });
  it("缺项定位载入原记录并保留测试来源、侧别和明确null，用户确认前不写入", async () => {
    detail.task.data_mode = "TEST";
    detail.inputs.push({ input_id: "ORIGINAL-I", dataset: "inspection_value", record_key: "ORIGINAL-KEY", part_side: "LEFT", element_id: "dimensional_results",
      payload: {characteristic_id: "C-1", point: "POINT-1", value: null, unit: "mm"}, source_type: "synthetic_demo_only", source_ref: "明确合成测试来源", confirmed_by: "test-quality", confirmed_at: "" });
    stubs.missing.mockResolvedValue({readiness: [], findings: [{dedup_key: "F-1", check_id: "DIM", severity: "BLOCKER", blocks_approval: true,
      element_id: "dimensional_results", title: "缺实测值", detail: "需补原始值", responsible_role: "quality_manager",
      navigation: {kind: "input", dataset: "inspection_value", input_id: "ORIGINAL-I", record_key: "ORIGINAL-KEY", part_side: "LEFT", field: "value"}}]});
    const {container} = await mount(); await openTask(container); await click(container, "编制与检查");
    await click(container, "去补充");
    expect(field(container, "记录键").value).toBe("ORIGINAL-KEY");
    expect(field(container, "来源引用").value).toBe("明确合成测试来源");
    expect(container.querySelector('[aria-label="实测值确认无值"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(stubs.input).not.toHaveBeenCalled();
    await click(container, "确认并保存输入");
    expect(stubs.input.mock.calls[0][1]).toMatchObject({record_key: "ORIGINAL-KEY", part_side: "LEFT", source_type: "synthetic_demo_only", payload: {value: null}});
  });
  it("版本下拉比较选定的冻结快照，仅发只读查询", async () => {
    detail.artifacts.push({ artifact_id: "A-1", scope: "PACKAGE", element_id: "", version: 1, kind: "xlsx", file_name: "UI-test.xlsx", status: "DRAFT", content_hash: "test", input_snapshot_hash: "snapshot", template_version: "1", byte_size: 1, frozen_at: "", created_by: "test-quality", created_at: "" });
    const {container} = await mount(); await openTask(container); await click(container, "编制与检查");
    await change(container.querySelector<HTMLSelectElement>('[aria-label="PPAP 对照版本"]')!, "A-1");
    expect(stubs.impact).toHaveBeenLastCalledWith("UI-TEST-PPAP", "A-1");
    expect(stubs.compile).not.toHaveBeenCalled(); expect(stubs.input).not.toHaveBeenCalled();
  });
  it("正式为默认范围；模式筛选独立传入请求，新建测试模式不可带入任务修订", async () => {
    const { container } = await mount();
    expect(stubs.list).toHaveBeenCalledWith(0, "", "REAL");
    await change(container.querySelector<HTMLSelectElement>('[aria-label="任务模式筛选"]')!, "TEST");
    expect(stubs.list).toHaveBeenLastCalledWith(0, "", "TEST");
    await click(container, "新建 PPAP 任务");
    const mode = container.querySelector<HTMLSelectElement>('[aria-label="新建任务模式"]')!;
    expect(mode.value).toBe("REAL");
    await change(mode, "TEST");
    expect(container.textContent).toContain("模式创建后不可切换");
    const createForm = button(container, "确认启动任务").closest("form")!;
    await act(async () => { createForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
    expect(stubs.create.mock.calls[0][0].mode).toBe("TEST");
    await openTask(container, "确认要求");
    expect(container.querySelector('[aria-label="新建任务模式"]')).toBeNull();
    const updateForm = button(container, "保存已确认关联").closest("form")!;
    await act(async () => { updateForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
    expect(stubs.update.mock.calls[0][1]).not.toHaveProperty("mode");
    expect(stubs.update.mock.calls[0][1]).not.toHaveProperty("data_mode");
  });
  it("测试任务显式标识并屏蔽真实取证、业务关联及客户登记，仍可本地归档", async () => {
    detail.task.data_mode = "TEST"; detail.task.package_state = "APPROVED";
    detail.artifacts.push({ artifact_id: "UI-TEST-A", scope: "PACKAGE", element_id: "", version: 1, kind: "xlsx", file_name: "UI-TEST.xlsx", status: "APPROVED", content_hash: "test-hash", input_snapshot_hash: "test-snapshot", template_version: "1.0.0", byte_size: 1, frozen_at: "test-date", created_by: "test-quality", created_at: "" });
    const { container } = await mount(); await openTask(container, "确认要求");
    expect(container.textContent).toContain("测试任务 · 不可提交客户");
    expect(container.textContent).toContain("产物不能用于正式任务或客户提交");
    expect((field(container, "MES 工单号") as HTMLInputElement).disabled).toBe(true);
    expect((field(container, "设计责任说明") as HTMLInputElement).disabled).toBe(false);
    await click(container, "准备资料"); await click(container, "来源资料");
    expect(button(container, "按已授权计划取证").disabled).toBe(true);
    await click(container, "审核与归档");
    expect(button(container, "登记客户处理状态").disabled).toBe(true);
    expect((field(container, "实际状态") as HTMLSelectElement).disabled).toBe(true);
    expect(button(container, "归档 V1").disabled).toBe(false);
    await click(container, "归档 V1");
    expect(stubs.archive).toHaveBeenCalledWith("UI-TEST-PPAP", "UI-TEST-A", "");
    expect(stubs.customer).not.toHaveBeenCalled(); expect(stubs.collect).not.toHaveBeenCalled();
  });
  it("未登录时写操作不可用，并保留登录入口；上游连接身份不能当操作者", async () => {
    const { container, login } = await mount(false);
    expect(button(container, "新建 PPAP 任务").disabled).toBe(true);
    await click(container, "去登录"); expect(login).toHaveBeenCalledOnce();
    await openTask(container, "确认要求");
    expect(button(container, "确认当前选择").disabled).toBe(true);
    await click(container, "准备资料"); await click(container, "来源资料");
    expect(button(container, "按已授权计划取证").disabled).toBe(true);
    expect(stubs.collect).not.toHaveBeenCalled();
  });
  it("非质量角色只可查询；项目管理员角色沿用后端映射", async () => {
    const first = await mount(true, ["Sales Manager"]);
    expect(button(first.container, "新建 PPAP 任务").disabled).toBe(true);
    const second = await mount(true, ["System Manager"]);
    expect(button(second.container, "新建 PPAP 任务").disabled).toBe(false);
  });
  it("EMPTY 与 ERROR 分开展示，截断范围不能作为完整取证", async () => {
    const { container } = await mount(); await openTask(container, "准备资料", "来源资料");
    expect(container.textContent).toContain("无有效记录 EMPTY");
    expect(container.textContent).toContain("读取失败 ERROR");
    expect(container.textContent).toContain("读取范围截断，不能视为完整");
    expect(container.textContent).toContain("project_defined");
    expect(container.textContent).toContain("NEEDS_CONFIRMATION");
  });
  it("适用性不预选建议值，不适用无依据时不请求保存", async () => {
    const { container } = await mount(); await openTask(container, "确认要求");
    const select = container.querySelector<HTMLSelectElement>('[aria-label="设计记录适用性"]')!;
    expect(select.value).toBe("");
    await change(select, "not_applicable"); await click(container, "确认当前选择");
    expect(stubs.applicability).not.toHaveBeenCalled();
    expect(container.textContent).toContain("不适用的条目必须填写依据");
    await change(container.querySelector<HTMLTextAreaElement>('[aria-label="设计记录适用依据"]')!, "界面测试：客户原件条款不适用");
    await click(container, "确认当前选择");
    expect(stubs.applicability).toHaveBeenCalledWith("UI-TEST-PPAP", [{ element_id: "design_records", requirement: "not_applicable", basis: "界面测试：客户原件条款不适用" }]);
  });
  it("输入保存带真实来源引用及显式侧别，不发送 actor / reviewer 或判定字段", async () => {
    const { container } = await mount(); await openTask(container);
    await change(field(container, "来源引用"), "UI-TEST-SOURCE"); await change(field(container, "特性编号"), "UI-TEST-CHAR");
    await change(field(container, "测点编号"), "UI-TEST-POINT"); await change(field(container, "实测值"), "10.25");
    await change(field(container, "单位"), "mm");
    const form = button(container, "确认并保存输入").closest("form")!;
    await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
    expect(stubs.input).toHaveBeenCalledWith("UI-TEST-PPAP", { dataset: "inspection_value", payload: { characteristic_id: "UI-TEST-CHAR", point: "UI-TEST-POINT", value: 10.25, unit: "mm" }, part_side: "LEFT", record_key: "", source_type: "user_confirmation", source_ref: "UI-TEST-SOURCE" });
    const payload = stubs.input.mock.calls[0][1]; expect(payload).not.toHaveProperty("actor"); expect(payload).not.toHaveProperty("reviewer"); expect(payload.payload).not.toHaveProperty("conclusion");
  });
  it("原件只绑定人工勾选的条目，未选择时不默认填满目录", async () => {
    const { container } = await mount(); await openTask(container, "准备资料", "来源资料");
    await change(field(container, "来源编号"), "UI-TEST-DRAWING"); await change(field(container, "受控存储相对路径"), "UI-TEST-DRAWING.pdf");
    await change(field(container, "资料说明"), "界面测试原件");
    await change(field(container, "原件文档版本"), "UI-TEST-V1");
    const checkbox = container.querySelector<HTMLInputElement>('[aria-label="原件绑定设计记录"]')!;
    expect(checkbox.checked).toBe(false);
    const form = button(container, "确认登记证据").closest("form")!;
    await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
    expect(stubs.evidence.mock.calls[0][1].element_ids).toEqual([]);
    await act(async () => { checkbox.click(); });
    await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
    expect(stubs.evidence.mock.calls[1][1].element_ids).toEqual(["design_records"]);
    expect(stubs.evidence.mock.calls[1][1].document_version).toBe("UI-TEST-V1");
    expect(container.textContent).toContain("未绑定（不计条目资料就绪）");
  });
  it("阻断项禁提交审核，已批准版本提供下载与归档、不重新提交", async () => {
    detail.issues.push({ issue_id: "UI-TEST-I", severity: "BLOCKER", kind: "missing", check_id: "test", element_id: "design_records", field_path: "", title: "测试阻断", detail: "缺原件", basis: "test", required_evidence: "原件", responsible_role: "quality_manager", status: "OPEN", blocks_approval: true, resolution_note: "" });
    detail.artifacts.push({ artifact_id: "UI-TEST-A", scope: "PACKAGE", element_id: "", version: 1, kind: "XLSX", file_name: "UI-TEST.xlsx", status: "DRAFT", content_hash: "test-hash", input_snapshot_hash: "test-snapshot", template_version: "1.0.0", byte_size: 1, frozen_at: "", created_by: "test-quality", created_at: "" });
    const { container } = await mount(); await openTask(container); await click(container, "审核与归档");
    expect(button(container, "提交 V1 审核").disabled).toBe(true);
    await click(container, "下载 V1"); expect(stubs.download).toHaveBeenCalledWith("UI-TEST-PPAP", "UI-TEST-A", "UI-TEST.xlsx");
    detail.artifacts[0].status = "APPROVED"; detail.artifacts[0].frozen_at = "test-date";
    await click(container, "刷新列表");
    expect(container.textContent).toContain("已冻结 test-date");
    expect(button(container, "提交 V1 审核")).toBeUndefined(); expect(button(container, "归档 V1")).toBeTruthy();
  });
  it("确认无值与未填写分开保存，修订已有null不会静默丢弃", async () => {
    detail.inputs.push({ input_id: "UI-TEST-IN", dataset: "inspection_value", record_key: "UI-TEST-K", part_side: "LEFT", element_id: "", payload: { characteristic_id: "UI-TEST-C", point: "UI-TEST-P", value: null, unit: "mm" }, source_type: "user_confirmation", source_ref: "UI-TEST-REF", confirmed_by: "test-quality", confirmed_at: "" });
    const { container } = await mount(); await openTask(container); await click(container, "载入修订");
    expect(container.querySelector<HTMLButtonElement>('[aria-label="实测值确认无值"]')?.getAttribute("aria-pressed")).toBe("true");
    const form = button(container, "确认并保存输入").closest("form")!;
    await act(async () => { form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
    expect(stubs.input.mock.calls[0][1].payload.value).toBeNull();
    expect(stubs.input.mock.calls[0][1].payload).not.toHaveProperty("missing_reason");
  });
  it("同账号审核展示分离提示，提交决定只携带review ID、decision及意见", async () => {
    detail.task.package_state = "PENDING_REVIEW";
    detail.applicability = { total: 1, confirmed: 1, unconfirmed_element_ids: [] };
    detail.elements[0].requirement = "retain";
    detail.artifacts.push({ artifact_id: "UI-TEST-A", scope: "PACKAGE", element_id: "", version: 1, kind: "xlsx", file_name: "UI-TEST.xlsx", status: "DRAFT", content_hash: "UI-TEST-HASH", input_snapshot_hash: "UI-TEST-SNAPSHOT", template_version: "1.0.0", byte_size: 100, frozen_at: "", created_by: "test-quality", created_at: "" });
    stubs.impact.mockResolvedValue({ ok: true, has_artifact: true, comparison_artifact_id: "UI-TEST-A", changed: false });
    detail.reviews.push({ review_id: "UI-TEST-R", artifact_id: "UI-TEST-A", artifact_version: 1, decision: "SUBMITTED", reviewer: "test-quality", reviewer_role: "quality_manager", author: "test-quality", separation_warning: "author_reviewer_same_account", comments: "", created_at: "" });
    const { container } = await mount(); await openTask(container); await click(container, "审核与归档");
    expect(container.textContent).toContain("编制人与审核人相同"); expect(button(container, "批准此版本").disabled).toBe(true);
    await change(field(container, "审核 / 归档意见"), "界面测试：核对输入及产物"); await click(container, "批准此版本");
    expect(stubs.decide).toHaveBeenCalledWith("UI-TEST-PPAP", "UI-TEST-R", "APPROVED", "界面测试：核对输入及产物");
  });
  it("幂等编制结果明确显示复用；来源失败不会被显示为无此项", async () => {
    stubs.compile.mockResolvedValue({ success: true, idempotent_replay: true });
    const { container } = await mount(); await openTask(container); await click(container, "编制与检查");
    await click(container, "请求编制草稿"); expect(container.textContent).toContain("复用既有运行，未重复生成");
    stubs.collect.mockRejectedValue(new Error("原始来源读取失败")); await click(container, "准备资料"); await click(container, "来源资料");
    await click(container, "按已授权计划取证"); expect(container.querySelector('[role="alert"]')?.textContent).toContain("原始来源读取失败");
  });
});
