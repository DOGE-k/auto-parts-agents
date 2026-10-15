// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import PpapRecordSummary from "../PpapRecordSummary";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
it("中文摘要保留0、false、明确null、空字符串和完整数组原值", async () => {
  const container = document.createElement("div"); const root = createRoot(container);
  const payload = { count: 0, flag: false, absent: null, blank: "", samples: [0, null, 1.2] };
  await act(async () => root.render(<PpapRecordSummary payload={payload} labels={{ count: "数量", flag: "是否特殊", absent: "缺测", blank: "备注", samples: "样本" }} />));
  const summary = container.querySelector("dl")!.textContent;
  expect(summary).toContain("数量0"); expect(summary).toContain("否（false）"); expect(summary).toContain("明确无值（null）");
  expect(summary).toContain("空字符串"); expect(summary).toContain("3 项");
  expect(container.querySelector("details")!.open).toBe(false);
  expect(JSON.parse(container.querySelector("pre")!.textContent!)).toEqual(payload);
  await act(async () => root.unmount());
});
