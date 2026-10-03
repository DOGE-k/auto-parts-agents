// 问答跳转定位回归（评审意见①）：回答携带业务对象编号时，跳转按钮必须带 target
// 打开对应对象，而不是只切换到空模块。
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const askAssistantMock = vi.fn();

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    askAssistant: (...args: unknown[]) => askAssistantMock(...args),
    askAssistantStream: vi.fn(() => Promise.reject(new Error("transport failure"))),
  };
});

import AssistantPanel from "../AssistantPanel";
import type { AssistantAnswer } from "../../api";

function answerWith(overrides: Partial<AssistantAnswer>): AssistantAnswer {
  return {
    question: "WO-2026-001 什么时候能做完？",
    answer: "该工单尚未完成。",
    call_chain: [
      {
        seq: 1, caller: "coordinator", callee: "tracking",
        skill_id: "tracking.track_real",
        arguments: { work_order_id: "2" },
        result_summary: "ok", status: "ok", elapsed_ms: 10,
      },
    ],
    rounds: 1,
    tool_count: 1,
    coordination_run_id: "RUN-COORD-TEST",
    authority: "ERPNext + OpenMES",
    context: {},
    ...overrides,
  };
}

function mountPanel(
  onNavigate: (module: "sales" | "procurement" | "production" | "quality", target?: { work_order_id?: string; work_order_no?: string; quotation_id?: string; plan_id?: string; erp_order_id?: string }) => void,
): {
  container: HTMLElement; cleanup: () => void;
} {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  act(() => {
    root.render(
      <AssistantPanel
        identity={null}
        currentErpDraftId=""
        currentWorkOrderId=""
        notify={() => undefined}
        onError={() => undefined}
        onNavigate={onNavigate}
      />,
    );
  });
  return {
    container,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const cleanups: Array<() => void> = [];
afterEach(() => {
  for (const fn of cleanups.splice(0).reverse()) fn();
  askAssistantMock.mockReset();
});

function findButton(container: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes(text));
}

describe("AssistantPanel 问答跳转定位", () => {
  it("调用链带 work_order_id 时显示'查看该工单跟单'并携带对象编号", async () => {
    const onNavigate = vi.fn();
    askAssistantMock.mockResolvedValue(answerWith({}));
    const panel = mountPanel(onNavigate);
    cleanups.push(panel.cleanup);
    const textarea = panel.container.querySelector("textarea")!;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")!.set!;
    act(() => {
      setter.call(textarea, "WO-2026-001 什么时候能做完？");
      textarea.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    await act(async () => {
      findButton(panel.container, "提问")!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const button = findButton(panel.container, "查看该工单跟单");
    expect(button).toBeTruthy();
    act(() => {
      button!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    expect(onNavigate).toHaveBeenCalledTimes(1);
    const [moduleArg, targetArg] = onNavigate.mock.calls[0];
    expect(moduleArg).toBe("production");
    expect(targetArg).toEqual({ work_order_id: "2", work_order_no: undefined });
  });

  it("结构化方案字段（proposal_options）优先给出方案审批跳转", async () => {
    const onNavigate = vi.fn();
    askAssistantMock.mockResolvedValue(answerWith({
      call_chain: [],
      proposal_options: {
        plan_id: "PROC-TEST1",
        quotation_id: "QUO-TEST1",
        shortage: { finished_item: "BD-2401", shortage_count: 1, shortage_items: [] },
        supplier_options: [],
        cost_assessments: [],
      } as AssistantAnswer["proposal_options"],
    }));
    const panel = mountPanel(onNavigate);
    cleanups.push(panel.cleanup);
    const textarea = panel.container.querySelector("textarea")!;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")!.set!;
    act(() => {
      setter.call(textarea, "缺料怎么办");
      textarea.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    await act(async () => {
      findButton(panel.container, "提问")!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const button = findButton(panel.container, "打开该方案审批");
    expect(button).toBeTruthy();
    act(() => {
      button!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    const [moduleArg, targetArg] = onNavigate.mock.calls[0];
    expect(moduleArg).toBe("procurement");
    expect(targetArg).toEqual({ quotation_id: "QUO-TEST1", plan_id: "PROC-TEST1" });
  });
});
