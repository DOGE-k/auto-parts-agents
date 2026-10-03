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
const getMessagesMock = vi.fn();
const getSessionsMock = vi.fn();

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    askAssistant: (...args: unknown[]) => askAssistantMock(...args),
    // 面板流式优先：这里模拟传输层失败，走真实回退路径到 askAssistant
    askAssistantStream: vi.fn(() => Promise.reject(new Error("transport failure"))),
    getAssistantSessionMessages: (...args: unknown[]) => getMessagesMock(...args),
    getAssistantSessions: (...args: unknown[]) => getSessionsMock(...args),
  };
});

import AssistantPanel from "../AssistantPanel";

function mountPanel(
  props: Partial<{ currentWorkOrderId: string; currentErpDraftId: string }> = {},
  opts: { presetSessionId?: string } = {},
): { container: HTMLElement; root: Root; container_removed: () => void } {
  sessionStorage.clear();
  if (opts.presetSessionId) sessionStorage.setItem("assistant_session_id", opts.presetSessionId);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <AssistantPanel
        identity={null}
        currentErpDraftId={props.currentErpDraftId ?? ""}
        currentWorkOrderId={props.currentWorkOrderId ?? ""}
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
    getMessagesMock.mockReset();
    sessionStorage.clear();
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

// 2026-10-02 会话体验修复回归（交接文档 §3/§4）：连续聊天保留、切页不丢（组件
// 保持挂载由 RealBusinessPage 负责，这里验证多轮状态）、新会话切断页面上下文、
// 刷新后按 sessionStorage 恢复历史。
describe("AssistantPanel 连续聊天与页面上下文（交接文档回归）", () => {
  const cleanups: Array<() => void> = [];

  afterEach(() => {
    for (const fn of cleanups.splice(0).reverse()) fn();
    askAssistantMock.mockReset();
    getMessagesMock.mockReset();
    getSessionsMock.mockReset();
    sessionStorage.clear();
  });

  function answer(text: string, sessionId: string) {
    return {
      question: "q",
      answer: text,
      call_chain: [],
      rounds: 0,
      tool_count: 0,
      coordination_run_id: "",
      authority: "ERPNext + OpenMES",
      context: {},
      session_id: sessionId,
    };
  }

  it("连续两问：第一轮问答保留在消息列表中，输入框可继续提问", async () => {
    const panel = mountPanel();
    cleanups.push(panel.container_removed);
    askAssistantMock
      .mockResolvedValueOnce(answer("第一轮回答：工单 9 进度 0%。", "ASST-ROUNDTEST01"))
      .mockResolvedValueOnce(answer("第二轮回答：质量门禁通过。", "ASST-ROUNDTEST01"));
    await typeAndSubmit(panel, "工单 9 进度怎么样？");
    await flush();
    await typeAndSubmit(panel, "那质量门禁呢？");
    await flush();
    const text = panel.container.textContent ?? "";
    expect(text).toContain("第一轮回答：工单 9 进度 0%。");
    expect(text).toContain("第二轮回答：质量门禁通过。");
    expect(text).toContain("工单 9 进度怎么样？");
    expect(text).toContain("那质量门禁呢？");
    // 两轮提问后输入框已清空，可继续输入
    const textarea = panel.container.querySelector("textarea");
    expect(textarea?.value).toBe("");
  });

  it("正常提问把页面当前工单注入上下文", async () => {
    const panel = mountPanel({ currentWorkOrderId: "9" });
    cleanups.push(panel.container_removed);
    askAssistantMock.mockResolvedValue(answer("ok", "ASST-CTXTEST0001"));
    await typeAndSubmit(panel, "为什么还不能发运？");
    await flush();
    const call = askAssistantMock.mock.calls[0];
    expect(call[1]).toMatchObject({ 当前流程MES工单id: "9" });
  });

  it("新会话后首轮不再注入页面当前工单，也不回显旧对象（交接文档 §4 验收）", async () => {
    const panel = mountPanel({ currentWorkOrderId: "9" });
    cleanups.push(panel.container_removed);
    askAssistantMock.mockResolvedValue(answer("请提供工单或质量问题编号。", "ASST-NEWSESS0001"));
    // 点击"新会话"
    const newSessionBtn = Array.from(panel.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("新会话"),
    );
    expect(newSessionBtn).toBeTruthy();
    act(() => {
      newSessionBtn!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    // 新会话首轮提问（不带编号）
    await typeAndSubmit(panel, "质量不过关怎么办？");
    await flush();
    // 页面上下文未被注入：后端收到空 context，不会命中工单 9
    const call = askAssistantMock.mock.calls[0];
    expect(call[1]).toEqual({});
    expect(panel.container.textContent).toContain("本轮未自动带入页面当前工单/订单");
    // 新会话首问结束后恢复正常策略：第二轮重新注入页面上下文
    askAssistantMock.mockResolvedValueOnce(answer("ok2", "ASST-NEWSESS0001"));
    await typeAndSubmit(panel, "那工单 9 呢？");
    await flush();
    const secondCall = askAssistantMock.mock.calls[1];
    expect(secondCall[1]).toMatchObject({ 当前流程MES工单id: "9" });
  });

  it("刷新后按 sessionStorage 的会话号恢复历史消息（交接文档 §3.4）", async () => {
    getMessagesMock.mockResolvedValue([
      { id: 1, role: "user", content: "刷新前的问题", created_at: "2026-10-02T00:00:00Z" },
      { id: 2, role: "assistant", content: "刷新前的回答", created_at: "2026-10-02T00:00:01Z" },
    ]);
    const panel = mountPanel({}, { presetSessionId: "ASST-RESTORED001" });
    cleanups.push(panel.container_removed);
    await flush();
    expect(getMessagesMock).toHaveBeenCalledWith("ASST-RESTORED001");
    const text = panel.container.textContent ?? "";
    expect(text).toContain("刷新前的问题");
    expect(text).toContain("刷新前的回答");
  });

  it("历史会话：展开列表、点选旧会话即恢复其消息（2026-10-02 新增）", async () => {
    const panel = mountPanel();
    cleanups.push(panel.container_removed);
    getSessionsMock.mockResolvedValue([
      { session_id: "ASST-OLDSESSION1", title: "旧会话标题", created_at: "2026-10-01T00:00:00Z", last_active_at: "2026-10-01T12:00:00Z" },
    ]);
    getMessagesMock.mockResolvedValue([
      { id: 1, role: "user", content: "旧会话里的问题", created_at: "2026-10-01T00:00:00Z" },
      { id: 2, role: "assistant", content: "旧会话里的回答", created_at: "2026-10-01T00:00:01Z" },
    ]);
    // 展开历史会话：点击 summary 触发按需加载（不依赖 details 的 open 默认行为）
    const sessionsSummary = panel.container.querySelector("details.assistant-sessions summary");
    expect(sessionsSummary).toBeTruthy();
    act(() => {
      sessionsSummary!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    // 点选列表中的旧会话
    const row = Array.from(panel.container.querySelectorAll("button.assistant-session-row"))
      .find((b) => b.textContent?.includes("旧会话标题"));
    expect(row).toBeTruthy();
    act(() => {
      row!.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(getMessagesMock).toHaveBeenCalledWith("ASST-OLDSESSION1");
    const text = panel.container.textContent ?? "";
    expect(text).toContain("旧会话里的问题");
    expect(text).toContain("旧会话里的回答");
  });
});
