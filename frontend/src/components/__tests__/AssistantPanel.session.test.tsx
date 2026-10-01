// AssistantPanel 会话化渲染回归测试（P0）
// 背景：2026-10-01 页面走查实测——确定性回答（数量变更/方案选择/追问）不带
// call_chain 字段时，面板 call_chain.map() 崩溃降级。修复后契约：后端恒返回
// call_chain 数组，前端再做防御性守卫。本测试锁定该行为。
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

// React 18 act 环境标记
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const askAssistantMock = vi.fn();

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    askAssistant: (...args: unknown[]) => askAssistantMock(...args),
    // 面板流式优先：这里模拟传输层失败，走真实回退路径到 askAssistant
    askAssistantStream: vi.fn(() => Promise.reject(new Error("transport failure"))),
  };
});

import AssistantPanel from "../AssistantPanel";

const identity = null;

function mountPanel(): { container: HTMLElement; root: Root; container_removed: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <AssistantPanel
        identity={identity}
        currentErpDraftId=""
        currentWorkOrderId=""
        notify={() => undefined}
        onError={() => undefined}
      />,
    );
  });
  return {
    container,
    root,
    container_removed: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

function typeAndSubmit(panel: { container: HTMLElement }, question: string): void {
  // 沿用组件真实提交路径：受控输入 + Enter 键（onKeyDown 提交）
  const textarea = panel.container.querySelector("textarea");
  if (!textarea) throw new Error("textarea not found");
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  act(() => {
    setter?.call(textarea, question);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  act(() => {
    textarea.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
    );
  });
}

async function flush(times = 6): Promise<void> {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

describe("AssistantPanel 会话化渲染（P0 回归）", () => {
  const cleanups: Array<() => void> = [];

  afterEach(() => {
    for (const fn of cleanups.splice(0).reverse()) fn();
    askAssistantMock.mockReset();
  });

  it("确定性回答（无 call_chain 字段）不崩溃，并显示确定性处理标识", async () => {
    const panel = mountPanel();
    cleanups.push(panel.container_removed);
    // 模拟后端确定性数量变更回答：没有 call_chain/rounds/tool_count 字段
    askAssistantMock.mockResolvedValue({
      question: "数量改成 3000",
      answer: "已按新数量 3000 重新执行一次只读报价分析。",
      coordination_run_id: "",
      authority: "ERPNext + OpenMES",
      context: {},
      session_id: "ASST-TEST0000001",
      business_task_id: "TASK-TEST0000001",
      applied_context: "当前沿用数量=3000；如果需要修改请直接说明。",
      handled_by: "deterministic_quantity_change",
      stale_downstream: [
        { type: "quotation", id: "QUO-OLD", reason: "数量已改为 3000，需要重新确认" },
      ],
    });
    await typeAndSubmit(panel, "数量改成 3000");
    await flush();
    expect(askAssistantMock).toHaveBeenCalled();
    expect(panel.container.textContent).toContain("已按新数量 3000");
    expect(panel.container.textContent).toContain("需要重新确认");
    expect(panel.container.textContent).toContain("确定性处理");
    expect(panel.container.textContent).not.toContain("模块暂时不可用");
  });

  it("追问回答渲染缺失槽位与调用哪个智能体", async () => {
    const panel = mountPanel();
    cleanups.push(panel.container_removed);
    askAssistantMock.mockResolvedValue({
      question: "BD-2401 500 件多少钱？",
      answer: "要回答报价问题，我还缺少以下信息。",
      coordination_run_id: "",
      authority: "ERPNext + OpenMES",
      context: {},
      needs_input: true,
      handled_by: "clarify",
      missing_slots: [
        {
          slot: "customer_id",
          alternatives: ["customer_id"],
          why: "报价必须挂在真实 ERP 客户下",
          agent: "报价智能体（quotation.analyze_real）",
        },
      ],
    });
    await typeAndSubmit(panel, "BD-2401 500 件多少钱？");
    await flush();
    expect(panel.container.textContent).toContain("需要补充信息");
    expect(panel.container.textContent).toContain("customer_id");
    expect(panel.container.textContent).toContain("quotation.analyze_real");
    expect(panel.container.textContent).not.toContain("模块暂时不可用");
  });

  it("上下文回显条正常渲染", async () => {
    const panel = mountPanel();
    cleanups.push(panel.container_removed);
    askAssistantMock.mockResolvedValue({
      question: "选第二个方案",
      answer: "已选择第 2 个方案。",
      coordination_run_id: "",
      authority: "ERPNext + OpenMES",
      context: {},
      applied_context: "当前沿用订单=SAL-ORD-2026-00023；如果需要修改请直接说明。",
      handled_by: "deterministic_selection",
      pending_selection: {
        plan_id: "PROC-1",
        option_id: "OPT-2",
        option_snapshot: { option_id: "OPT-2" },
      },
    });
    await typeAndSubmit(panel, "选第二个方案");
    await flush();
    expect(panel.container.textContent).toContain("当前沿用订单=SAL-ORD-2026-00023");
    expect(panel.container.textContent).toContain("OPT-2");
    expect(panel.container.textContent).not.toContain("模块暂时不可用");
  });
});
