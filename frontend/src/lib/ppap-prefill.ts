import type { PpapSuggestion } from "../types/ppap";

export type PpapPrefill = { fields: Record<string, string>; origins: Record<string, PpapSuggestion> };
const textValue = (value: unknown): string => typeof value === "object" ? JSON.stringify(value) : String(value);

/** Replace only unchanged machine suggestions. Manual edits and explicit null win. */
export function mergePpapSuggestions(fields: Record<string, string>, nullFields: Set<string>,
  origins: Record<string, PpapSuggestion>, suggestions: PpapSuggestion[], allowedFields: string[],
  excludedFields: Set<string> = new Set()): PpapPrefill {
  const nextFields = { ...fields };
  const nextOrigins: Record<string, PpapSuggestion> = {};
  for (const [field, suggestion] of Object.entries(origins)) {
    if (nextFields[field] === textValue(suggestion.value)) delete nextFields[field];
  }
  for (const suggestion of suggestions) {
    const field = suggestion.field;
    if (!allowedFields.includes(field) || excludedFields.has(field) || nullFields.has(field)
        || suggestion.value === null || suggestion.value === undefined || suggestion.value === ""
        || (typeof suggestion.value === "number" && !Number.isFinite(suggestion.value))) continue;
    if (nextFields[field] !== undefined && nextFields[field] !== "") continue;
    nextFields[field] = textValue(suggestion.value);
    nextOrigins[field] = suggestion;
  }
  return { fields: nextFields, origins: nextOrigins };
}
