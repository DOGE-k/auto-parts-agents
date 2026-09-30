// @vitest-environment jsdom
import { describe, expect, it, beforeEach } from "vitest";
import {
  getRealSessionToken,
  setRealSessionToken,
  getRealWriteToken,
  setRealWriteToken,
  getSessionIssuedAt,
  writeHeaders,
} from "../../api";

describe("会话令牌注入（阶段十关键行为）", () => {
  beforeEach(() => {
    setRealSessionToken("");
    setRealWriteToken("");
  });

  it("sessionStorage 只在浏览器会话内保存短期令牌", () => {
    setRealSessionToken("tok-1");
    expect(getRealSessionToken()).toBe("tok-1");
    expect(getSessionIssuedAt()).toBeGreaterThan(0);
    setRealSessionToken("");
    expect(getRealSessionToken()).toBe("");
    expect(getSessionIssuedAt()).toBe(0);
  });

  it("空白令牌写入等同于清除", () => {
    setRealSessionToken("tok-1");
    setRealSessionToken("   ");
    expect(getRealSessionToken()).toBe("");
  });

  it("writeHeaders 携带写入令牌且不影响幂等键", () => {
    setRealWriteToken("w-token");
    const h1 = writeHeaders() as Record<string, string>;
    expect(h1["X-Real-Write-Token"]).toBe("w-token");
    expect(h1["Idempotency-Key"]).toBeTruthy();
    setRealWriteToken("");
    const h2 = writeHeaders() as Record<string, string>;
    expect(h2["X-Real-Write-Token"]).toBeUndefined();
  });
});
