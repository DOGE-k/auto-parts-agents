/** Format system names for the product UI without changing stored or API data. */

// 系统里会原样出现在摘要/状态字段中的英文枚举值；显示层统一转中文。
// 精确（忽略大小写）匹配后才替换，未命中的值（订单号、ID 等）保持原样。
const enumWords: Record<string, string> = {
  // 报价/方案/订单生命周期
  DRAFT: "草稿",
  PENDING_APPROVAL: "待审批",
  APPROVED: "已批准",
  REJECTED: "已驳回",
  ACCEPTED: "已接受",
  CANCELLED: "已取消",
  COMPLETED: "已完成",
  DONE: "已完成",
  DELIVERED: "已交付",
  SHIPPED: "已发运",
  RELEASED: "已放行",
  CLOSED: "已关闭",
  // 生产执行
  PLANNED: "已计划",
  PENDING: "待产",
  IN_PROGRESS: "进行中",
  PAUSED: "已暂停",
  // 协同事件
  OPEN: "待处理",
  PROCESSING: "协同中",
  MANUAL_HANDLED: "已人工接管",
  FAILED: "失败",
  // 运行结果
  OK: "成功",
  ERROR: "失败",
  // 摘要中的数据可用性/关联状态（real_order / order_linkage 返回值）
  DATA_MISSING: "数据缺失",
  LINKED: "已关联",
  UNLINKED: "未关联",
  NOT_FOUND: "未找到",
  ERP_ORDER_NOT_FOUND: "ERP 订单不存在",
  MES_UNREACHABLE: "MES 不可达",
  ERP_UNREACHABLE: "ERP 不可达",
  NO_SHORTAGE: "无缺料",
  PO_DRAFT_CREATED: "PO 草稿已创建",
  // ETA 估算口径（real_order.track_order）
  RATE_BASED: "按实际速率估算",
  // 质量问题状态
  RESOLVED: "已解决",
  ACKNOWLEDGED: "已确认",
  WAIVED: "已豁免",
  // 布尔型摘要（result_summary 取自 result.status/success 等字段）
  TRUE: "是",
  FALSE: "否",
};

export function displaySystemText(value: string | null | undefined): string {
  const raw = value ?? "";
  const direct = enumWords[raw.trim().toUpperCase()];
  if (direct !== undefined) return direct;
  return raw
    .replace(/erpnext_api/gi, "ERP 接口")
    .replace(/openmes_api/gi, "MES 接口")
    .replace(/erpnext/gi, "ERP 系统")
    .replace(/openmes/gi, "MES 系统");
}
