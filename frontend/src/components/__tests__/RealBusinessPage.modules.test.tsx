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
    // 评审意见②：可见步骤条只含本阶段 3 步，完整 8 步链收进折叠
    expect(page.container.querySelector(".full-chain-details")).toBeTruthy();
    const directStepper = page.container.querySelector(".page-content > .stepper");
    expect(directStepper).toBeTruthy();
    expect(directStepper!.querySelectorAll(".step-item").length).toBe(3);
    const chainStepper = page.container.querySelector(".full-chain-details .stepper");
    expect(chainStepper!.querySelectorAll(".step-item").length).toBe(8);
    // 页签时代的"运行记录"与"质量中心"面板不得同时展开（验收 7）
    expect(text).not.toContain("协同事件（自动协作）");
    expect(text).not.toContain("Agent 运行记录");
  });

  it("页头项目身份区分项目用户与数据连接账号（交接文档 §1）", async () => {
    const page = mountPage("sales");
    cleanups.push(page.cleanup);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const text = textOf(page.container);
    // ERPNext 服务端集成账号不能显示为审批身份；必须明确标注"未登录"
    expect(text).toContain("项目账号未登录");
    expect(text).toContain("ERPNext 数据连接账号：Administrator");
    expect(text).toContain("服务端集成账号");
    expect(text).not.toContain("当前审批人：Administrator");
    // 身份详情折叠存在（无 OpenMES 会话时展示只读说明与数据连接账号）
    const roles = page.container.querySelector(".identity-roles");
    expect(roles).toBeTruthy();
    // 会话设置不占页头（验收 8）：Bearer 输入框不存在
    expect(page.container.querySelectorAll("input[placeholder*='Bearer'], input[placeholder*='令牌']").length).toBe(0);
  });

  it("无报价对象时销售页显示报价表单，不显示'已完成'空壳（交接文档 §8.2）", () => {
    const page = mountPage("sales");
    cleanups.push(page.cleanup);
    const text = textOf(page.container);
    expect(text).toContain("销售与订单");
    expect(text).not.toContain("报价与订单草稿已完成");
  });

  it("采购与缺料模块：无报价/方案对象时显示空态卡与待审批入口（交接文档 §8.3）", () => {
    const page = mountPage("procurement");
    cleanups.push(page.cleanup);
    const text = textOf(page.container);
    expect(text).toContain("采购与缺料");
    expect(text).toContain("尚未选择报价或采购方案");
    expect(text).toContain("去销售与订单");
    expect(text).toContain("去审批与审计");
    expect(text).not.toContain("采购链路已完成");
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
    expect(connText).toContain("项目登录");
    // 高级联调设置默认折叠，Bearer/写入令牌不在展开文案里直接铺开（验收 11）
    expect(connText).toContain("高级联调设置");
  });
});
