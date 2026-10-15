// @vitest-environment jsdom
// These are isolated UI fixtures; they are never saved as ERP/MES or formal PPAP evidence.
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import PpapStudyEditor from "../PpapStudyEditor";
import { parseStudyData, parseStudyNumber, type PpapStudyField } from "../ppapStudyData";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const cleanups: (() => void)[] = [];
afterEach(() => cleanups.splice(0).reverse().forEach((cleanup) => cleanup()));
async function mount(field: PpapStudyField, initial: string, disabled = false) {
  const container = document.createElement("div"); document.body.appendChild(container);
  const root: Root = createRoot(container); const changed = vi.fn();
  let replace: (text: string, nextField?: PpapStudyField) => void = () => {};
  function Harness() {
    const [value, setValue] = useState(initial);
    const [currentField, setField] = useState(field);
    replace = (text, nextField) => { setValue(text); if (nextField) setField(nextField); };
    return <form><PpapStudyEditor field={currentField} value={value} disabled={disabled}
      onChange={(text) => { changed(text); setValue(text); }} /></form>;
  }
  await act(async () => root.render(<Harness />));
  cleanups.push(() => { act(() => root.unmount()); container.remove(); });
  return { container, changed, load: async (text: string, nextField?: PpapStudyField) => {
    await act(async () => replace(text, nextField));
  } };
}
const input = (container: HTMLElement, label: string) => container.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!;
const current = (container: HTMLElement) => JSON.parse(container.querySelector<HTMLTextAreaElement>(".ppap-study-json textarea")!.value);
async function change(node: HTMLInputElement | HTMLTextAreaElement, value: string) {
  await act(async () => {
    const prototype = node instanceof window.HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(node, value);
    node.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
async function click(container: HTMLElement, label: string) {
  const button = Array.from(container.querySelectorAll("button")).find((node) => node.textContent === label)!;
  await act(async () => button.click());
}

describe("原始检测数据表格的实际形状与编辑", () => {
  it("识别评价人、零件、测次；载入时不改写真实顺序或 part_id 键", async () => {
    const source = [{ appraiser: "评价人乙", parts: [{ part_id: "P2", trials: [1.2, null] }, { part: "P1", trials: [2.2] }] },
      { appraiser: "评价人甲", parts: [{ part: "P1", trials: [3.2, 3.3] }] }];
    const { container, changed } = await mount("measurements", JSON.stringify(source));
    expect(container.textContent).toContain("已识别 2 位评价人、2 个零件标识");
    expect(container.textContent).toContain("测次：2 / 1");
    expect(changed).not.toHaveBeenCalled();
    await change(input(container, "评价人 1 零件 1 测次 2"), "1.35");
    const expected = structuredClone(source); expected[0].parts[0].trials[1] = 1.35;
    expect(current(container)).toEqual(expected);
    expect(current(container)[0].parts[0]).not.toHaveProperty("part");
  });
  it("空方案需主动新增维度；不会初始化测量数据或默认三乘十乘三", async () => {
    const { container, changed } = await mount("measurements", "");
    expect(changed).not.toHaveBeenCalled();
    expect(container.textContent).toContain("尚无采集方案");
    await click(container, "新增评价人");
    expect(current(container)).toEqual([{ appraiser: "", parts: [] }]);
    await click(container, "评价人 1 新增零件");
    expect(current(container)[0].parts[0]).toEqual({ part: "", trials: [] });
    await click(container, "评价人 1 零件 1 增加测次");
    expect(current(container)[0].parts[0].trials).toEqual([null]);
    await change(input(container, "评价人 1 标识"), "实际评价人");
    await change(input(container, "评价人 1 零件 1 标识"), "实际零件");
    expect(current(container)[0].appraiser).toBe("实际评价人");
    await click(container, "评价人 1 增加一轮测次");
    expect(current(container)[0].parts[0].trials).toEqual([null, null]);
  });
  it("MSA 删除指定记录不排序其他记录；各零件的缺测保持 null", async () => {
    const { container } = await mount("measurements", JSON.stringify([{ appraiser: "B", parts: [
      { part: "P2", trials: [8, 9] }, { part: "P1", trials: [7, null] }] }, { appraiser: "A", parts: [] }]));
    await click(container, "评价人 1 零件 1 删除末次");
    expect(current(container)[0].parts[0].trials).toEqual([8]);
    await click(container, "删除评价人 1 零件 1");
    expect(current(container)[0].parts).toEqual([{ part: "P1", trials: [7, null] }]);
    await click(container, "删除评价人 1");
    expect(current(container)).toEqual([{ appraiser: "A", parts: [] }]);
  });
  it("清空样本为 null；负数、小数及科学计数数值照实保存", async () => {
    const { container } = await mount("values", "[4,5,6]");
    await change(input(container, "原始样本 1"), "");
    await change(input(container, "原始样本 2"), "-.25");
    await change(input(container, "原始样本 3"), "1e-3");
    expect(current(container)).toEqual([null, -.25, .001]);
    await click(container, "新增原始样本");
    expect(current(container)).toEqual([null, -.25, .001, null]);
    await click(container, "删除样本 2");
    expect(current(container)).toEqual([null, .001, null]);
  });
  it("非法数值保留可见草稿并阻止表单保存，不覆盖原值或变成零", async () => {
    const { container, changed } = await mount("values", "[4,5]");
    await change(input(container, "原始样本 1"), "true");
    expect(changed).not.toHaveBeenCalled();
    expect(input(container, "原始样本 1").value).toBe("true");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    await change(input(container, "原始样本 2"), "6");
    expect(current(container)).toEqual([4, 6]);
    expect(input(container, "原始样本 1").value).toBe("true");
    await click(container, "删除样本 2");
    expect(current(container)).toEqual([4, 6]);
    await change(input(container, "原始样本 1"), "3");
    expect(container.querySelector("form")!.checkValidity()).toBe(true);
    expect(current(container)).toEqual([3, 6]);
  });
  it("同字段外部载入有效记录时清掉复用 DOM 的旧数值错误", async () => {
    const { container, load } = await mount("values", "[4,5]");
    const reusedInput = input(container, "原始样本 1");
    await change(reusedInput, "Infinity");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    expect(reusedInput.validationMessage).not.toBe("");
    await load("[7,8]");
    expect(input(container, "原始样本 1")).toBe(reusedInput);
    expect(reusedInput.value).toBe("7");
    expect(reusedInput.validationMessage).toBe("");
    expect(reusedInput.getAttribute("aria-invalid")).toBe("false");
    expect(container.querySelector("form")!.checkValidity()).toBe(true);
    expect(container.textContent).not.toContain("存在未确认的数值");
  });
  it("高级 JSON 错误后的外部有效载入清错误并恢复表格保存", async () => {
    const { container, load } = await mount("values", "[4,5]");
    const reusedRaw = container.querySelector<HTMLTextAreaElement>(".ppap-study-json textarea")!;
    await change(reusedRaw, "[true]");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    await load("[7,null]");
    expect(container.querySelector(".ppap-study-json textarea")).toBe(reusedRaw);
    expect(reusedRaw.validationMessage).toBe("");
    expect(current(container)).toEqual([7, null]);
    expect(container.querySelector("form")!.checkValidity()).toBe(true);
  });
  it("字段切换清旧错误，但新载入的数据非法时仍禁止保存", async () => {
    const { container, load } = await mount("values", "[4,5]");
    await change(input(container, "原始样本 1"), "bad");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    await load("[[7,8]]", "subgroups");
    expect(input(container, "子组 1 样本 1").value).toBe("7");
    expect(container.querySelector("form")!.checkValidity()).toBe(true);
    await load("[[true]]");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    expect(container.textContent).toContain("不接受布尔值");
  });
  it("本地未修复草稿不会被有效邻格回声或普通重渲染清除", async () => {
    const { container, changed } = await mount("values", "[4,5]");
    await change(input(container, "原始样本 1"), "NaN");
    expect(changed).not.toHaveBeenCalled();
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    await change(input(container, "原始样本 2"), "6");
    expect(input(container, "原始样本 1").value).toBe("NaN");
    expect(input(container, "原始样本 1").validationMessage).not.toBe("");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    expect(current(container)).toEqual([4, 6]);
  });
  it("子组编辑保留边界和顺序，不会默认展平或生成原始序列", async () => {
    const { container } = await mount("subgroups", "[[3,1],[2],[]]");
    expect(container.textContent).toContain("组内数量：2 / 1 / 0");
    await change(input(container, "子组 1 样本 2"), "1.5");
    expect(current(container)).toEqual([[3, 1.5], [2], []]);
    await click(container, "子组 2 新增样本");
    await click(container, "新增子组");
    expect(current(container)).toEqual([[3, 1.5], [2, null], [], []]);
    await click(container, "删除子组 3");
    expect(current(container)).toEqual([[3, 1.5], [2, null], []]);
  });
  it("未知复杂字段只保留高级 JSON，不能因表格渲染丢字段", async () => {
    const source = '[{"appraiser":"A","parts":[{"part":"P1","trials":[1,2],"calibration":"C1"}]}]';
    const { container, changed } = await mount("measurements", source);
    expect(container.querySelector("table")).toBeNull();
    expect(container.textContent).toContain("不会删改原数据");
    expect(changed).not.toHaveBeenCalled();
    const raw = container.querySelector<HTMLTextAreaElement>(".ppap-study-json textarea")!;
    expect(raw.value).toBe(source);
    const revision = source.replace("C1", "C2");
    await change(raw, revision);
    expect(changed).toHaveBeenLastCalledWith(revision);
    expect(raw.value).toBe(revision);
  });
  it("高级 JSON 里已有非法数值也会阻止保存；原文本不被修正或舍弃", async () => {
    const { container, changed } = await mount("values", "[true,null]");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    expect(container.querySelector("table")).toBeNull();
    expect(changed).not.toHaveBeenCalled();
    const raw = container.querySelector<HTMLTextAreaElement>(".ppap-study-json textarea")!;
    await change(raw, "[1,");
    expect(raw.value).toBe("[1,");
    expect(container.querySelector("form")!.checkValidity()).toBe(false);
    await change(raw, "[1,null]");
    expect(container.querySelector("form")!.checkValidity()).toBe(true);
    expect(current(container)).toEqual([1, null]);
  });
  it("照片引用按行保留顺序和空行，不冒充文件上传", async () => {
    const { container } = await mount("photo_refs", '["local-ref-B","local-ref-A"]');
    expect(input(container, "照片引用每行一个").value).toBe("local-ref-B\nlocal-ref-A");
    await change(input(container, "照片引用每行一个"), "ref-1\n\nref-2");
    expect(current(container)).toEqual(["ref-1", "", "ref-2"]);
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });
  it("未登录或忙碌时禁用全部输入、高级 JSON 和结构改动", async () => {
    const { container, changed } = await mount("measurements", '[{"appraiser":"A","parts":[{"part":"P1","trials":[1,2]}]}]', true);
    expect(Array.from(container.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement>("input,textarea,button")).every((node) => node.disabled)).toBe(true);
    await click(container, "新增评价人");
    expect(changed).not.toHaveBeenCalled();
  });
});

describe("纯解析边界", () => {
  it("拒绝布尔、数字字符串和非有限 JSON 数值，允许明确缺测", () => {
    for (const text of ["[true]", '["1"]', "[1e309]"]) expect(parseStudyData("values", text)).toMatchObject({ supported: false, invalid: true });
    expect(parseStudyData("values", "[null,0,-1.5]")).toMatchObject({ supported: true, data: [null, 0, -1.5] });
    expect(parseStudyData("measurements", '[{"appraiser":"A","parts":[{"part":"P1","trials":[false]}]}]')).toMatchObject({ supported: false, invalid: true });
  });
  it("不承认非十进制、布尔、无穷或半截数值为有效测量", () => {
    for (const text of ["true", "Infinity", "NaN", "1e309", "0x11", "-", "1e", "1,2"]) expect(parseStudyNumber(text)).toEqual({ valid: false });
    expect(parseStudyNumber("  ")).toEqual({ valid: true, value: null });
    expect(parseStudyNumber("0")).toEqual({ valid: true, value: 0 });
  });
  it("不支持形状和内嵌换行引用不静默改写，空方案没有隐藏默认", () => {
    expect(parseStudyData("subgroups", "[1,2]")).toMatchObject({ supported: false, invalid: false });
    expect(parseStudyData("photo_refs", '["ref\\nembedded"]')).toMatchObject({ supported: false, invalid: false });
    expect(parseStudyData("measurements", "")).toEqual({ supported: true, data: [], empty: true });
    expect(parseStudyData("measurements", "null")).toEqual({ supported: true, data: [], empty: true });
  });
});
