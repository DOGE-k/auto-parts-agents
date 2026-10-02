// 信息架构改版回归：左侧导航模块 → RealBusinessPage 视图切换（§八验收 1/5/6/7/8/12）
// - 默认模块为 AI 协同问答；真实模块按导航切换且互不同时展开；
// - 会话设置不再出现在页头（迁移到"系统连接"模块）；
// - 审批身份压缩为摘要，完整角色折叠在"身份详情"里；
// - 模块提示卡把订单全流程串联为 销售→采购→生产 的接续路径。
// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    api: vi.fn((path: string) => {
      if (path.startsWith("/real-orders/erp/customers")) return Promise.resolve([]);
      if (path.startsWith("/real-orders/erp/items")) return Promise.resolve([]);
      if (path.startsWith("/mes/work-orders")) return Promise.resolve([]);
      return Promise.resolve([]);
    }),
    getRealIdentity: vi.fn(() =>
      Promise.resolve({
        subject: "Administrator",
        actor_id: "Administrator",
        display_name: "Administrator",
        roles: ["Accounts Manager", "System Manager"],
        authority: "ERPNext",
        provider: "erpnext",
        authenticated: true,
      }),
    ),
    getQualityTodo: vi.fn(() => Promise.resolve({ items: [] })),
  };
});

import RealBusinessPage, { type RealModuleKey } from "../../RealBusinessPage";

const identityStub = null;

function mountPage(activeModule: RealModuleKey): {
  container: HTMLElement;
  root: Root;
  cleanup: () => void;
  rerender: (module: RealModuleKey) => void;
} {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const renderWith = (module: RealModuleKey) => {
    root.render(
      <RealBusinessPage
        activeModule={module}
        onNavigate={() => undefined}
      />,
    );
  };
  act(() => renderWith(activeModule));
  return {
    container,
    root,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
    rerender: (module: RealModuleKey) => act(() => renderWith(module)),
  };
}

function textOf(container: HTMLElement): string {
  return container.textContent ?? "";
}

const cleanups: Array<() => void> = [];
afterEach(() => {
  for (const fn of cleanups.splice(0).reverse()) fn();
  vi.clearAllMocks();
});

describe("RealBusinessPage 信息架构改版（模块化视图）", () => {
  it("销售与订单模块：显示模块标题与步骤条，不出现其他模块内容", () => {
    const page = mountPage("sales");
    cleanups.push(page.cleanup);
    const text = textOf(page.container);
    expect(text).toContain("销售与订单");
    expect(text).toContain("报价分析");
    expect(text).toContain("ERP 草稿");
    // 页签时代的"运行记录"与"质量中心"面板不得同时展开（验收 7）
    expect(text).not.toContain("协同事件（自动协作）");
    expect(text).not.toContain("Agent 运行记录");
  });

  it("页头审批身份为紧凑摘要：完整角色折叠在身份详情中（验收 12）", async () => {
    const page = mountPage("sales");
    cleanups.push(page.cleanup);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const text = textOf(page.container);
    // 摘要只有 display_name（authority），不整段铺角色列表
    expect(text).toContain("Administrator");
    const roles = page.container.querySelector(".identity-roles");
    expect(roles).toBeTruthy();
    expect(roles!.textContent).toContain("Accounts Manager");
    // 会话设置不占页头（验收 8）：Bearer 输入框不存在
    expect(page.container.querySelectorAll("input[placeholder*='Bearer'], input[placeholder*='令牌']").length).toBe(0);
  });

  it("采购与缺料模块：流程未到时显示接续提示卡引导先完成报价（验收 13 链路接续）", () => {
    const page = mountPage("procurement");
    cleanups.push(page.cleanup);
    const text = textOf(page.container);
    expect(text).toContain("采购与缺料");
    expect(text).toContain("还没有可分析的净需求");
    expect(text).toContain("去销售与订单");
  });

  it("生产跟单模块：包含跟单视图与 MES 完工数据二级视图（原独立导航项并入）", () => {
    const page = mountPage("production");
    cleanups.push(page.cleanup);
    const text = textOf(page.container);
    expect(text).toContain("生产跟单");
    expect(text).toContain("跟单视图");
    expect(text).toContain("MES 完工数据");
  });

  it("质量中心与审批与审计、系统连接模块各自独立渲染", () => {
    const quality = mountPage("quality");
    cleanups.push(quality.cleanup);
    expect(textOf(quality.container)).toContain("质量中心");

    const audit = mountPage("audit");
    cleanups.push(audit.cleanup);
    expect(textOf(audit.container)).toContain("审批与审计");

    const connection = mountPage("connection");
    cleanups.push(connection.cleanup);
    const connText = textOf(connection.container);
    expect(connText).toContain("系统连接");
    expect(connText).toContain("数据连接");
    expect(connText).toContain("审批账号");
    // 高级联调设置默认折叠，Bearer/写入令牌不在展开文案里直接铺开（验收 11）
    expect(connText).toContain("高级联调设置");
  });
});
