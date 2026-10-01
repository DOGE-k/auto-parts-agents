// CollaborationEventsPanel 回归测试（P1 协同事件面板）
// 背景：2026-10-01 页面走查实测——列表接口缺 result 字段时"查看协同结论"
// 展开为空（按钮状态翻转但无内容）。修复后列表摘要恒携带 result；本测试锁定。
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getCollaborationEventsMock = vi.fn();
const retryCollaborationEventMock = vi.fn();
const takeoverCollaborationEventMock = vi.fn();

vi.mock("../../api", () => ({
  collaborationEventStatusLabels: {
    PENDING: "待处理",
    PROCESSING: "协同中",
    COMPLETED: "已完成",
    FAILED: "失败",
    MANUAL_HANDLED: "已人工接管",
  },
  getCollaborationEvents: (...args: unknown[]) => getCollaborationEventsMock(...args),
  retryCollaborationEvent: (...args: unknown[]) => retryCollaborationEventMock(...args),
  takeoverCollaborationEvent: (...args: unknown[]) => takeoverCollaborationEventMock(...args),
}));

import CollaborationEventsPanel from "../CollaborationEventsPanel";

const completedEvent = {
  event_id: "EVT-TEST0000001",
  event_type: "quality_issue_raised",
  dedup_key: "quality_issue_raised:11:5",
  status: "COMPLETED",
  payload: { work_order_id: "11", issue_id: "5", title: "TEST 演示", source: "issue_registration" },
  failure_count: 0,
  max_retries: 3,
  taken_over_by: "",
  taken_over_at: null,
  created_at: "2026-10-01T10:00:00Z",
  processed_at: "2026-10-01T10:00:05Z",
  error: null,
  has_result: true,
  result: {
    conclusions: ["质量维度：1 项未关闭质量问题，质量门禁未通过", "生产维度：完成率 100%（400/400）"],
    quality_impact: { open_issues_count: 1, quality_gate_passed: false, missing_documents: [] },
    tracking: { completion_rate: 100, due_date: "2026-11-20", eta_status: "COMPLETED" },
    procurement_risk: { plan_id: "PROC-1", has_shortage: false, shortage_count: 0, supplier_options: [] },
    data_gaps: [{ dimension: "quality", detail: "检验记录 0 条" }],
    authority: "ERPNext + OpenMES（只读协同，未写入）",
  },
};

function mountPanel(): { container: HTMLElement; cleanup: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  act(() => {
    root.render(
      <CollaborationEventsPanel notify={() => undefined} onError={() => undefined} />,
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

async function flush(times = 6): Promise<void> {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

describe("CollaborationEventsPanel（P1 回归）", () => {
  const cleanups: Array<() => void> = [];

  afterEach(() => {
    for (const fn of cleanups.splice(0).reverse()) fn();
    getCollaborationEventsMock.mockReset();
    retryCollaborationEventMock.mockReset();
    takeoverCollaborationEventMock.mockReset();
  });

  it("渲染事件列表（编号/状态徽标/元信息），空列表显示提示", async () => {
    getCollaborationEventsMock.mockResolvedValue({ items: [completedEvent], count: 1 });
    const panel = mountPanel();
    cleanups.push(panel.cleanup);
    await flush();
    expect(panel.container.textContent).toContain("EVT-TEST0000001");
    expect(panel.container.textContent).toContain("已完成");
    expect(panel.container.textContent).toContain("TEST 演示");

    // 先改 mock 再挂载：面板挂载时立即触发首次加载
    getCollaborationEventsMock.mockResolvedValue({ items: [], count: 0 });
    const empty = mountPanel();
    cleanups.push(empty.cleanup);
    await flush();
    expect(empty.container.textContent).toContain("当前没有协同事件");
  });

  it("展开显示三维度结论与数据缺口（列表摘要含 result）", async () => {
    getCollaborationEventsMock.mockResolvedValue({ items: [completedEvent], count: 1 });
    const panel = mountPanel();
    cleanups.push(panel.cleanup);
    await flush();
    const btn = Array.from(panel.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("查看协同结论"),
    );
    expect(btn).toBeTruthy();
    act(() => {
      btn!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(panel.container.textContent).toContain("质量维度：1 项未关闭质量问题");
    expect(panel.container.textContent).toContain("生产维度：完成率 100%");
    expect(panel.container.textContent).toContain("检验记录 0 条");
    expect(panel.container.textContent).toContain("只读协同，未写入");
  });

  it("FAILED 事件显示失败原因与重试按钮，重试调用 API", async () => {
    const failed = {
      ...completedEvent,
      event_id: "EVT-TEST0000002",
      status: "FAILED",
      failure_count: 1,
      max_retries: 3,
      result: null,
      error: { message: "MES 不可达" },
    };
    getCollaborationEventsMock.mockResolvedValue({ items: [failed], count: 1 });
    retryCollaborationEventMock.mockResolvedValue({ ...failed, status: "COMPLETED" });
    const panel = mountPanel();
    cleanups.push(panel.cleanup);
    await flush();
    expect(panel.container.textContent).toContain("失败原因：MES 不可达");
    expect(panel.container.textContent).toContain("失败 1/3 次");
    const retryBtn = Array.from(panel.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("重试协同"),
    );
    expect(retryBtn).toBeTruthy();
    act(() => {
      retryBtn!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(retryCollaborationEventMock).toHaveBeenCalledWith("EVT-TEST0000002");
    // 重试后重新加载列表
    expect(getCollaborationEventsMock).toHaveBeenCalledTimes(2);
  });

  it("人工接管调用 API 并刷新列表", async () => {
    getCollaborationEventsMock.mockResolvedValue({ items: [completedEvent], count: 1 });
    takeoverCollaborationEventMock.mockResolvedValue({
      ...completedEvent,
      status: "MANUAL_HANDLED",
      taken_over_by: "Administrator",
    });
    const panel = mountPanel();
    cleanups.push(panel.cleanup);
    await flush();
    const takeBtn = Array.from(panel.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("人工接管"),
    );
    expect(takeBtn).toBeTruthy();
    act(() => {
      takeBtn!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(takeoverCollaborationEventMock).toHaveBeenCalledWith("EVT-TEST0000001");
  });
});
