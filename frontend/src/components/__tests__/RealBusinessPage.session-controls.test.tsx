// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    api: vi.fn(() => Promise.resolve([])),
    getRealIdentity: vi.fn(),
    getQualityTodo: vi.fn(() => Promise.resolve({ items: [] })),
    logoutProjectSession: vi.fn(() => Promise.resolve()),
  };
});

import RealBusinessPage from "../../RealBusinessPage";
import {
  getRealIdentity,
  getRealSessionToken,
  getRealWriteToken,
  getSessionIssuedAt,
  logoutProjectSession,
  setProjectSessionExpiresIn,
  setRealSessionToken,
  setRealWriteToken,
  type RealIdentity,
} from "../../api";

const projectIdentity: RealIdentity = {
  subject: "test-project-user",
  actor_id: "test-project-user",
  display_name: "测试项目用户",
  roles: ["project_operator"],
  authority: "project",
  provider: "project",
  authenticated: true,
};

const connectionIdentity: RealIdentity = {
  subject: "test-integration-account",
  actor_id: "test-integration-account",
  display_name: "测试数据连接账号",
  roles: [],
  authority: "ERPNext",
  provider: "erpnext",
  authenticated: true,
};

const cleanups: Array<() => void> = [];
const projectExpiryKey = "project_session_expires_at";
let fetchMock: ReturnType<typeof vi.fn>;

async function mountConnectionPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const onIdentityChange = vi.fn();
  cleanups.push(() => {
    act(() => root.unmount());
    container.remove();
  });
  await act(async () => {
    root.render(
      <RealBusinessPage
        activeModule="connection"
        onNavigate={() => undefined}
        onIdentityChange={onIdentityChange}
      />,
    );
  });
  return { container, onIdentityChange };
}

function buttonWithText(container: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button"))
    .find((candidate) => candidate.textContent === text);
  expect(button, `应显示“${text}”按钮`).toBeTruthy();
  return button!;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  setRealSessionToken("test-debug-bearer");
  setRealWriteToken("test-debug-write-token");
  setProjectSessionExpiresIn(3600);
  vi.mocked(getRealIdentity).mockResolvedValue(projectIdentity);
  fetchMock = vi.fn(() => Promise.reject(new Error("测试禁止真实网络请求")));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0).reverse()) cleanup();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("项目登录与高级联调配置独立操作", () => {
  it("已登录用户清除联调配置只移除调试令牌，保留项目身份和到期时间", async () => {
    const expiresAt = window.sessionStorage.getItem(projectExpiryKey);
    const { container, onIdentityChange } = await mountConnectionPage();
    expect(onIdentityChange).toHaveBeenLastCalledWith(projectIdentity);
    expect(container.textContent).toContain("当前项目用户");

    container.querySelector<HTMLDetailsElement>(".advanced-settings")!.open = true;
    await act(async () => buttonWithText(container, "清除联调配置").click());

    expect(getRealSessionToken()).toBe("");
    expect(getRealWriteToken()).toBe("");
    expect(getSessionIssuedAt()).toBe(0);
    expect(window.sessionStorage.getItem(projectExpiryKey)).toBe(expiresAt);
    expect(logoutProjectSession).not.toHaveBeenCalled();
    expect(getRealIdentity).toHaveBeenCalledTimes(2);
    expect(onIdentityChange).toHaveBeenLastCalledWith(projectIdentity);
    expect(container.textContent).toContain("当前项目用户");
    expect(buttonWithText(container, "退出登录")).toBeTruthy();
    expect(container.querySelector<HTMLInputElement>("input[placeholder='粘贴短期企业会话令牌']")!.value).toBe("");
    expect(container.querySelector<HTMLInputElement>("input[placeholder='服务端 REAL_WRITE_API_TOKEN']")!.value).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("退出登录调用项目退出接口并移除项目到期时间", async () => {
    vi.mocked(getRealIdentity)
      .mockResolvedValueOnce(projectIdentity)
      .mockResolvedValueOnce(connectionIdentity);
    const { container, onIdentityChange } = await mountConnectionPage();
    expect(window.sessionStorage.getItem(projectExpiryKey)).not.toBeNull();
    expect(onIdentityChange).toHaveBeenLastCalledWith(projectIdentity);

    await act(async () => buttonWithText(container, "退出登录").click());

    expect(logoutProjectSession).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem(projectExpiryKey)).toBeNull();
    expect(getRealIdentity).toHaveBeenCalledTimes(2);
    expect(onIdentityChange).toHaveBeenLastCalledWith(null);
    expect(container.textContent).not.toContain("当前项目用户");
    expect(container.querySelector(".real-session-login")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
