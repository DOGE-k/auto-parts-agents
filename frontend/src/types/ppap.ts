export type PpapJson = Record<string, unknown>;
export type PpapSide = "NONE" | "LEFT" | "RIGHT" | "BOTH";
export type PpapMode = "REAL" | "TEST";
export type PpapRequirement = "" | "submit" | "retain" | "on_request" | "not_applicable";

export type PpapTask = {
  data_mode: PpapMode;
  task_id: string; customer_id: string; customer_name: string; part_no: string; part_name: string;
  part_side: PpapSide; drawing_version: string; engineering_change_level: string;
  submission_reason: string; submission_level: string; due_date: string; owner: string;
  run_state: string; package_state: string; customer_state: string; template_id: string;
  template_version: string; template_source: string; business_object: PpapJson; coverage: PpapJson;
  input_snapshot_hash: string; state_note: string; open_issue_count?: number; blocking_issue_count?: number;
};
export type PpapTaskFields = Pick<PpapTask, "customer_id" | "customer_name" | "part_no" | "part_name" |
  "part_side" | "drawing_version" | "engineering_change_level" | "submission_reason" | "submission_level" | "due_date">;
export type PpapTaskCreateFields = PpapTaskFields & { mode?: PpapMode };
export type PpapElement = {
  element_id: string; title: string; requirement: PpapRequirement; applicability_basis: string;
  suggested_requirement: PpapRequirement; applicability_condition: string; status: string; status_reason: string;
  compilation_method: string; required_inputs: string[]; evidence_types: string[];
  responsible_role: string; content?: PpapJson; check_result: PpapJson; notes: string;
};
export type PpapDatasetContract = { label: string; required: string[]; optional: string[]; numeric: string[]; boolean: string[] };
export type PpapTemplate = {
  template_id: string; template_version: string; template_source: string; display_name: string;
  notice: string; is_customer_template: boolean; elements: PpapElement[]; datasets: string[];
  side_sensitive_datasets: string[]; evidence_source_types: string[];
};
export type PpapEvidence = {
  evidence_id: string; source_type: string; source_id: string; locator: string; summary: string;
  read_status: "OK" | "EMPTY" | "ERROR"; review_state: string; truncated: boolean;
  content_hash: string; collected_by: string; collected_at: string; error?: PpapJson | null;
  element_ids?: string[];
};
export type PpapInput = {
  input_id: string; dataset: string; record_key: string; part_side: PpapSide; element_id: string;
  payload: PpapJson; source_type: string; source_ref: string; confirmed_by: string; confirmed_at: string;
};
export type PpapIssue = {
  issue_id: string; severity: string; kind: string; check_id: string; element_id: string;
  field_path: string; title: string; detail: string; basis: string; required_evidence: string;
  responsible_role: string; status: string; blocks_approval: boolean; resolution_note: string;
};
export type PpapArtifact = {
  artifact_id: string; scope: string; element_id: string; version: number; kind: string; file_name: string;
  status: string; content_hash: string; input_snapshot_hash: string; template_version: string;
  byte_size: number; frozen_at: string; created_by: string; created_at: string;
};
export type PpapReview = {
  review_id: string; artifact_id: string; artifact_version: number; decision: string;
  reviewer: string; reviewer_role: string; author: string; separation_warning: string;
  comments: string; created_at: string;
};
export type PpapRun = {
  run_id: string; status: string; input_snapshot_hash: string; requested_by: string; started_at: string;
  finished_at: string; idempotent_replay: boolean; summary: PpapJson; error: PpapJson | null;
  steps?: PpapJson[]; call_chain?: PpapJson[];
};
export type PpapDetail = {
  ok: boolean; task: PpapTask; elements: PpapElement[]; inputs: PpapInput[]; evidence: PpapEvidence[];
  issues: PpapIssue[]; artifacts: PpapArtifact[]; reviews: PpapReview[]; runs: PpapRun[];
  template: PpapTemplate; applicability: { total: number; confirmed: number; unconfirmed_element_ids: string[] };
};
export type PpapList = { items: PpapTask[]; total: number; limit: number; offset: number };
export type PpapInputs = { items: PpapInput[]; contracts: Record<string, PpapDatasetContract>; datasets: string[] };
export type PpapSuggestion = { field: string; value: unknown; origin: string; source_ref: string;
  source_type: string; input_id: string; record_key: string };
export type PpapInputAssistance = { ok: boolean; read_only: boolean; task_id: string; data_mode: PpapMode;
  dataset: string; part_side: PpapSide; suggestions: PpapSuggestion[];
  candidates: Array<{ characteristic_id: string; name: string; part_side: PpapSide; record_key: string;
    input_id: string; source_ref: string; source_type: string; conflict?: boolean | string }>;
  conflicts: unknown[]; notices: string[]; existing_input?: PpapInput | null; explicitly_empty_fields?: string[] };
export type PpapNavigation = { kind: string; dataset?: string; field?: string; element_id?: string;
  input_id?: string; record_key?: string; part_side?: PpapSide; reason?: string };
export type PpapPlan = { collectors: Array<{ collector_id: string; source_type: string; label: string;
  resolvable: boolean; missing_business_keys: string[]; description: string; note: string }>; notice: string };
export type PpapMutation = PpapJson & { success?: boolean; ok?: boolean; error?: string; errors?: string[];
  rejected?: Array<{ element_id?: string; message?: string; reason?: string }>; task?: PpapTask;
  separation_warning?: string; idempotent_replay?: boolean };
