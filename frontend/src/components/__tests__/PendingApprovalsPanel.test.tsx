// 待人工审批面板回归（评审意见④）：审批与审计页展示停留在门禁的报价/方案并可定位打开
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMock = vi.fn((path: string) => {
  if (path.startsWith("/real-orders/quotations")) {
    return Promise.resolve([
      { quotation_id: "QUO-P1", status: "PENDING_APPROVAL", item: { item_code: "BD-2401", item_name: "制动盘-前轮" }, quantity: 500, total_price: "5000", currency: "CNY" },
      { quotation_id: "QUO-D1", status: "APPROVED", item: {}, quantity: 1, total_price: "", currency: "CNY" },
    ]);
  }
  if (path.startsWith("/real-orders/procurement/plans")) {
    return Promise.resolve([
      { plan_id: "PROC-P1", quotation_id: "QUO-P1", status: "PENDING_APPROVAL", net_requirement: { shortage_count: 2 }, supplier_options: [{}, {}] },
    ]);
  }
  return Promise.resolve([]);
});

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, api: (...args: unknown[]) => apiMock(...(args as [string])) };
});

import PendingApprovalsPanel from "../PendingApprovalsPanel";

const cleanups: Array<() => void> = [];
afterEach(() => {
  for (const fn of cleanups.splice(0).reverse()) fn();
  apiMock.mockClear();
});

function mountPanel(onOpenQuotation: (id: string) => void, onOpenPlan: (qid: string | undefined, pid: string) => void): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  act(() => {
    root.render(<PendingApprovalsPanel onOpenQuotation={onOpenQuotation} onOpenPlan={onOpenPlan} />);
  });
  cleanups.push(() => {
    act(() => root.unmount());
    container.remove();
  });
  return container;
}

describe("PendingApprovalsPanel 待人工审批", () => {
  it("只列 PENDING_APPROVAL 的报价与方案，点击回调携带编号", async () => {
    const onOpenQuotation = vi.fn();
    const onOpenPlan = vi.fn();
    const container = mountPanel(onOpenQuotation, onOpenPlan);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const text = container.textContent ?? "";
    expect(text).toContain("QUO-P1");
    expect(text).toContain("PROC-P1");
    expect(text).not.toContain("QUO-D1");
    const buttons = Array.from(container.querySelectorAll("button"));
    const quoteBtn = buttons.find((b) => b.textContent?.includes("打开报价审批"))!;
    const planBtn = buttons.find((b) => b.textContent?.includes("打开方案审批"))!;
    act(() => {
      quoteBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
      planBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    expect(onOpenQuotation).toHaveBeenCalledWith("QUO-P1");
    expect(onOpenPlan).toHaveBeenCalledWith("QUO-P1", "PROC-P1");
  });
});
