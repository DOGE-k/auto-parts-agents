// ErrorBoundary 组件测试（阶段十演示容错：面板崩溃降级 + 重试恢复）
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import ErrorBoundary from "../ErrorBoundary";

// React 18 act 环境标记
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Bomb({ exploding }: { exploding: boolean }): string {
  if (exploding) throw new Error("面板渲染爆炸");
  return "正常内容";
}

describe("ErrorBoundary", () => {
  const containers: HTMLElement[] = [];
  const roots: Root[] = [];

  function mount(ui: React.ReactNode): { container: HTMLElement; root: Root } {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(ui);
    });
    containers.push(container);
    roots.push(root);
    return { container, root };
  }

  afterEach(() => {
    for (const root of roots.splice(0).reverse()) {
      act(() => root.unmount());
    }
    for (const c of containers.splice(0)) c.remove();
    vi.restoreAllMocks();
  });

  it("子组件正常时直接渲染，不出现降级卡片", () => {
    const { container } = mount(
      <ErrorBoundary name="测试面板"><Bomb exploding={false} /></ErrorBoundary>,
    );
    expect(container.textContent).toContain("正常内容");
    expect(container.textContent).not.toContain("模块暂时不可用");
  });

  it("子组件渲染抛错时降级为面板卡片（含面板名与错误信息），不拖垮整页", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { container } = mount(
      <ErrorBoundary name="智能协同问答"><Bomb exploding /></ErrorBoundary>,
    );
    expect(container.textContent).toContain("智能协同问答 · 模块暂时不可用");
    expect(container.textContent).toContain("面板渲染爆炸");
    expect(container.textContent).toContain("重试恢复该面板");
    expect(spy).toHaveBeenCalled();
  });

  it("点击重试后重置错误状态；子组件恢复时重新渲染正常内容", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { container, root } = mount(
      <ErrorBoundary name="测试面板"><Bomb exploding /></ErrorBoundary>,
    );
    expect(container.textContent).toContain("模块暂时不可用");

    const retry = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("重试"));
    expect(retry).toBeTruthy();
    act(() => {
      root.render(<ErrorBoundary name="测试面板"><Bomb exploding={false} /></ErrorBoundary>);
      retry!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.textContent).toContain("正常内容");
    expect(container.textContent).not.toContain("模块暂时不可用");
  });
});
