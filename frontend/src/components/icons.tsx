// 侧栏导航图标（前端优化清单任务3）：统一 24 viewBox / 1.7 描边 / 圆角线帽的
// 线性风格，替代此前混用的 Unicode 字符（▦◆◈⇄▤⚠⌁⚙），保证笔画粗细一致。
import type { ReactNode } from "react";

const icons: Record<string, ReactNode> = {
  // 业务总览：四宫格
  overview: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </>
  ),
  // AI 协同问答：中心节点连接四岗
  assistant: (
    <>
      <circle cx="12" cy="12" r="2.1" />
      <circle cx="5" cy="5" r="1.8" />
      <circle cx="19" cy="5" r="1.8" />
      <circle cx="5" cy="19" r="1.8" />
      <circle cx="19" cy="19" r="1.8" />
      <path d="M10.6 10.6 6.5 6.5M13.4 10.6l4.1-4.1M10.6 13.4l-4.1 4.1M13.4 13.4l4.1 4.1" />
    </>
  ),
  // 销售与订单：单据
  sales: (
    <>
      <path d="M7 3.5h7L19 8v12.5H7z" />
      <path d="M14 3.5V8h5" />
      <path d="M10 12.5h6M10 16h6" />
    </>
  ),
  // 采购与缺料：双向箭头
  procurement: (
    <>
      <path d="M4 8.5h13M14.5 5 18 8.5 14.5 12" />
      <path d="M20 15.5H7M9.5 12 6 15.5 9.5 19" />
    </>
  ),
  // 生产跟单：带进度点的工序列表
  production: (
    <>
      <path d="M4 7h8" />
      <circle cx="17.5" cy="7" r="1.7" />
      <path d="M4 12h4" />
      <circle cx="13" cy="12" r="1.7" />
      <path d="M4 17h10" />
      <circle cx="19" cy="17" r="1.7" />
    </>
  ),
  // 质量中心：盾牌 + 对勾
  quality: (
    <>
      <path d="M12 3.5 5 6v5.5c0 4.2 2.9 7.4 7 9 4.1-1.6 7-4.8 7-9V6z" />
      <path d="m9 11.5 2.2 2.2 4.3-4.7" />
    </>
  ),
  // 审批与审计：时钟
  audit: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  // 系统连接：双服务节点
  connection: (
    <>
      <rect x="4" y="4.5" width="16" height="6.5" rx="1.5" />
      <rect x="4" y="13" width="16" height="6.5" rx="1.5" />
      <path d="M7.5 7.8h.01M7.5 16.3h.01" />
    </>
  ),
  // —— Mock 模拟组 ——
  scenarios: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="m10 8.8 5 3.2-5 3.2z" />
    </>
  ),
  dashboard: (
    <>
      <path d="M4.5 16a8 8 0 1 1 15 0" />
      <path d="M12 15.5 15.5 10" />
      <path d="M4.5 19.5h15" />
    </>
  ),
  agents: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <rect x="10" y="10" width="4" height="4" />
      <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
    </>
  ),
  plans: (
    <>
      <rect x="4" y="5" width="6.5" height="14" rx="1.2" />
      <rect x="13.5" y="5" width="6.5" height="14" rx="1.2" />
    </>
  ),
  approvals: (
    <>
      <rect x="4.5" y="4.5" width="15" height="15" rx="2" />
      <path d="m8.5 12.2 2.4 2.4 4.8-5" />
    </>
  ),
  "mock-audit": (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </>
  ),
  // 折叠按钮
  "chevron-left": <path d="m14 6-6 6 6 6" />,
  "chevron-right": <path d="m10 6 6 6-6 6" />,
};

export function NavIcon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {icons[name] ?? icons.overview}
    </svg>
  );
}
