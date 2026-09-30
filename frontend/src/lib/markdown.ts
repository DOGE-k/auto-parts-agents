// 协调者回答的轻量 Markdown 渲染：先转义 HTML，再恢复标题/加粗/表格结构。
// 内容来源是本系统协调者与真实工具结果，无用户富文本输入面。
// 轻量 Markdown 渲染（协调者回答）：先转义 HTML，再恢复标题/加粗/表格结构。
// 内容来源是本系统协调者与真实工具结果，无用户富文本输入面。
export function formatAssistantAnswer(text: string): string {
  const esc = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = esc.split("\n");
  const out: string[] = [];
  let tableRows: string[][] = [];
  const bold = (s: string) => s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  const flushTable = () => {
    if (tableRows.length === 0) return;
    // 第二行是分隔行（---），跳过
    const body = tableRows.filter((cells, i) => !(i === 1 && cells.every((c) => /^:?-{2,}:?$/.test(c.trim()))));
    const rows = body.map((cells) => `<tr>${cells.map((c) => `<td>${bold(c.trim())}</td>`).join("")}</tr>`).join("");
    out.push(`<table class="md-table">${rows}</table>`);
    tableRows = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      tableRows.push(trimmed.slice(1, -1).split("|"));
      continue;
    }
    flushTable();
    if (/^#{1,4}\s/.test(trimmed)) {
      out.push(`<div class="md-heading">${bold(trimmed.replace(/^#{1,4}\s/, ""))}</div>`);
    } else if (trimmed === "") {
      out.push('<div class="md-gap"></div>');
    } else {
      out.push(`<div class="md-line">${bold(trimmed)}</div>`);
    }
  }
  flushTable();
  return out.join("");
}
