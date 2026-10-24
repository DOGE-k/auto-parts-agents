// summarizeSkillResult / summarizeRunResult：按技能提取真实返回关键字段转一句中文。
// 截断样例为服务器实测「SAL-ORD-2026-00001 什么时候能做完？」的 call_chain 原文
// （后端 _summarize_result 截断 400 字符）；完整对象样例字段与
// backend/app/services/real_order.py、order_linkage.py 的真实返回一一对应。
import { describe, expect, it } from "vitest";
import { summarizeRunInput, summarizeRunResult, summarizeSkillResult } from "../result-summary";

describe("summarizeSkillResult（截断 JSON，服务器实测样例）", () => {
  it("tracking.track_real：工单号/状态/完成率/ETA 状态提取，缺失字段省略", () => {
    const raw =
      '{"work_order_id": "7", "work_order_no": "WO-BUPT-261017-01", "status": "PENDING", "quantity": "500.00", "completed_qty": "0.00", "completion_rate": 0.0, "due_date": "2026-11-19T23:00:00.000000Z", "eta": null, "eta_status": "DATA_MISSING", "eta_basis": "missing_observed_rate", "observed_rate": null, "eta_data_gaps": [{"field": "observed_production_rate", "detail": "OpenMES 没有同时包含实际产量和实际耗时/开始时间的执行记录';
    expect(summarizeSkillResult("tracking.track_real", raw)).toBe(
      "工单 WO-BUPT-261017-01，状态 待产，完成率 0%（0.00/500.00 件），ETA 状态 数据缺失",
    );
  });

  it("tracking.lookup_order_link：截断处无 association 时只说已关联，不编造工单号", () => {
    const raw =
      '{"status": "LINKED", "order_id": "SAL-ORD-2026-00001", "linked": true, "erp_order": {"order_id": "SAL-ORD-2026-00001", "customer_id": "上汽集团", "customer_name": "上汽集团", "transaction_date": "2026-10-17", "delivery_date": "2026-11-20", "status": "Draft", "currency": "CNY", "total": "42500.0", "items": [{"item_code": "BD-2401", "item_name": "制动盘-前轮 Brake Disc Front", "qty": "500.0", "rate": "85.0", "uo';
    expect(summarizeSkillResult("tracking.lookup_order_link", raw)).toBe("已关联 MES 工单");
  });

  it("未登记的 skill_id 返回 null（调用方回退 JSON 原文）", () => {
    expect(summarizeSkillResult("unknown.skill", '{"status": "ok"}')).toBeNull();
  });
});

