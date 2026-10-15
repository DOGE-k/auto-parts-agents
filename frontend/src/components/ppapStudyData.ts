export type PpapStudyField = "measurements" | "values" | "subgroups" | "photo_refs";
export type StudyNumber = number | null;
export type StudyPart = { part?: string; part_id?: string; trials: StudyNumber[] };
export type StudyAppraiser = { appraiser: string; parts: StudyPart[] };
export type StudyData = StudyAppraiser[] | StudyNumber[] | StudyNumber[][] | string[];
export type StudyParse = { supported: true; data: StudyData; empty: boolean } |
  { supported: false; error: string; invalid: boolean };

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const onlyKeys = (value: Record<string, unknown>, allowed: string[]) =>
  Object.keys(value).every((key) => allowed.includes(key));
const isNumber = (value: unknown): value is StudyNumber => value === null ||
  (typeof value === "number" && Number.isFinite(value));

/** Read exact input shapes; never coerce strings, reorder axes, or drop unknown fields. */
export function parseStudyData(field: PpapStudyField, text: string): StudyParse {
  if (!text.trim() || text.trim() === "null") return { supported: true, data: [], empty: true };
  let value: unknown;
  try { value = JSON.parse(text); }
  catch { return { supported: false, invalid: true, error: "原始数据格式不完整，请修正高级 JSON；原文本已保留。" }; }
  if (!Array.isArray(value)) return { supported: false, invalid: false,
    error: "此数据结构暂不支持表格编辑，请在高级 JSON 中原样修订；不会自动改写。" };
  const unsupported = (): StudyParse => ({ supported: false, invalid: false,
    error: "此数据包含表格未识别的字段或结构，请在高级 JSON 中原样修订；不会删改原数据。" });
  const invalid = (): StudyParse => ({ supported: false, invalid: true,
    error: "实测数据只接受有限数值或 null（缺测），不接受布尔值、数字字符串或非有限数值；原文本已保留。" });
  if (field === "values") return value.every(isNumber) ?
    { supported: true, data: value as StudyNumber[], empty: false } : invalid();
  if (field === "subgroups") {
    if (!value.every(Array.isArray)) return unsupported();
    return value.every((group) => group.every(isNumber)) ?
      { supported: true, data: value as StudyNumber[][], empty: false } : invalid();
  }
  if (field === "photo_refs") return value.every((reference) => typeof reference === "string" && !/[\r\n]/.test(reference)) ?
    { supported: true, data: value as string[], empty: false } : unsupported();
  for (const appraiser of value) {
    if (!isObject(appraiser) || !onlyKeys(appraiser, ["appraiser", "parts"]) ||
      typeof appraiser.appraiser !== "string" || !Array.isArray(appraiser.parts)) return unsupported();
    for (const part of appraiser.parts) {
      if (!isObject(part) || !onlyKeys(part, ["part", "part_id", "trials"]) ||
        (typeof part.part !== "string" && typeof part.part_id !== "string") ||
        ("part" in part && "part_id" in part) || !Array.isArray(part.trials)) return unsupported();
      if (!part.trials.every(isNumber)) return invalid();
    }
  }
  return { supported: true, data: value as StudyAppraiser[], empty: false };
}

export function parseStudyNumber(text: string): { valid: true; value: StudyNumber } | { valid: false } {
  const value = text.trim();
  if (!value) return { valid: true, value: null };
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value)) return { valid: false };
  const number = Number(value);
  return Number.isFinite(number) ? { valid: true, value: number } : { valid: false };
}
