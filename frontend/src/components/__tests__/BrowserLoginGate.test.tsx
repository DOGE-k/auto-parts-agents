// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("../../api", async (original) => ({
  ...await original<typeof import("../../api")>(),
  api: vi.fn(),
  loginRealSession: vi.fn(),
}));
import { api, loginRealSession } from "../../api";
import BrowserLoginGate, { PROJECT_AUTH_REQUIRED_EVENT } from "../BrowserLoginGate";

const account = { subject: "operator", actor_id: "operator", display_name: "操作员", provider: "project", authority: "Project", roles: ["quality_manager"], authenticated: true };
let container: HTMLElement;
let root: Root;
beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });
async function mount() {
  await act(async () => { root.render(<BrowserLoginGate><div data-testid="business">业务内容</div></BrowserLoginGate>); });
}
function setInput(label: string, value: string) {
  const input = Array.from(container.querySelectorAll("label")).find((v) => v.textContent?.startsWith(label))!.querySelector("input")!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("浏览器项目登录门禁", () => {
  it("未登录不挂载业务组件，提供可直接使用的登录表单", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("请先登录"));
    await mount();
    expect(container.querySelector('[data-testid="business"]')).toBeNull();
    expect(container.querySelector('input[autocomplete="username"]')).toBeTruthy();
    expect(container.querySelector('input[type="password"]')).toBeTruthy();
    expect(container.querySelector('button[type="submit"]')).toBeTruthy();
  });
  it("服务端确认有效项目会话后才显示业务", async () => {
    vi.mocked(api).mockResolvedValueOnce({ identity: account, expires_in: 600 });
    await mount();
    expect(container.querySelector('[data-testid="business"]')).toBeTruthy();
    expect(Number(window.sessionStorage.getItem("project_session_expires_at"))).toBeGreaterThan(Date.now());
  });
  it("登录成功后显示业务并清除密码输入框", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("请先登录"));
    vi.mocked(loginRealSession).mockResolvedValueOnce({ identity: account, expires_in: 600 });
    await mount();
    window.sessionStorage.setItem("assistant_session_id", "stale-previous-account-chat");
    await act(async () => { setInput("用户名", "operator"); setInput("密码", "test-only-password"); });
    await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(loginRealSession).toHaveBeenCalledWith("operator", "test-only-password");
    expect(window.sessionStorage.getItem("assistant_session_id")).toBeNull();
    expect(container.querySelector('[data-testid="business"]')).toBeTruthy();
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });
  it("会话失效或退出后卸载业务并清除前一账号会话选择", async () => {
    vi.mocked(api).mockResolvedValueOnce({ identity: account, expires_in: 600 });
    await mount();
    window.sessionStorage.setItem("assistant_session_id", "previous-user-chat");
    await act(async () => { window.dispatchEvent(new Event(PROJECT_AUTH_REQUIRED_EVENT)); });
    expect(container.querySelector('[data-testid="business"]')).toBeNull();
    expect(container.querySelector("form")).toBeTruthy();
    expect(window.sessionStorage.getItem("assistant_session_id")).toBeNull();
  });
});
