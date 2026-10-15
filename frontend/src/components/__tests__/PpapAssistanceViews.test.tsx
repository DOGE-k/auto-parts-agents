// @vitest-environment jsdom
// These are isolated UI fixtures, never ERP/MES evidence or usable PPAP data.
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PpapArtifact, PpapElement, PpapIssue, PpapJson } from "../../types/ppap";
import { PpapMissingItemsView, PpapVersionDiffView } from "../PpapAssistanceViews";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const cleanups: Array<() => void> = [];
afterEach(() => { cleanups.splice(0).reverse().forEach((cleanup) => cleanup()); });
const elements: PpapElement[] = [
  { element_id: "dimensional_results", title: "全尺寸结果", responsible_role: "quality_manager" } as PpapElement,
  { element_id: "process_flow", title: "过程流程图", responsible_role: "production_manager" } as PpapElement,
];
const navigation = { kind: "input", dataset: "inspection_value", field: "unit", element_id: "dimensional_results", input_id: "UI-TEST-PIN", record_key: "UI-TEST-R1", part_side: "LEFT", reason: "补充同侧检验记录的确认单位" };
const report = () => ({ findings: [
  { dedup_key: "UI-TEST-F1", severity: "BLOCKER", blocks_approval: true, element_id: "dimensional_results", responsible_role: "quality_manager", title: "单位缺失", detail: "实测值必须有确认单位", field_path: "payload.unit", navigation },
  { dedup_key: "UI-TEST-F2", severity: "MAJOR", blocks_approval: false, element_id: "process_flow", responsible_role: "production_manager", title: "工序资料待补充", detail: "原始工序来源缺失", navigation: { kind: "input", dataset: "process_operation" } },
  { dedup_key: "UI-TEST-F3", severity: "BLOCKER", blocks_approval: true, element_id: "", responsible_role: "quality_manager", title: "规则未确认", detail: "客户判定规则需确认", navigation: { kind: "rules", reason: "必须由业务确认规则" } },
], readiness: [{ element_id: "dimensional_results", missing_inputs: ["inspection_value"], missing_evidence: ["user_upload"], data_ready: false }] });
async function mount(view: ReactElement) {
  const container = document.createElement("div"); document.body.appendChild(container);
  const root: Root = createRoot(container);
  await act(async () => root.render(view));
  cleanups.push(() => { act(() => root.unmount()); container.remove(); });
  return container;
}
async function select(container: HTMLElement, label: string, value: string) {
  const node = container.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!.call(node, value);
    node.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
}
const articles = (container: HTMLElement) => Array.from(container.querySelectorAll("article"));
const artifacts: PpapArtifact[] = [
  { artifact_id: "UI-TEST-A1", scope: "PACKAGE", version: 1, created_at: "UI-TEST-DATE1" } as PpapArtifact,
  { artifact_id: "UI-TEST-A2", scope: "PACKAGE", version: 2, created_at: "UI-TEST-DATE2" } as PpapArtifact,
  { artifact_id: "UI-TEST-E1", scope: "ELEMENT", version: 7, created_at: "UI-TEST-DATE3" } as PpapArtifact,
];
const change = (overrides: PpapJson = {}) => ({ section: "inputs", dataset: "inspection_value", record_key: "UI-TEST-R1", part_side: "LEFT", input_id: "UI-TEST-PIN", field_path: "payload.value", old_present: true, new_present: true, old_available: true, new_available: true, old_value: null, new_value: 3.2, old_source: { source_type: "user_confirmation", source_ref: "UI-TEST-原记录", confirmed_by: "UI-TEST-QA" }, new_source: { source_type: "user_confirmation", source_ref: "UI-TEST-新记录" }, change_type: "modified", ...overrides });
const diff = (changes: PpapJson[]) => ({ ok: true, has_artifact: true, changed: true, changes, affected_elements: ["dimensional_results"], impact_reasons: [{ element_id: "dimensional_results", reason: "检验输入已修订" }] });

