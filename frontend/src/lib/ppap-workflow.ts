import type { PpapArtifact, PpapDetail, PpapJson, PpapNavigation } from "../types/ppap";

export type PpapStage = "requirements" | "prepare" | "compile" | "review";
export const PPAP_DATASET_GROUPS = [
  { id: "base", label: "基础与客户要求", description: "零件、版本、客户要求", datasets: ["product_base", "customer_requirement", "change_record", "customer_handling"] },
  { id: "engineering", label: "工艺与工程", description: "工序、关系、特性规格", datasets: ["process_operation", "process_relation", "characteristic"] },
  { id: "inspection", label: "检验与研究", description: "尺寸、MSA、过程、材料", datasets: ["inspection_value", "msa_study", "process_study", "material_evidence"] },
  { id: "physical", label: "设备与实物", description: "设备、包装、样品", datasets: ["packaging_equipment", "physical_record"] },
];
export const getDatasetGroup = (dataset: string) => PPAP_DATASET_GROUPS.find(group => group.datasets.includes(dataset))?.id || "other";
const record = (value: unknown): PpapJson => value !== null && typeof value === "object" && !Array.isArray(value) ? value as PpapJson : {};
export type PpapWorkflow = {
  pendingRequirements: number; findingsCount: number | null; realtimeBlockers: number | null; registeredBlockers: number;
  latestPackage: PpapArtifact | null; inputChanged: boolean | null;
  next: { stage: PpapStage; label: string; description: string; navigation?: PpapNavigation };
  reviewReasons: string[];
};

/** Display facts only; neither viewing a step nor having inputs proves readiness or approval. */
export function getPpapWorkflow(detail: PpapDetail, missing: PpapJson | null, impact: PpapJson | null): PpapWorkflow {
  const pendingRequirements = detail.applicability.unconfirmed_element_ids.length;
  const registeredBlockers = detail.issues.filter(issue => issue.status === "OPEN" && issue.blocks_approval).length;
  const findings = missing && missing.ok !== false && Array.isArray(missing.findings) ? missing.findings.map(record) : null;
  const blocking = findings?.filter(item => item.blocks_approval === true || item.severity === "BLOCKER") || [];
  const latestPackage = detail.artifacts.filter(artifact => artifact.scope === "PACKAGE").reduce<PpapArtifact | null>((latest, item) => !latest || item.version > latest.version ? item : latest, null);
  const impactMatches = impact?.ok === true && impact.has_artifact === true && impact.comparison_artifact_id === latestPackage?.artifact_id;
  const inputChanged = impactMatches && typeof impact?.changed === "boolean" ? impact.changed : null;
  const reviewReasons: string[] = [];
  if (!latestPackage) reviewReasons.push("尚未生成资料包草稿");
  else if (latestPackage.status !== "DRAFT") reviewReasons.push("当前版本不是待审核草稿");
  if (pendingRequirements) reviewReasons.push(`适用性待确认 ${pendingRequirements} 项`);
  if (!findings) reviewReasons.push("实时检查结果尚未取得");
  else if (blocking.length) reviewReasons.push(`实时检查存在 ${blocking.length} 项阻断`);
  if (registeredBlockers) reviewReasons.push(`已登记问题仍有 ${registeredBlockers} 项未处理阻断`);
  if (latestPackage && inputChanged === null) reviewReasons.push("当前资料与该版本是否一致尚未核对");
  if (inputChanged) reviewReasons.push("资料已变化，需重新编制");
  if (["PENDING_REVIEW", "IN_REVIEW"].includes(detail.task.package_state)) reviewReasons.push("已有版本正在内部审核");
  let next: PpapWorkflow["next"];
  if (pendingRequirements) next = { stage: "requirements", label: "确认提交要求", description: `还有 ${pendingRequirements} 项要求待确认；不自动采用建议值。` };
  else if (latestPackage && ["APPROVED", "ARCHIVED"].includes(latestPackage.status)) next = inputChanged === true
    ? { stage: "compile", label: "查看资料变化", description: "批准版已冻结；当前资料变化须生成新草稿，保留旧版。" }
    : { stage: "review", label: "查看批准版与归档", description: "内部批准与客户接受分别记录；按指定版本查看归档和实际处理记录。" };
  else if (inputChanged) next = { stage: "compile", label: "重新编制草稿", description: "已登记资料已变化，现有草稿不能代表当前内容。" };
  else if (detail.task.package_state === "RETURNED" || latestPackage?.status === "SUPERSEDED") next = { stage: "review", label: "查看退回意见", description: "按退回意见修订资料后生成新草稿；退回版本不能直接再次批准。" };
  else if (!findings) next = { stage: "compile", label: "核对检查结果", description: "检查结果尚未取得，不能据此判断资料齐全。" };
  else if (blocking.length) {
    const target = blocking.map(item => record(item.navigation)).find(value =>
      ["input", "evidence", "task", "applicability"].includes(String(value.kind)) && (value.kind !== "input" || typeof value.dataset === "string") && (value.kind !== "applicability" || typeof value.element_id === "string"));
    const stage = target?.kind === "task" || target?.kind === "applicability" ? "requirements" : "prepare";
    next = { stage, label: "补充待办资料", description: `当前实时检查有 ${blocking.length} 项阻断；先定位对应资料，补充后重新检查。`, ...(target ? { navigation: target as PpapNavigation } : {}) };
  } else if (registeredBlockers) {
    next = { stage: "compile", label: "核对已登记问题", description: `当前实时检查无阻断，登记记录仍有 ${registeredBlockers} 项未处理阻断；重新检查并核对处理记录。` };
  } else if (!latestPackage) next = { stage: "compile", label: "生成资料包草稿", description: "可以编制并查看一致性检查结果；草稿仍需人工审核。" };
  else next = { stage: "review", label: "核对内部审核条件", description: "查看指定版本、当前资料是否一致及审核条件；服务端进行最终校验。" };
  return { pendingRequirements, findingsCount: findings?.length ?? null, realtimeBlockers: findings ? blocking.length : null,
    registeredBlockers, latestPackage, inputChanged, next, reviewReasons };
}
