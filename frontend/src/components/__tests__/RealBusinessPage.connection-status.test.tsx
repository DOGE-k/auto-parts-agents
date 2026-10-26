// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api", async (original) => ({
  ...await original<typeof import("../../api")>(),
  api: vi.fn(),
  getRealIdentity: vi.fn(),
  getQualityTodo: vi.fn(() => Promise.resolve({ items: [] })),
}));
vi.mock("../AssistantPanel", () => ({ default: () => null }));

import RealBusinessPage from "../../RealBusinessPage";
import { api, getRealIdentity } from "../../api";

const projectIdentity = {
  subject: "operator", actor_id: "operator", display_name: "操作员",
  provider: "project", authority: "project", roles: [], authenticated: true,
};
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api).mockImplementation(() => Promise.resolve([]));
  vi.mocked(getRealIdentity).mockResolvedValue(projectIdentity);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function mount() {
  const onConnectionStatus = vi.fn();
  await act(async () => {
    root.render(<RealBusinessPage activeModule="connection" onNavigate={() => undefined} onConnectionStatus={onConnectionStatus} />);
  });
  return onConnectionStatus;
}

describe("项目身份与业务系统连接状态独立", () => {
  it("项目登录成功时 ERP 仍等待真实读取，读取失败后报告异常", async () => {
    let rejectCustomers!: (reason: Error) => void;
    const customers = new Promise<never>((_resolve, reject) => { rejectCustomers = reject; });
    vi.mocked(api).mockImplementation((path) => path.startsWith("/real-orders/erp/customers") ? customers : Promise.resolve([]));
    const report = await mount();
    expect(container.textContent).toContain("operator");
    expect(report).toHaveBeenLastCalledWith({ erpnext: "pending", openmes: "pending" });

    await act(async () => { rejectCustomers(new Error("ERP 读取失败")); });
    expect(report).toHaveBeenLastCalledWith({ erpnext: "error", openmes: "connected" });
    expect(container.querySelector(".error-banner")?.textContent).toContain("ERP 读取失败");
  });

  it("项目身份解析失败不会覆盖真实 ERP 客户及物料读取成功的状态", async () => {
    vi.mocked(getRealIdentity).mockRejectedValueOnce(new Error("项目会话已失效"));
    const report = await mount();
    expect(report).toHaveBeenLastCalledWith({ erpnext: "connected", openmes: "connected" });
    expect(container.textContent).toContain("项目登录会话已失效");
  });
});
