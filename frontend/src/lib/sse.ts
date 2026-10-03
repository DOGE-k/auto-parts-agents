// SSE 行协议增量解析器（阶段十：协调问答流式输出；纯函数逻辑，配 vitest 锁定行为）
// 规则：事件以空行分隔；"event: X" 设事件名（缺省 message）；"data: Y" 累加负载（多行以 \n 连接）；": ..." 注释行（心跳）忽略。

export type SseEvent = { event: string; data: string };

export class SseParser {
  private buffer = "";

  /** 喂入一段文本，返回其中完整的事件（不完整的事件留到下次 feed）。 */
  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    // 事件块以空行（\n\n）结束；\r\n 兼容浏览器/代理行为
    let sep = Math.min(...["\n\n", "\r\n\r\n"].map((s) => this.buffer.indexOf(s)).filter((i) => i >= 0));
    while (Number.isFinite(sep) && sep >= 0) {
      const block = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + (this.buffer.startsWith("\r\n", sep) ? 4 : 2));
      const parsed = this.parseBlock(block);
      if (parsed) events.push(parsed);
      const candidates = ["\n\n", "\r\n\r\n"]
        .map((s) => this.buffer.indexOf(s))
        .filter((i) => i >= 0);
      sep = candidates.length ? Math.min(...candidates) : -1;
    }
    return events;
  }

  private parseBlock(block: string): SseEvent | null {
    let event = "";
    const dataLines: string[] = [];
    for (const rawLine of block.split("\n")) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (!line || line.startsWith(":")) continue; // 空行/心跳注释
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
      // 其他字段（id/retry）本通道未使用，忽略
    }
    if (!event && dataLines.length === 0) return null;
    return { event: event || "message", data: dataLines.join("\n") };
  }
}
