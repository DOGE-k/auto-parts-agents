import { describe, expect, it } from "vitest";
import { mergePpapSuggestions } from "../ppap-prefill";
import type { PpapSuggestion } from "../../types/ppap";
const suggestion = (field: string, value: unknown): PpapSuggestion => ({field, value, origin: "confirmed_input", source_ref: "isolated-test-source", source_type: "user_confirmation", input_id: "ISOLATED-INPUT", record_key: "ISOLATED-KEY"});
describe("PPAP prefill respects operator choices", () => {
  it("fills empty fields and preserves zero, manual data, and explicit null", () => {
    const result = mergePpapSuggestions({ lsl: "0", unit: "cm" }, new Set(["usl"]), {},
      [suggestion("lsl", 9), suggestion("unit", "mm"), suggestion("usl", 11), suggestion("nominal", 0)], ["lsl", "unit", "usl", "nominal"]);
    expect(result.fields).toEqual({ lsl: "0", unit: "cm", nominal: "0" });
    expect(Object.keys(result.origins)).toEqual(["nominal"]);
  });
  it("changes characteristic suggestions without erasing later manual edits", () => {
    const old = { unit: suggestion("unit", "mm"), lsl: suggestion("lsl", 9), usl: suggestion("usl", 11) };
    const result = mergePpapSuggestions({ unit: "mm", lsl: "8", usl: "11" }, new Set(), old,
      [suggestion("unit", "cm"), suggestion("lsl", 2), suggestion("usl", 4)], ["unit", "lsl", "usl"]);
    expect(result.fields).toEqual({ unit: "cm", lsl: "8", usl: "4" });
    expect(result.origins).not.toHaveProperty("lsl");
  });
  it("withdraws previous auto-values when the source becomes ambiguous", () => {
    expect(mergePpapSuggestions({ unit: "mm", point: "operator-point" }, new Set(), { unit: suggestion("unit", "mm") }, [], ["unit", "point"]).fields).toEqual({point: "operator-point"});
  });
  it("ignores unknown, missing, invalid, and intentionally cleared values", () => {
    const result = mergePpapSuggestions({}, new Set(), {}, [suggestion("unit", "mm"), suggestion("value", 3), suggestion("lsl", null), suggestion("usl", Infinity)], ["unit", "lsl", "usl"], new Set(["unit"]));
    expect(result.fields).toEqual({});
  });
});
