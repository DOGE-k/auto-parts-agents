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
  collaborationEventTypeLabels: {
    quality_issue_raised: "质量异常",
    material_shortage: "物料短缺",
    production_overdue: "生产延期",
    production_at_risk: "生产临期",
  },
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

function mountPanel(
  onGoTarget?: (event: import("../../api").CollaborationEvent) => void,
  onErrorSpy?: (message: string) => void,
): { container: HTMLElement; cleanup: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  act(() => {
    root.render(
      <CollaborationEventsPanel
        notify={() => undefined}
        onError={onErrorSpy ?? (() => undefined)}
        onGoTarget={onGoTarget}
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

// ===== 任务 A：去处置/去处理导航（交接文档 3.4 的五条要求） =====

const shortageEvent = {
  ...completedEvent,
  event_id: "EVT-SHORTTEST01",
  event_type: "material_shortage",
  dedup_key: "material_shortage:QUO-S1:sig",
  payload: { plan_id: "PROC-S1", quotation_id: "QUO-S1", source: "procurement_analyze" },
  result: {
    ...completedEvent.result,
    shortage: { quotation_id: "QUO-S1", plan_id: "PROC-S1", recommended_option_id: "OPT-1", shortage_items: [], supplier_options: [] },
  },
};

const overdueEvent = {
  ...completedEvent,
  event_id: "EVT-OVERDUE01",
  event_type: "production_overdue",
  dedup_key: "production_overdue:2:2026-10-01",
  payload: { work_order_id: "2", work_order_no: "WO-2026-001", source: "track_order" },
  result: {
    conclusions: ["生产维度：工单已过交期 3 天仍未完成"],
    overdue_days: 3,
    tracking: { completion_rate: 0, due_date: "2026-09-28" },
    data_gaps: [],
    authority: "OpenMES（只读协同，未写入）",
  },
};

const atRiskEvent = {
  ...completedEvent,
  event_id: "EVT-ATRISK0001",
  event_type: "production_at_risk",
  dedup_key: "production_at_risk:12:2026-10-02",
  payload: {
    work_order_id: "12", work_order_no: "WO-SO-2026-00026", source: "track_order",
    completion_rate: 10.0, due_date: "2026-10-04", days_left: 2, rule_version: "at_risk_v1",
  },
  result: {
    conclusions: ["生产维度：工单距交期还有 2 天，完成率 10.0%（低于预警阈值 50%，规则 at_risk_v1）"],
    days_left: 2,
    rule_version: "at_risk_v1",
    tracking: { completion_rate: 10.0, due_date: "2026-10-04" },
    data_gaps: [],
    authority: "OpenMES（只读协同，未写入）",
  },
};

function findButton(container: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find(
    (b) => b.textContent?.includes(text),
  );
}

describe("CollaborationEventsPanel 去处置/去处理导航（任务 A 回归）", () => {
  const cleanups: Array<() => void> = [];

  afterEach(() => {
    for (const fn of cleanups.splice(0).reverse()) fn();
    getCollaborationEventsMock.mockReset();
  });

  it("三类事件显示正确类型徽标与对应目标按钮文案", async () => {
    getCollaborationEventsMock.mockResolvedValue({
      items: [completedEvent, shortageEvent, overdueEvent],
      count: 3,
    });
    const panel = mountPanel(() => undefined);
    cleanups.push(panel.cleanup);
    await flush();
    expect(panel.container.textContent).toContain("质量异常");
    expect(panel.container.textContent).toContain("物料短缺");
    expect(panel.container.textContent).toContain("生产延期");
    expect(findButton(panel.container, "去处置")).toBeTruthy();
    expect(findButton(panel.container, "去处理方案")).toBeTruthy();
    expect(findButton(panel.container, "查看跟单")).toBeTruthy();
  });

  it("质量事件点击后回调携带工单与问题编号", async () => {
    getCollaborationEventsMock.mockResolvedValue({ items: [completedEvent], count: 1 });
    const onGoTarget = vi.fn();
    const panel = mountPanel(onGoTarget);
    cleanups.push(panel.cleanup);
    await flush();
    act(() => {
      findButton(panel.container, "去处置")!.dispatchEvent(
        new window.MouseEvent("click", { bubbles: true }),
      );
    });
    await flush();
    expect(onGoTarget).toHaveBeenCalledTimes(1);
    const arg = onGoTarget.mock.calls[0][0];
    expect(arg.event_id).toBe("EVT-TEST0000001");
    expect(arg.payload.work_order_id).toBe("11");
    expect(arg.payload.issue_id).toBe("5");
  });

  it("缺料事件回调携带报价/方案编号；延期事件回调携带工单编号", async () => {
    getCollaborationEventsMock.mockResolvedValue({ items: [shortageEvent, overdueEvent], count: 2 });
    const onGoTarget = vi.fn();
    const panel = mountPanel(onGoTarget);
    cleanups.push(panel.cleanup);
    await flush();
    act(() => {
      findButton(panel.container, "去处理方案")!.dispatchEvent(
        new window.MouseEvent("click", { bubbles: true }),
      );
    });
    act(() => {
      findButton(panel.container, "查看跟单")!.dispatchEvent(
        new window.MouseEvent("click", { bubbles: true }),
      );
    });
    await flush();
    expect(onGoTarget).toHaveBeenCalledTimes(2);
    const [shortageArg, overdueArg] = onGoTarget.mock.calls.map((c) => c[0]);
    expect(shortageArg.event_type).toBe("material_shortage");
    expect(shortageArg.result.shortage.quotation_id).toBe("QUO-S1");
    expect(shortageArg.result.shortage.plan_id).toBe("PROC-S1");
    expect(overdueArg.event_type).toBe("production_overdue");
    expect(overdueArg.payload.work_order_id).toBe("2");
  });

  it("关键编号缺失时显示可见错误且不调用导航回调", async () => {
    const noIds = {
      ...completedEvent,
      event_id: "EVT-NOIDS00001",
      payload: { title: "缺编号事件", source: "issue_registration" },
    };
    getCollaborationEventsMock.mockResolvedValue({ items: [noIds], count: 1 });
    const onGoTarget = vi.fn();
    const errors: string[] = [];
    const panel = mountPanel(onGoTarget, (m) => errors.push(m));
    cleanups.push(panel.cleanup);
    await flush();
    act(() => {
      findButton(panel.container, "去处置")!.dispatchEvent(
        new window.MouseEvent("click", { bubbles: true }),
      );
    });
    await flush();
    expect(onGoTarget).not.toHaveBeenCalled();
    expect(errors.some((m) => m.includes("缺少关联的工单编号或质量问题编号"))).toBe(true);
  });

  it("人工接管后仍可见状态与留痕，导航按钮不误显示为已处置", async () => {
    const handled = {
      ...completedEvent,
      event_id: "EVT-HANDLED001",
      status: "MANUAL_HANDLED",
      taken_over_by: "Administrator",
      taken_over_at: "2026-10-01T11:00:00Z",
    };
    getCollaborationEventsMock.mockResolvedValue({ items: [handled], count: 1 });
    const onGoTarget = vi.fn();
    const panel = mountPanel(onGoTarget);
    cleanups.push(panel.cleanup);
    await flush();
    // 接管留痕可见；不出现"已处置"措辞（接管≠处置）
    expect(panel.container.textContent).toContain("已由 Administrator 接管");
    expect(panel.container.textContent).not.toContain("已处置");
    // 导航按钮仍可用（查看不等于处置）
    expect(findButton(panel.container, "去处置")).toBeTruthy();
    act(() => {
      findButton(panel.container, "去处置")!.dispatchEvent(
        new window.MouseEvent("click", { bubbles: true }),
      );
    });
    await flush();
    expect(onGoTarget).toHaveBeenCalledTimes(1);
  });

  it("临期事件显示生产临期徽标与查看跟单按钮，点击回调携带工单编号", async () => {
    getCollaborationEventsMock.mockResolvedValue({ items: [atRiskEvent], count: 1 });
    const onGoTarget = vi.fn();
    const panel = mountPanel(onGoTarget);
    cleanups.push(panel.cleanup);
    await flush();
    expect(panel.container.textContent).toContain("生产临期");
    expect(panel.container.textContent).toContain("WO-SO-2026-00026");
    const button = findButton(panel.container, "查看跟单");
    expect(button).toBeTruthy();
    act(() => {
      button!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(onGoTarget).toHaveBeenCalledTimes(1);
    const arg = onGoTarget.mock.calls[0][0];
    expect(arg.event_type).toBe("production_at_risk");
    expect(arg.payload.work_order_id).toBe("12");
  });

  it("临期事件缺工单编号时显示可见错误且不调用导航回调", async () => {
    const noWo = {
      ...atRiskEvent,
      event_id: "EVT-ATRISKNOID",
      payload: { source: "track_order", rule_version: "at_risk_v1" },
    };
    getCollaborationEventsMock.mockResolvedValue({ items: [noWo], count: 1 });
    const onGoTarget = vi.fn();
    const errors: string[] = [];
    const panel = mountPanel(onGoTarget, (m) => errors.push(m));
    cleanups.push(panel.cleanup);
    await flush();
    const button = findButton(panel.container, "查看跟单");
    expect(button).toBeTruthy();
    act(() => {
      button!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(onGoTarget).not.toHaveBeenCalled();
    expect(errors.some((m) => m.includes("缺少关联的工单编号"))).toBe(true);
  });
});
