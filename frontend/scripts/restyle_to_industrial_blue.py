# -*- coding: utf-8 -*-
"""一次性风格重映射：teal 工业风 → 参考项目 ROS·MES 的亮色工业蓝。

分三段：
1. :root 设计令牌整块替换
2. 全文硬编码色板映射（teal 家族 → 蓝/海军蓝/slate 家族）
3. 关键组件精修（侧栏激活态、品牌标、表格 hover、数字等宽）
"""

from pathlib import Path
import re
import sys

CSS = Path(__file__).resolve().parents[1] / "src" / "styles.css"
text = CSS.read_text(encoding="utf-8")
orig = text

# ── 1. 设计令牌 ──────────────────────────────────────────────
new_root = """:root {
  font-family: 'Inter', 'HarmonyOS Sans SC', 'MiSans', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif;
  color: #0f172a;
  background: #f1f5f9;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  font-weight: 400;
  font-size: 14px;
  line-height: 1.55;

  /* Ink scale（Slate 灰阶） */
  --ink: #0f172a;
  --ink-2: #334155;
  --muted: #64748b;
  --faint: #94a3b8;
  --line: #e2e8f0;
  --line-soft: #eef2f7;

  /* 工业科技蓝 */
  --teal: #2563eb;
  --teal-dark: #1d4ed8;
  --teal-light: #ecf2fe;
  --teal-glow: rgba(37, 99, 235, 0.14);

  /* Surfaces */
  --surface: #ffffff;
  --surface-2: #f8fafc;
  --canvas: #f1f5f9;
  --sidebar-bg: #0b1626;
  --sidebar-elev: #12233c;

  /* Mono font */
  --mono: 'JetBrains Mono', 'DM Mono', monospace;

  /* Shadows（Slate 柔和分层） */
  --shadow-xs: 0 1px 2px rgba(15, 23, 42, 0.04);
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 2px 4px rgba(15, 23, 42, 0.06), 0 10px 28px rgba(15, 23, 42, 0.10);
  --shadow-lg: 0 8px 24px rgba(15, 23, 42, 0.10), 0 20px 48px rgba(15, 23, 42, 0.08);
  --shadow-xl: 0 16px 40px rgba(15, 23, 42, 0.14), 0 32px 72px rgba(15, 23, 42, 0.08);

  /* Radius */
  --r-sm: 8px;
  --r-md: 10px;
  --r-lg: 14px;
  --r-xl: 18px;

  /* Transitions */
  --ease: cubic-bezier(0.4, 0, 0.2, 1);
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
}"""

text = re.sub(r":root \{.*?\}", new_root, text, count=1, flags=re.S)

# ── 2. 硬编码色板映射（按出现语义分组，长串先替换避免子串误伤） ──
mapping = {
    # 侧栏 / 深色面（teal-navy → 海军蓝）
    "#0d1f23": "#0b1626", "#0a1a1e": "#12233c", "#142b30": "#12233c",
    "#132e33": "#16294a", "#0e2a2e": "#12233c",
    # 品牌标
    "#35d6c0": "#3b82f6", "#0f9d8f": "#2563eb", "#0a6f66": "#1d4ed8",
    "#04332e": "#ffffff",
    # 侧栏文字 / 导航
    "#b8ccc9": "#94a7c4", "#9db4b1": "#94a7c4", "#6e8c8a": "#7c93b8",
    "#5e7e7a": "#6b83a8", "#8db4ae": "#a5c4f0", "#d9ecea": "#dbe7ff",
    "#f0f7f5": "#f8fafc", "#c5d0cf": "#cbd5e1", "#6b807d": "#64748b",
    "#b8d4cf": "#c7d7f0",
    # teal 强调 → 蓝
    "#0a7d74": "#2563eb", "#05665f": "#1d4ed8", "#e4f3f0": "#ecf2fe",
    "#2eb8a8": "#3b82f6", "#6ee0cc": "#93c5fd", "#57c3b1": "#60a5fa",
    "#d0ebe8": "#dbe7fe", "#eef7f4": "#f8fafc", "#edf7f4": "#eef4ff",
    "#f1f7f7": "#f8fafc", "#f7fcfb": "#f8fafc", "#f4f7f8": "#f8fafc",
    "#eef2f4": "#f1f5f9", "#e9f7f3": "#eef4ff",
    # 深色按钮（teal-dark → navy）
    "#1d3b3d": "#12233c", "#112e30": "#0b1626", "#264850": "#1a2f52",
    "#1a3c3e": "#12233c", "#47615f": "#475569", "#d8e3e1": "#dbe3ef",
    # 绿色成功态 → 标准绿
    "#2d8666": "#16a34a", "#4d9468": "#16a34a", "#3d8c5e": "#15803d",
    "#4ea898": "#16a34a", "#e9f6ef": "#dcfce7", "#e6f4ec": "#dcfce7",
    "#e9f6ed": "#dcfce7", "#e8f5ec": "#dcfce7", "#d4e8e0": "#bbf7d0",
    # 阴影基色
    "rgba(16, 42, 46": "rgba(15, 23, 42",
    "rgba(5, 102, 95": "rgba(29, 78, 216",
    "rgba(20, 190, 168": "rgba(37, 99, 235",
    "rgba(87, 195, 177": "rgba(37, 99, 235",
}
for old, new in mapping.items():
    text = text.replace(old, new)

# ── 3. 组件精修 ──────────────────────────────────────────────
# 3.1 侧栏激活态：参考项目的蓝色渐变药丸 + 光晕
old_active = """.nav-item.active {
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.12) 0%, rgba(37, 99, 235, 0.03) 100%);
  color: #fff;
  font-weight: 600;
}"""
new_active = """.nav-item.active {
  background: linear-gradient(90deg, #2563eb, #1d4ed8);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);
}"""
assert old_active in text, "nav active block changed"
text = text.replace(old_active, new_active, 1)

old_bar = """.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, #93c5fd, #3b82f6);
  box-shadow: 0 0 8px rgba(110, 224, 204, 0.5);
}"""
new_bar = """.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: rgba(255, 255, 255, 0.85);
  box-shadow: 0 0 8px rgba(255, 255, 255, 0.45);
}"""
assert old_bar in text, "nav bar block changed"
text = text.replace(old_bar, new_bar, 1)

# 3.2 表格：行 hover 用品牌蓝浅底（与表头区分）
text = text.replace(
    "tbody tr:hover { background: var(--surface-2); }",
    "tbody tr:hover { background: #f1f5fd; }", 1)
text = text.replace(
    "table { border-collapse: collapse; width: 100%; min-width: 760px; }",
    "table { border-collapse: collapse; width: 100%; min-width: 760px; "
    "font-variant-numeric: tabular-nums; }", 1)

# 3.3 面板：更柔和的边与阴影、更大圆角
text = text.replace(
    """.panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: 20px 22px;
  min-width: 0;
  box-shadow: var(--shadow-xs);
}""",
    """.panel {
  background: var(--surface);
  border: 1px solid var(--line-soft);
  border-radius: var(--r-lg);
  padding: 20px 22px;
  min-width: 0;
  box-shadow: var(--shadow-sm);
}""", 1)

# 3.4 数字等宽（统计/金额）
text = text.replace(
    "button, input, select { font: inherit; }",
    "button, input, select { font: inherit; }\ntable, .stat-value, .card-value { font-variant-numeric: tabular-nums; }",
    1)

CSS.write_text(text, encoding="utf-8")
print(f"done: {len(orig)} -> {len(text)} chars")
