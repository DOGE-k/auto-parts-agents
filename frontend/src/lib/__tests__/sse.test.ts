// SSE 行协议解析器测试（阶段十流式问答）
import { describe, expect, it } from "vitest";
import { SseParser } from "../sse";

describe("SseParser", () => {
  it("解析完整事件并携带事件名与数据", () => {
    const parser = new SseParser();
    const events = parser.feed('event: step\ndata: {"seq":1}\n\nevent: done\ndata: {"answer":"ok"}\n\n');
    expect(events).toEqual([
      { event: "step", data: '{"seq":1}' },
      { event: "done", data: '{"answer":"ok"}' },
    ]);
  });

  it("跨 chunk 的半包事件留到下次 feed 再产出（增量解析）", () => {
    const parser = new SseParser();
    expect(parser.feed('event: step\ndata: {"seq":')).toEqual([]);
    expect(parser.feed('2}\n')).toEqual([]);
    const events = parser.feed("\n\nevent: done\ndata: {}\n\n");
    expect(events).toEqual([
      { event: "step", data: '{"seq":2}' },
      { event: "done", data: "{}" },
    ]);
  });

  it("忽略心跳注释行与空行", () => {
    const parser = new SseParser();
    const events = parser.feed(': ping\n\n: keep-alive\nevent: step\ndata: 1\n\n');
    expect(events).toEqual([{ event: "step", data: "1" }]);
  });

  it("兼容 CRLF 分隔与缺省事件名（message）", () => {
    const parser = new SseParser();
    const events = parser.feed("data: hello\r\n\r\n");
    expect(events).toEqual([{ event: "message", data: "hello" }]);
  });

  it("多行 data 以换行连接", () => {
    const parser = new SseParser();
    const events = parser.feed("event: done\ndata: line1\ndata: line2\n\n");
    expect(events).toEqual([{ event: "done", data: "line1\nline2" }]);
  });
});
