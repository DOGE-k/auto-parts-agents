// displaySystemText：英文枚举值转中文、系统名替换、业务单号保持原样
import { describe, expect, it } from "vitest";
import { displaySystemText } from "../system-labels";

describe("displaySystemText", () => {
  it("英文枚举状态转中文（忽略大小写与首尾空白）", () => {
    expect(displaySystemText("DRAFT")).toBe("草稿");
    expect(displaySystemText("draft")).toBe("草稿");
    expect(displaySystemText(" IN_PROGRESS ")).toBe("进行中");
    expect(displaySystemText("ok")).toBe("成功");
    expect(displaySystemText("ERROR")).toBe("失败");
    expect(displaySystemText("PENDING_APPROVAL")).toBe("待审批");
    expect(displaySystemText("MANUAL_HANDLED")).toBe("已人工接管");
  });

  it("系统集成名替换为中文", () => {
    expect(displaySystemText("erpnext_api 超时")).toBe("ERP 接口 超时");
    expect(displaySystemText("openmes 连接失败")).toBe("MES 系统 连接失败");
  });

  it("业务单号与未登记值保持原样", () => {
    expect(displaySystemText("SAL-ORD-2026-00001")).toBe("SAL-ORD-2026-00001");
    expect(displaySystemText("WO-BUPT-261017-01")).toBe("WO-BUPT-261017-01");
    expect(displaySystemText("SomeUnknownValue")).toBe("SomeUnknownValue");
    expect(displaySystemText(null)).toBe("");
    expect(displaySystemText(undefined)).toBe("");
  });
});
