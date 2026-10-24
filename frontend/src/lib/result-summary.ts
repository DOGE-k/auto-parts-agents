/** 按技能提取真实返回中的关键字段，转成一句可读中文（前端展示层，不改数据）。
 *
 * 数据来源（前端优化清单任务 7 前置实测 + 后端源码核对，字段名逐一对应）：
 * - 服务器实测 call_chain：tracking.lookup_order_link / tracking.track_real
 *   （result_summary 在后端 _summarize_result 截断到 400 字符，JSON 可能不完整，
 *    looseParse 对截断文本做顶层标量宽松提取，提取不到的字段一律省略）
 * - backend/app/services/real_order.py、order_linkage.py、ppap*.py 各函数真实返回结构
 * 任何字段缺失就省略该部分；完全提取不到时返回 null，调用方回退显示 JSON 原文。
 */
import { displaySystemText } from "./system-labels";

// ===== 宽松解析：完整 JSON 直接 parse；截断 JSON 提取顶层标量（首个同名键优先） =====
function looseParse(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // 截断 JSON → 宽松提取
  }
  const out: Record<string, unknown> = {};
  const re = /"([A-Za-z_][\w.]*)"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?)|(true|false))/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(raw)) !== null) {
    const key = m[1];
    if (key in out) continue;
    if (m[2] !== undefined) out[key] = m[2];
    else if (m[3] !== undefined) out[key] = Number(m[3]);
    else if (m[4] === "true") out[key] = true;
    else out[key] = false;
  }
  return out;
}