describe("PPAP 缺项定位", () => {
  it("未编制也显示实时发现，分别统计实时与持久问题", async () => {
    const issues = [{ issue_id: "UI-TEST-I1", dedup_key: "UI-TEST-F1", status: "OPEN" }, { issue_id: "UI-TEST-I2", status: "RESOLVED" }] as unknown as PpapIssue[];
    const onNavigate = vi.fn();
    const container = await mount(<PpapMissingItemsView report={report()} elements={elements} issues={issues} onNavigate={onNavigate} />);
    expect(container.textContent).toContain("实时检查发现 3 项，其中阻断 2 项；已登记未处理问题 1 项");
    expect(articles(container)).toHaveLength(2);
    expect(articles(container)[0].textContent).toContain("已登记");
    expect(articles(container)[1].textContent).toContain("未登记");
    expect(container.textContent).toContain("检验原始值");
    expect(onNavigate).not.toHaveBeenCalled();
  });
  it("按全部、条目和岗位筛选，并支持任务整体范围", async () => {
    const container = await mount(<PpapMissingItemsView report={report()} elements={elements} onNavigate={vi.fn()} />);
    await select(container, "缺项发现范围", "ALL"); expect(articles(container)).toHaveLength(3);
    await select(container, "缺项条目筛选", "process_flow"); expect(articles(container)).toHaveLength(1);
    expect(articles(container)[0].textContent).toContain("工序资料待补充");
    await select(container, "缺项条目筛选", ""); await select(container, "缺项岗位筛选", "production_manager");
    expect(articles(container)).toHaveLength(1);
    await select(container, "缺项岗位筛选", ""); await select(container, "缺项条目筛选", "__task__");
    expect(articles(container)).toHaveLength(1); expect(articles(container)[0].textContent).toContain("规则未确认");
  });
  it("去补充精确转交显式记录键与侧别，不根据field_path猜入口", async () => {
    const onNavigate = vi.fn();
    const container = await mount(<PpapMissingItemsView report={report()} elements={elements} onNavigate={onNavigate} />);
    await act(async () => container.querySelector<HTMLButtonElement>("article button")!.click());
    expect(onNavigate).toHaveBeenCalledOnce(); expect(onNavigate).toHaveBeenCalledWith(navigation);
    expect(container.textContent).toContain("记录键：UI-TEST-R1"); expect(container.textContent).toContain("侧别：左件");
  });
  it("规则、未知目标及不完整input目标只显示原因，无可编辑入口", async () => {
    const onNavigate = vi.fn();
    const container = await mount(<PpapMissingItemsView report={{ findings: [
      { severity: "BLOCKER", field_path: "inputs.unit", navigation: { kind: "manual", reason: "无法唯一定位原记录" } },
      { severity: "BLOCKER", navigation: { kind: "rules", reason: "客户规则待确认" } },
      { severity: "BLOCKER", navigation: { kind: "input", reason: "未提供数据集" } },
    ] }} elements={elements} onNavigate={onNavigate} />);
    expect(container.querySelectorAll("article button")).toHaveLength(0);
    expect(container.textContent).toContain("无法唯一定位原记录"); expect(container.textContent).toContain("规则需业务确认");
    expect(onNavigate).not.toHaveBeenCalled();
  });
  it("保留父组件权限禁用且不把空结果表述为已批准", async () => {
    const onNavigate = vi.fn();
    const container = await mount(<PpapMissingItemsView report={report()} elements={elements} disabled onNavigate={onNavigate} />);
    expect(container.querySelector<HTMLButtonElement>("article button")!.disabled).toBe(true);
    await act(async () => container.querySelector<HTMLButtonElement>("article button")!.click()); expect(onNavigate).not.toHaveBeenCalled();
    const empty = await mount(<PpapMissingItemsView report={{ findings: [] }} elements={elements} onNavigate={onNavigate} />);
    expect(empty.textContent).toContain("不代表资料已获批准");
  });
});

