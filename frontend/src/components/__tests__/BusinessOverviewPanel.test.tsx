// 业务总览面板回归（信息架构改版 §4.4）：KPI 概览、需要处理列表、AI 协同入口
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMock = vi.fn((path: string) => {
  if (path.startsWith("/real-orders/quotations")) {
    return Promise.resolve([
      { quotation_id: "QUO-A", status: "PENDING_APPROVAL" },
      { quotation_id: "QUO-B", status: "APPROVED" },
    ]);
  }
  if (path.startsWith("/real-orders/procurement/plans")) {
    return Promise.resolve([
      { plan_id: "PROC-A", status: "PENDING_APPROVAL" },
    ]);
  }
  if (path.startsWith("/real-orders/agent-runs")) {
    return Promise.resolve([
      { run_id: "RUN-1", agent_type: "tracking", operation: "track", result_status: "ok", result_summary: "DONE" },
    ]);
  }
  return Promise.resolve([]);
});
const eventsMock = vi.fn(() =>
  Promise.resolve({
    items: [
      { event_id: "EVT-R1", event_type: "production_overdue", status: "PENDING", payload: { work_order_no: "WO-1" } },
      { event_id: "EVT-C1", event_type: "material_shortage", status: "COMPLETED", payload: {} },
    ],
    count: 2,
  }),
);

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    api: (...args: unknown[]) => apiMock(...(args as [string])),
    getCollaborationEvents: (...args: unknown[]) => eventsMock(...(args as [])),
  };
});

import BusinessOverviewPanel from "../BusinessOverviewPanel";

function mountPanel(): { container: HTMLElement; root: Root; cleanup: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <BusinessOverviewPanel
        hasProjectSession={false}
        qualityTodoCount={null}
        onNavigateAssistant={() => undefined}
        onNavigate={() => undefined}
      />,
    );
  });
  return {
    container,
    root,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const cleanups: Array<() => void> = [];
afterEach(() => {
  for (const fn of cleanups.splice(0).reverse()) fn();
  apiMock.mockClear();
  eventsMock.mockClear();
});

describe("BusinessOverviewPanel 业务总览", () => {
  it("KPI 汇总报价/方案待审批与风险事件数量", async () => {
    const panel = mountPanel();
    cleanups.push(panel.cleanup);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const text = panel.container.textContent ?? "";
    expect(text).toContain("报价待审批");
    expect(text).toContain("方案待审批");
    expect(text).toContain("缺料/风险事件");
  });

  it("未完成事件进入需要处理列表，已完成事件不出现", async () => {
    const panel = mountPanel();
    cleanups.push(panel.cleanup);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const text = panel.container.textContent ?? "";
    expect(text).toContain("EVT-R1");
    expect(text).toContain("查看生产跟单");
    expect(text).not.toContain("EVT-C1");
  });

  it("AI 协同入口输入问题后跳转协同问答", async () => {
    const onNavigateAssistant = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    cleanups.push(() => {
      act(() => root.unmount());
      container.remove();
    });
    act(() => {
      root.render(
        <BusinessOverviewPanel
          hasProjectSession={false}
          qualityTodoCount={null}
          onNavigateAssistant={onNavigateAssistant}
          onNavigate={() => undefined}
        />,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const input = container.querySelector("input")!;
    const button = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("去提问"))!;
    act(() => {
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    // React 受控输入：用原生 setter 赋值
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, "SAL-ORD-2026-00023 什么时候能做完？");
    act(() => {
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    await act(async () => {
      button.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    expect(onNavigateAssistant).toHaveBeenCalledWith("SAL-ORD-2026-00023 什么时候能做完？");
  });
});