// ===== 字段读取（全部防御式：缺失返回 null/undefined，绝不造默认值） =====
function txt(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t : null;
}
function num(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}
function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}
function arr(v: unknown): unknown[] | null {
  return Array.isArray(v) ? v : null;
}
function obj(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function fmtRate(v: number): string {
  return `${Math.round(v * 10) / 10}%`;
}
function fmtMoney(v: string, currency: string | null): string {
  const n = Number(v);
  const s = Number.isFinite(n) ? `${n}` : v;
  return currency === "CNY" ? `${s} 元` : `${s}${currency ? ` ${currency}` : ""}`;
}
function fmtDelta(v: string, currency: string | null): string {
  const n = Number(v);
  const s = Number.isFinite(n) ? `${n}` : v;
  const sign = Number.isFinite(n) && n > 0 ? "+" : "";
  const unit = currency === "CNY" ? " 元" : currency ? ` ${currency}` : "";
  return `${sign}${s}${unit}`;
}
function fmtDate(v: string): string {
  return v.slice(0, 10);
}
function joinList(v: unknown): string[] {
  return (arr(v) ?? []).filter((x): x is string => typeof x === "string" && x.trim() !== "");
}

// ===== 各技能摘要句（仅使用后端真实字段，缺失省略） =====
type Extractor = (d: Record<string, unknown>) => string | null;

function planBrief(d: Record<string, unknown>): string | null {
  const parts: string[] = [];
  const pid = txt(d.plan_id);
  const status = txt(d.status);
  if (pid) parts.push(`方案 ${pid}`);
  if (status) parts.push(`状态 ${displaySystemText(status)}`);
  if (bool(d.has_shortage) === true) parts.push("存在缺料");
  const approval = txt(d.approval_id);
  if (approval) parts.push(`审批 ${approval}`);
  const po = txt(d.po_draft_id);
  if (po) parts.push(`PO 草稿 ${po}`);
  return parts.length ? parts.join("，") : null;
}

// 通用成功/失败返回（审批、ERP 草稿写入、质量问题处置等 success/error 结构）
function successErrorSummary(d: Record<string, unknown>): string | null {
  const success = bool(d.success);
  if (success === false) {
    const err = txt(d.error);
    return err ? `失败：${displaySystemText(err)}` : "执行失败";
  }
  if (success !== true) return null;
  const parts: string[] = ["执行成功"];
  const named: [string, string][] = [
    ["approval_id", "审批"],
    ["erp_draft_id", "ERP 草稿"],
    ["po_draft_id", "PO 草稿"],
    ["plan_id", "方案"],
    ["work_order_no", "工单"],
  ];
  for (const [key, label] of named) {
    const v = txt(d[key]);
    if (v) parts.push(`${label} ${v}`);
  }
  if (bool(d.idempotent_replay) === true) parts.push("幂等回读原审批");
  return parts.join("，");
}

function coordinatorAskSummary(d: Record<string, unknown>): string | null {
  const parts: string[] = [];
  const tools = num(d.tool_count);
  const rounds = num(d.rounds);
  const answer = txt(d.answer);
  if (tools !== null) parts.push(`调用工具 ${tools} 次`);
  if (rounds !== null) parts.push(`${rounds} 轮推理`);
  if (answer) parts.push(`回答 ${answer.length} 字`);
  return parts.length ? `协同问答：${parts.join("，")}` : null;
}

const extractors: Record<string, Extractor> = {
  // ===== 跟单 =====
  "tracking.track_real": (d) => {
    const status = txt(d.status);
    if (status === "MES_UNREACHABLE") return "MES 不可达，无法读取生产数据";
    if (status === "NOT_FOUND") return "工单在 MES 中不存在，无法读取实际生产速率";
    const parts: string[] = [];
    const woNo = txt(d.work_order_no);
    if (woNo) parts.push(`工单 ${woNo}`);
    if (status) parts.push(`状态 ${displaySystemText(status)}`);
    const rate = num(d.completion_rate);
    const completed = txt(d.completed_qty);
    const qty = txt(d.quantity);
    if (rate !== null) {
      parts.push(`完成率 ${fmtRate(rate)}${completed !== null && qty !== null ? `（${completed}/${qty} 件）` : ""}`);
    }
    const etaStatus = txt(d.eta_status);
    const eta = txt(d.eta);
    if (etaStatus === "RATE_BASED" && eta) parts.push(`预计完工 ${fmtDate(eta)}`);
    else if (etaStatus) parts.push(`ETA 状态 ${displaySystemText(etaStatus)}`);
    const riskMsg = joinList((arr(d.risks) ?? []).map((r) => (obj(r) ? txt(obj(r)!.message) : null)).filter((x): x is string => x !== null))[0];
    if (riskMsg) parts.push(`风险：${riskMsg}`);
    return parts.length ? parts.join("，") : null;
  },
  "tracking.lookup_order_link": (d) => {
    const status = txt(d.status);
    if (status === "ERP_ORDER_NOT_FOUND") return txt(d.message) ?? "ERP 中不存在该销售订单，未查询 MES";
    const linked = bool(d.linked);
    if (linked === null) return null;
    if (!linked) return "该 ERP 订单未建立 MES 工单正式关联";
    const assoc = obj(d.association);
    const links = assoc ? (arr(assoc.links) ?? []).map((x) => obj(x)).filter((x): x is Record<string, unknown> => x !== null) : [];
    const count = assoc ? num(assoc.count) : null;
    const mes = links.length ? obj(links[0].mes_record) : null;
    const woNo = mes ? txt(mes.record_no) : null;
    const cautions: string[] = [];
    if (links.length && bool(links[0].quantity_consistent) === false) cautions.push("数量核验不一致");
    if (links.length && bool(links[0].due_date_consistent) === false) cautions.push("交期核验不一致");
    const head = `已关联 MES 工单${woNo ? ` ${woNo}` : ""}${count !== null && count > 1 ? `（共 ${count} 个）` : ""}`;
    return cautions.length ? `${head}（${cautions.join("、")}，需人工核对）` : head;
  },
  "tracking.find_real_by_no": (d) => {
    const found = bool(d.found);
    if (found === false) return txt(d.note) ?? "MES 中不存在该工单编号";
    if (found !== true) return null;
    const parts: string[] = [];
    const no = txt(d.work_order_no);
    const status = txt(d.status);
    const qty = txt(d.quantity);
    const erp = txt(d.customer_order_no);
    if (no) parts.push(`找到工单 ${no}`);
    if (status) parts.push(`状态 ${displaySystemText(status)}`);
    if (qty) parts.push(`数量 ${qty}`);
    if (erp) parts.push(`对应订单 ${erp}`);
    return parts.length ? parts.join("，") : null;
  },
  "tracking.list_real_work_orders": (d) => {
    const count = num(d.count);
    if (count === null) return null;
    return `共 ${count} 个 MES 生产工单${bool(d.possibly_truncated) ? "（超过上限，可能截断）" : ""}`;
  },
  "tracking.assess_delivery_impact": (d) => {
    const status = txt(d.status);
    if (status === "ERROR") return txt(d.error) ?? "无法读取工单真实 MES 数据";
    if (status === "DATA_MISSING") {
      const miss = arr(d.missing_fields) ?? [];
      const detail = miss.length && obj(miss[0]) ? txt(obj(miss[0])!.detail) : null;
      return `交期影响数据缺失${detail ? `：${displaySystemText(detail)}` : ""}`;
    }
    if (status !== "ok") return null;
    const parts: string[] = [];
    const wo = txt(d.work_order_no);
    if (wo) parts.push(`工单 ${wo}`);
    const conclusion = txt(d.conclusion);
    if (conclusion) parts.push(displaySystemText(conclusion));
    return parts.length ? parts.join("：") : null;
  },
  "tracking.check_real_ship_gate": (d) => {
    const can = bool(d.can_ship);
    if (can === null) return null;
    if (can) {
      const rate = num(d.completion_rate);
      return `允许发运（审批与质量门禁通过${rate !== null ? `，完成率 ${fmtRate(rate)}` : ""}）`;
    }
    const reasons = joinList(d.blocking_reasons);
    return `不可发运：${reasons.length ? reasons.join("；") : "存在未满足的门禁条件"}`;
  },

  // ===== 报价 =====
  "quotation.analyze_real": (d) => {
    const status = txt(d.status);
    if (status === "DATA_MISSING") {
      const miss = arr(d.missing_data) ?? [];
      const detail = miss.length && obj(miss[0]) ? txt(obj(miss[0])!.detail) : null;
      return `报价数据缺失${detail ? `：${displaySystemText(detail)}` : "，无法自动报价"}`;
    }
    const parts: string[] = [];
    const qid = txt(d.quotation_id);
    if (qid) parts.push(`报价 ${qid}`);
    const unit = txt(d.unit_price);
    const total = txt(d.total_price);
    const cur = txt(d.currency);
    const qty = num(d.quantity);
    if (unit) parts.push(`单价 ${fmtMoney(unit, cur)}${qty !== null ? ` × ${qty} 件` : ""}`);
    if (total) parts.push(`总额 ${fmtMoney(total, cur)}`);
    const stock = bool(d.stock_sufficient);
    if (stock !== null) parts.push(stock ? "库存可覆盖订单" : "库存不足，需采购");
    const est = obj(d.delivery_estimate);
    const days = est ? num(est.estimated_days) : null;
    if (days !== null) parts.push(`交付参考 ${days} 天`);
    const missCount = arr(d.missing_data)?.length;
    if (missCount) parts.push(`缺 ${missCount} 项数据（详见原文）`);
    return parts.length ? parts.join("，") : null;
  },
  "quotation.get_real": (d) => {
    if (bool(d.found) === false) return "报价不存在";
    const parts: string[] = [];
    const qid = txt(d.quotation_id);
    const status = txt(d.status);
    const unit = txt(d.unit_price);
    const total = txt(d.total_price);
    const cur = txt(d.currency);
    if (qid) parts.push(`报价 ${qid}`);
    if (status) parts.push(`状态 ${displaySystemText(status)}`);
    if (unit) parts.push(`单价 ${fmtMoney(unit, cur)}`);
    if (total) parts.push(`总额 ${fmtMoney(total, cur)}`);
    return parts.length ? parts.join("，") : null;
  },
  "quotation.find_real_by_erp_order": (d) => {
    const found = bool(d.found);
    if (found === false) return txt(d.note) ?? "该 ERP 订单没有已保存的真实报价";
    if (found !== true) return null;
    const parts: string[] = [];
    const qid = txt(d.quotation_id);
    const total = txt(d.total_price);
    const cur = txt(d.currency);
    const status = txt(d.status);
    if (qid) parts.push(`最新报价 ${qid}`);
    if (total) parts.push(`总额 ${fmtMoney(total, cur)}`);
    if (status) parts.push(`状态 ${displaySystemText(status)}`);
    const plan = obj(d.latest_procurement_plan);
    if (plan && bool(plan.found) === true) {
      const pid = txt(plan.plan_id);
      const pstatus = txt(plan.status);
      parts.push(`采购方案 ${pid ?? ""}${pstatus ? `（${displaySystemText(pstatus)}）` : ""}`);
    } else if (plan && bool(plan.found) === false) {
      parts.push("尚无采购方案");
    }
    return parts.length ? parts.join("，") : null;
  },
  "quotation.list_real_sales_orders": (d) => {
    const count = num(d.count);
    if (count === null) return null;
    return `共 ${count} 个 ERP 销售订单${bool(d.possibly_truncated) ? "（超过上限，可能截断）" : ""}`;
  },
  "quotation.assess_cost_impact": (d) => {
    const status = txt(d.status);
    if (status === "ERROR") return txt(d.error) ?? "评估失败";
    if (status === "DATA_MISSING") {
      const miss = arr(d.missing_fields) ?? [];
      const detail = miss.length && obj(miss[0]) ? txt(obj(miss[0])!.detail) : null;
      return `成本数据缺失${detail ? `：${displaySystemText(detail)}` : ""}`;
    }
    if (status !== "ok") return null;
    const parts: string[] = [];
    const supplier = txt(d.supplier_name);
    if (supplier) parts.push(`供应商 ${supplier}`);
    const delta = txt(d.material_cost_delta);
    const cur = txt(d.currency);
    if (delta) parts.push(`材料成本 ${fmtDelta(delta, cur)}`);
    const perUnit = txt(d.per_unit_surcharge);
    if (perUnit) parts.push(`单件加价 ${fmtMoney(perUnit, cur)}`);
    const before = txt(d.material_margin_rate_before_percent);
    const after = txt(d.material_margin_rate_after_percent);
    if (before && after) parts.push(`材料毛利率 ${before}% → ${after}%`);
    return parts.length ? parts.join("，") : null;
  },

  // ===== 采购 =====
  "procurement.analyze_real": (d) => {
    if (txt(d.status) === "NO_SHORTAGE") return "库存充足，无需采购";
    const parts: string[] = [];
    const req = obj(d.net_requirement);
    const shortage = req ? num(req.shortage_count) : null;
    if (shortage !== null) parts.push(`检测到 ${shortage} 项缺料`);
    const rec = txt(d.recommended_option_id);
    if (rec) parts.push(`推荐方案 ${rec}`);
    const opts = arr(d.supplier_options);
    if (opts) parts.push(`共 ${opts.length} 个供应商可选`);
    return parts.length ? parts.join("，") : null;
  },
  "procurement.get_real_plan": (d) => {
    if (bool(d.found) === false) return "采购方案不存在";
    return planBrief(d);
  },
  "procurement.find_real_plan_by_quotation": (d) => {
    const found = bool(d.found);
    if (found === false) return txt(d.note) ?? "该报价还没有采购方案";
    if (found !== true) return null;
    return planBrief(d);
  },
  "procurement.assess_combination": (d) => {
    if (txt(d.status) !== "ok") return null;
    const parts: string[] = [];
    const suppliers = joinList(d.suppliers);
    if (suppliers.length) parts.push(`组合 ${suppliers.join(" + ")}`);
    const cost = txt(d.combined_cost);
    const cur = txt(d.currency);
    if (cost) parts.push(`合计 ${fmtMoney(cost, cur)}`);
    const lead = num(d.max_lead_time_days);
    if (lead !== null) parts.push(`最长交期 ${lead} 天`);
    const coverage = obj(d.coverage);
    if (coverage) {
      if (bool(coverage.complete) === true) parts.push("覆盖全部缺料");
      else {
        const uncovered = joinList(coverage.uncovered_items);
        if (uncovered.length) parts.push(`未覆盖：${uncovered.join("、")}`);
      }
    }
    const warnings = joinList(d.warnings);
    if (warnings.length) parts.push(warnings[0]);
    return parts.length ? parts.join("，") : null;
  },
  "procurement.inventory_overview": (d) => {
    const status = txt(d.status);
    if (status === "ERP_UNREACHABLE") {
      const err = txt(d.error);
      return `ERP 不可达${err ? `：${err}` : ""}`;
    }
    if (status !== "ok") return null;
    const count = num(d.count);
    return count !== null ? `ERP 库存 ${count} 条仓位记录` : null;
  },

  // ===== 质量 =====
  "quality.get_real_package": (d) => {
    const parts: string[] = [];
    const open = num(d.open_issues_count);
    const gate = bool(d.quality_gate_passed);
    if (open !== null) parts.push(`未关闭问题 ${open} 个`);
    if (gate !== null) parts.push(`质量门禁${gate ? "通过" : "未通过"}`);
    const docs = arr(d.documents)?.length;
    if (docs !== undefined) parts.push(`工程文档 ${docs} 份`);
    const insp = arr(d.inspections)?.length;
    if (insp !== undefined) parts.push(`检验记录 ${insp} 条`);
    const missing = arr(d.missing_documents)?.length;
    if (missing) parts.push(`缺文档 ${missing} 份`);
    return parts.length ? parts.join("，") : null;
  },
  "quality.list_open_issues": (d) => {
    const items = arr(d.items);
    if (!items) return null;
    return `全厂未关闭质量问题 ${items.length} 个`;
  },
  "quality.assess_quality_impact": (d) => {
    if (txt(d.status) !== "ok") return null;
    const parts: string[] = [];
    const open = num(d.open_issues_count);
    const gate = bool(d.quality_gate_passed);
    if (open !== null) parts.push(`未关闭问题 ${open} 个`);
    if (gate !== null) parts.push(`质量门禁${gate ? "通过" : "未通过"}`);
    const concl = joinList(d.impact_conclusions);
    if (concl.length) parts.push(concl[0]);
    return parts.length ? parts.join("，") : null;
  },
  "quality.check_issue_closure": (d) => {
    const status = txt(d.status);
    if (status === "error") return txt(d.error) ?? "质量问题不属于指定工单";
    if (status !== "ok") return null;
    const ready = bool(d.closure_ready);
    if (ready === true) return "闭环前置校验通过（处置/根因/围堵/纠正措施齐备）";
    if (ready === false) {
      const failed: string[] = [];
      const checks = obj(d.checks);
      if (checks) {
        const names: [string, string][] = [
          ["resolved", "问题未解决"],
          ["disposition_recorded", "处置决定未记录"],
          ["root_cause_recorded", "根因未记录"],
          ["containment_action_recorded", "围堵措施未记录"],
          ["corrective_actions_verified", "纠正措施未验证"],
        ];
        for (const [key, label] of names) if (checks[key] === false) failed.push(label);
      }
      return `未达关闭条件：${failed.length ? failed.join("、") : "存在未满足项"}`;
    }
    return null;
  },

  // ===== PPAP（只读查询） =====
  "quality.ppap_find_tasks": (d) => {
    const total = num(d.total);
    return total !== null ? `PPAP 任务 ${total} 个` : null;
  },
  "quality.ppap_get_applicability": (d) => {
    if (bool(d.ok) === false) return `PPAP 查询失败（${txt(d.error) ?? "未知原因"}）`;
    const elements = arr(d.elements);
    if (!elements) return null;
    const tid = txt(d.task_id);
    return `${tid ? `任务 ${tid} ` : ""}适用性条目 ${elements.length} 条`;
  },
  "quality.ppap_get_status": (d) => {
    if (bool(d.ok) === false) return `PPAP 查询失败（${txt(d.error) ?? "未知原因"}）`;
    const parts: string[] = [];
    const task = obj(d.task);
    if (task) {
      const tid = txt(task.task_id);
      const state = txt(task.run_state);
      const pkg = txt(task.package_state);
      if (tid) parts.push(`任务 ${tid}`);
      if (state) parts.push(`状态 ${displaySystemText(state)}`);
      if (pkg) parts.push(`资料包 ${displaySystemText(pkg)}`);
    }
    const issues = arr(d.issues)?.length;
    const artifacts = arr(d.artifacts)?.length;
    const reviews = arr(d.reviews)?.length;
    if (issues !== undefined) parts.push(`问题 ${issues} 个`);
    if (artifacts !== undefined) parts.push(`文档 ${artifacts} 份`);
    if (reviews !== undefined) parts.push(`评审 ${reviews} 次`);
    return parts.length ? parts.join("，") : null;
  },
  "quality.ppap_explain_missing": (d) => {
    if (bool(d.ok) === false) return `PPAP 查询失败（${txt(d.error) ?? "未知原因"}）`;
    const parts: string[] = [];
    const findings = arr(d.findings)?.length;
    if (findings !== undefined) parts.push(`发现 ${findings} 项`);
    const summary = obj(d.summary);
    const blockers = summary ? num(summary.blocker_count) : null;
    const failed = summary ? num(summary.checks_failed) : null;
    if (blockers !== null) parts.push(`阻断 ${blockers} 项`);
    if (failed !== null) parts.push(`未通过检查 ${failed} 项`);
    return parts.length ? parts.join("，") : null;
  },
  "quality.ppap_evidence_plan": (d) => {
    if (bool(d.ok) === false) return `PPAP 查询失败（${txt(d.error) ?? "未知原因"}）`;
    const entries = arr(d.entries);
    return entries ? `证据收集项 ${entries.length} 条` : null;
  },
  "quality.ppap_get_versions": (d) => {
    if (bool(d.ok) === false) return `PPAP 查询失败（${txt(d.error) ?? "未知原因"}）`;
    const items = arr(d.items);
    return items ? `版本记录 ${items.length} 条` : null;
  },
};

// 运行记录 operation（real_agent_runs）→ 同一提取逻辑。后端 _agent_run 的
// operation 名与技能 id 不同（tracking:track ↔ tracking.track_real），逐一对应。
const runKeyToSkill: Record<string, string> = {
  "quotation:analyze": "quotation.analyze_real",
  "quotation:find_by_erp_order": "quotation.find_real_by_erp_order",
  "quotation:assess_cost_impact": "quotation.assess_cost_impact",
  "procurement:analyze": "procurement.analyze_real",
  "procurement:find_by_quotation": "procurement.find_real_plan_by_quotation",
  "procurement:assess_combination": "procurement.assess_combination",
  "tracking:track": "tracking.track_real",
  "tracking:find_by_no": "tracking.find_real_by_no",
  "tracking:assess_delivery_impact": "tracking.assess_delivery_impact",
  "shipping:gate_check": "tracking.check_real_ship_gate",
  "quality:package": "quality.get_real_package",
  "quality:assess_impact": "quality.assess_quality_impact",
  "quality:closure_check": "quality.check_issue_closure",
};

const runExtractors: Record<string, Extractor> = {
  "approval:approve_quotation": successErrorSummary,
  "approval:approve_procurement": successErrorSummary,
  "erp_write:create_so_draft": successErrorSummary,
  "erp_write:create_po_draft": successErrorSummary,
  "quality:resolve_issue": successErrorSummary,
  "quality:close_issue": successErrorSummary,
  "coordinator:ask": coordinatorAskSummary,
};

/** 问答调用链返回摘要 → 一句可读中文；无法提取时返回 null（调用方显示 JSON 原文）。 */
export function summarizeSkillResult(skillId: string, rawSummary: string | null | undefined): string | null {
  const extractor = extractors[skillId];
  if (!extractor) return null;
  const raw = (rawSummary ?? "").trim();
  if (!raw.startsWith("{")) return null;
  return extractor(looseParse(raw));
}

/** Agent 运行记录完整结果 → 一句可读中文；无法提取时返回 null。 */
export function summarizeRunResult(agentType: string, operation: string, result: unknown): string | null {
  const key = `${agentType}:${operation}`;
  const extractor = runExtractors[key] ?? extractors[runKeyToSkill[key] ?? ""];
  if (!extractor) return null;
  const data = obj(result);
  if (!data) return null;
  return extractor(data);
}

/** 运行记录输入参数 → 逐项（参数名 + 值）；位置参数后端未留名，如实标「参数N」。 */
export function summarizeRunInput(
  input: { args?: unknown; kwargs?: unknown } | null | undefined,
): { name: string; value: string }[] {
  if (!input || typeof input !== "object") return [];
  const items: { name: string; value: string }[] = [];
  const args = Array.isArray(input.args) ? input.args : [];
  args.forEach((v, i) => {
    items.push({ name: `参数${i + 1}`, value: runValueText(v) });
  });
  const kwargs = obj(input.kwargs) ?? {};
  for (const [k, v] of Object.entries(kwargs)) {
    items.push({ name: k, value: runValueText(v) });
  }
  return items;
}

function runValueText(v: unknown): string {
  if (typeof v === "string") return displaySystemText(v);
  if (v === null || v === undefined) return "—";
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