describe("summarizeRunResult（完整结果对象）", () => {
  it("tracking:track：ETA 按实际速率给出预计完工，并带首条风险", () => {
    const result = {
      work_order_id: "7",
      work_order_no: "WO-BUPT-261017-01",
      status: "IN_PROGRESS",
      quantity: "500.00",
      completed_qty: "181.00",
      completion_rate: 36.36,
      eta: "2026-11-05T08:00:00.000000Z",
      eta_status: "RATE_BASED",
      risks: [{ level: "medium", message: "生产进度偏低，需关注交期风险" }],
    };
    expect(summarizeRunResult("tracking", "track", result)).toBe(
      "工单 WO-BUPT-261017-01，状态 进行中，完成率 36.4%（181.00/500.00 件），预计完工 2026-11-05，风险：生产进度偏低，需关注交期风险",
    );
  });

  it("tracking:track：MES 不可达时如实报告", () => {
    expect(
      summarizeRunResult("tracking", "track", {
        status: "MES_UNREACHABLE",
        eta_data_gaps: [{ field: "mes", detail: "OpenMES 不可达：连接超时" }],
      }),
    ).toBe("MES 不可达，无法读取生产数据");
  });

  it("quotation:analyze：单价×数量=总额、库存结论、交付参考", () => {
    const result = {
      quotation_id: "QUO-20261024-AB12",
      status: "DRAFT",
      quantity: 500,
      unit_price: "85.0",
      total_price: "42500.0",
      currency: "CNY",
      stock_sufficient: true,
      delivery_estimate: { basis: "stock_available", estimated_days: 0 },
      missing_data: [],
    };
    expect(summarizeRunResult("quotation", "analyze", result)).toBe(
      "报价 QUO-20261024-AB12，单价 85 元 × 500 件，总额 42500 元，库存可覆盖订单，交付参考 0 天",
    );
  });

  it("quotation:analyze：数据缺失时给出缺失明细", () => {
    const result = {
      status: "DATA_MISSING",
      missing_data: [{ field: "selling_price:BD-2401", detail: "缺少价格数据，无法自动报价" }],
    };
    expect(summarizeRunResult("quotation", "analyze", result)).toBe(
      "报价数据缺失：缺少价格数据，无法自动报价",
    );
  });

  it("shipping:gate_check：不可发运时列出真实阻断原因", () => {
    const result = {
      can_ship: false,
      blocking_reasons: ["报价/订单未审批", "生产进度不足 (0% < 90%)"],
      completion_rate: 0,
    };
    expect(summarizeRunResult("shipping", "gate_check", result)).toBe(
      "不可发运：报价/订单未审批；生产进度不足 (0% < 90%)",
    );
  });

  it("tracking:assess_delivery_impact：直接使用后端中文结论", () => {
    const result = {
      status: "ok",
      work_order_no: "WO-BUPT-261017-01",
      verdict: "arrival_in_time",
      conclusion: "物料于交期前 12 天到位，物料维度不构成延期",
    };
    expect(summarizeRunResult("tracking", "assess_delivery_impact", result)).toBe(
      "工单 WO-BUPT-261017-01：物料于交期前 12 天到位，物料维度不构成延期",
    );
  });

  it("quality:package：未关闭问题/门禁/缺文档计数", () => {
    const result = {
      open_issues_count: 2,
      quality_gate_passed: false,
      documents: [{}, {}],
      inspections: [{}],
      missing_documents: ["Control Plan"],
    };
    expect(summarizeRunResult("quality", "package", result)).toBe(
      "未关闭问题 2 个，质量门禁未通过，工程文档 2 份，检验记录 1 条，缺文档 1 份",
    );
  });

  it("quality:closure_check：未达关闭条件列出未满足项", () => {
    const result = {
      status: "ok",
      closure_ready: false,
      checks: {
        resolved: false,
        disposition_recorded: true,
        root_cause_recorded: false,
        containment_action_recorded: true,
        corrective_actions_verified: false,
      },
    };
    expect(summarizeRunResult("quality", "closure_check", result)).toBe(
      "未达关闭条件：问题未解决、根因未记录、纠正措施未验证",
    );
  });

  it("quotation:find_by_erp_order：报价与采购方案状态联动", () => {
    const result = {
      found: true,
      quotation_id: "QUO-1",
      status: "APPROVED",
      total_price: "42500.0",
      currency: "CNY",
      latest_procurement_plan: { found: true, plan_id: "PROC-1", status: "PENDING_APPROVAL" },
    };
    expect(summarizeRunResult("quotation", "find_by_erp_order", result)).toBe(
      "最新报价 QUO-1，总额 42500 元，状态 已批准，采购方案 PROC-1（待审批）",
    );
  });

  it("procurement:analyze：缺料数与推荐方案", () => {
    const result = {
      status: "PENDING_APPROVAL",
      net_requirement: { shortage_count: 2, shortage_items: [] },
      recommended_option_id: "SUP-2",
      supplier_options: [{}, {}],
    };
    expect(summarizeRunResult("procurement", "analyze", result)).toBe(
      "检测到 2 项缺料，推荐方案 SUP-2，共 2 个供应商可选",
    );
  });

  it("approval:approve_quotation：审批成功与幂等回读", () => {
    expect(
      summarizeRunResult("approval", "approve_quotation", {
        success: true,
        approval_id: "APR-1",
        approved: true,
        idempotent_replay: true,
      }),
    ).toBe("执行成功，审批 APR-1，幂等回读原审批");
    expect(
      summarizeRunResult("approval", "approve_quotation", {
        success: false,
        error: "审批未通过，不能创建 ERP 草稿",
      }),
    ).toBe("失败：审批未通过，不能创建 ERP 草稿");
  });

  it("coordinator:ask：轮次/工具次数/回答字数", () => {
    expect(
      summarizeRunResult("coordinator", "ask", {
        answer: "## 结论\n工单未开工",
        rounds: 3,
        tool_count: 2,
      }),
    ).toBe("协同问答：调用工具 2 次，3 轮推理，回答 11 字");
  });

  it("未登记的 operation 返回 null（调用方回退 JSON 原文）", () => {
    expect(summarizeRunResult("mock", "demo", { status: "ok" })).toBeNull();
  });
});

describe("summarizeRunInput", () => {
  it("位置参数标参数N，kwargs 用真实参数名，枚举值转中文", () => {
    expect(
      summarizeRunInput({ args: ["7"], kwargs: { erp_order_id: "SAL-ORD-2026-00001", approved: true, nested: { a: 1 } } }),
    ).toEqual([
      { name: "参数1", value: "7" },
      { name: "erp_order_id", value: "SAL-ORD-2026-00001" },
      { name: "approved", value: "true" },
      { name: "nested", value: '{"a":1}' },
    ]);
  });
});
