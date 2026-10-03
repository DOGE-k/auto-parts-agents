import { describe, expect, it } from "vitest";
import { formatAssistantAnswer } from "../markdown";

describe("formatAssistantAnswer", () => {
  it("escapes html to prevent injection", () => {
    const out = formatAssistantAnswer("<script>alert(1)</script>");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;script&gt;");
  });

  it("renders bold markers as strong tags", () => {
    expect(formatAssistantAnswer("**结论**：可以")).toContain("<strong>结论</strong>");
  });

  it("renders markdown tables into md-table structure", () => {
    const md = ["| 方案 | 成本 |", "|---|---|", "| A | +1180 |", "| B | +1080 |"].join("\n");
    const out = formatAssistantAnswer(md);
    expect(out).toContain('class="md-table"');
    expect((out.match(/<tr>/g) ?? []).length).toBe(3); // 表头 + 2 数据行，分隔行被跳过
    expect(out).toContain("+1180");
  });
});
