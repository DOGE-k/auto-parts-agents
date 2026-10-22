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

  it("非安全上下文没有 crypto.randomUUID 时仍能生成幂等键", () => {
    // http://<公网IP> 部署时浏览器不暴露 randomUUID；直接调用会让所有写操作在发请求前就失败。
    const original = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: { getRandomValues: (arr: Uint8Array) => { arr.fill(7); return arr; } },
    });
    try {
      const headers = writeHeaders() as Record<string, string>;
      expect(headers["Idempotency-Key"]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    } finally {
      Object.defineProperty(globalThis, "crypto", { configurable: true, value: original });
    }
  });

  it("连 getRandomValues 都没有时也不会抛错", () => {
    const original = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
    try {
      const headers = writeHeaders() as Record<string, string>;
      expect(headers["Idempotency-Key"]).toMatch(/^idem-/);
    } finally {
      Object.defineProperty(globalThis, "crypto", { configurable: true, value: original });
    }
  });
});