describe("PPAP 版本修改对照", () => {
  it("严格区分明确null、未提供和历史未保存，绝不把旧null猜成0", async () => {
    const container = await mount(<PpapVersionDiffView impact={diff([
      change(), change({ field_path: "payload.unit", old_present: false, old_value: null, new_value: null }),
      change({ field_path: "payload.values", old_available: false, old_value: null, new_value: [2, 3] }),
    ])} artifacts={artifacts} compareArtifactId="" onCompare={vi.fn()} elements={elements} />);
    const rows = container.querySelectorAll("tbody tr");
    expect(rows[0].children[1].textContent).toContain("明确为空（null）");
    expect(rows[0].children[2].textContent).toContain("3.2");
    expect(rows[1].children[1].querySelector("span")!.textContent).toBe("未提供"); expect(rows[1].children[2].textContent).toContain("明确为空（null）");
    expect(rows[2].children[1].textContent).toContain("历史未保存原值，无法还原");
    expect(container.textContent).toContain("全尺寸结果：检验输入已修订");
  });
  it("数组完整保留并折叠展示，保持来源、侧别、记录键与中文字段标签", async () => {
    const before = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]; const after = [[[1, 2], [3, 9]], [[5, 6], [7, 8]]];
    const container = await mount(<PpapVersionDiffView impact={diff([change({ field_path: "payload.measurements", old_value: before, new_value: after })])} artifacts={artifacts} compareArtifactId="" onCompare={vi.fn()} />);
    const values = container.querySelectorAll("pre"); expect(values[0].textContent).toBe(JSON.stringify(before, null, 2));
    expect(values[1].textContent).toBe(JSON.stringify(after, null, 2));
    expect(container.textContent).toContain("评价人 × 零件 × 测次原始数据"); expect(container.textContent).toContain("侧别：左件");
    expect(container.textContent).toContain("记录键：UI-TEST-R1"); expect(container.textContent).toContain("UI-TEST-原记录");
    expect(container.querySelectorAll(".ppap-diff-value")).toHaveLength(2);
    expect(container.querySelector<HTMLDetailsElement>(".ppap-diff-value")!.open).toBe(false);
  });
  it("只提供PACKAGE版本选择，选择才发只读回调", async () => {
    const onCompare = vi.fn();
    const container = await mount(<PpapVersionDiffView impact={diff([change()])} artifacts={artifacts} compareArtifactId="" onCompare={onCompare} />);
    expect(onCompare).not.toHaveBeenCalled();
    const choices = container.querySelectorAll("select option"); expect(choices).toHaveLength(3);
    expect(choices[1].textContent).toContain("V2"); expect(choices[2].textContent).toContain("V1");
    await select(container, "PPAP 对照版本", "UI-TEST-A1"); expect(onCompare).toHaveBeenCalledWith("UI-TEST-A1");
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });
  it("无版本、加载和缺少状态明确展示，不从空数组推断无变化", async () => {
    const empty = await mount(<PpapVersionDiffView impact={{ has_artifact: false }} artifacts={[]} compareArtifactId="" onCompare={vi.fn()} />);
    expect(empty.textContent).toContain("尚无编制版本"); expect(empty.querySelector<HTMLSelectElement>("select")!.disabled).toBe(true);
    const unavailable = await mount(<PpapVersionDiffView impact={diff([change({ old_present: undefined, old_available: undefined })])} artifacts={artifacts} compareArtifactId="" onCompare={vi.fn()} />);
    expect(unavailable.textContent).toContain("未提供可核对的原值状态");
    const legacy = await mount(<PpapVersionDiffView impact={{ has_artifact: true, changed: true, changes: [] }} artifacts={artifacts} compareArtifactId="" onCompare={vi.fn()} />);
    expect(legacy.textContent).toContain("未提供可核对的字段级差异");
    const loading = await mount(<PpapVersionDiffView loading impact={null} artifacts={artifacts} compareArtifactId="" onCompare={vi.fn()} />);
    expect(loading.textContent).toContain("正在读取对照结果"); expect(loading.querySelector<HTMLSelectElement>("select")!.disabled).toBe(true);
  });
  it("布尔false和数值0不被空值显示吞掉", async () => {
    const container = await mount(<PpapVersionDiffView impact={diff([change({ old_value: false, new_value: 0 })])} artifacts={artifacts} compareArtifactId="" onCompare={vi.fn()} />);
    const cells = container.querySelectorAll("tbody td"); expect(cells[1].textContent).toContain("否（false）");
    expect(cells[2].textContent).toMatch(/^0/);
  });
});
