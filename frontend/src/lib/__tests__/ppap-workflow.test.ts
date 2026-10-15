// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { getDatasetGroup, getPpapWorkflow, PPAP_DATASET_GROUPS } from "../ppap-workflow";
import type { PpapDetail } from "../../types/ppap";

const detail = (patch: Partial<PpapDetail> = {}) => ({ task: { package_state: "NOT_SUBMITTED" },
  applicability: { total: 22, confirmed: 22, unconfirmed_element_ids: [] }, issues: [], artifacts: [], ...patch } as PpapDetail);
const artifact = { artifact_id: "UI-WORKFLOW-A", scope: "PACKAGE", version: 2, status: "DRAFT" } as PpapDetail["artifacts"][number];
describe("PPAP 阶段依据实际事实推导", () => {
  it("13个数据集各归入一个业务类别，不漏掉既有契约", () => {
    const all = PPAP_DATASET_GROUPS.flatMap(group => group.datasets);
    expect(all).toHaveLength(13); expect(new Set(all).size).toBe(13);
    expect(getDatasetGroup("msa_study")).toBe("inspection"); expect(getDatasetGroup("process_operation")).toBe("engineering");
    expect(getDatasetGroup("unknown")).toBe("other");
  });
  it("检查尚未取得时不能显示0阻断或可批准，输入条数也不代表完成", () => {
    const facts = getPpapWorkflow(detail({ inputs: Array(30).fill({}) }), null, null);
    expect(facts.realtimeBlockers).toBeNull(); expect(facts.findingsCount).toBeNull();
    expect(facts.reviewReasons).toContain("实时检查结果尚未取得"); expect(facts.next.stage).toBe("compile");
  });
  it("浏览阶段不改变适用性，待确认要求优先处理", () => {
    const facts = getPpapWorkflow(detail({ applicability: { total: 22, confirmed: 21, unconfirmed_element_ids: ["msa"] } }), { findings: [] }, null);
    expect(facts.next.stage).toBe("requirements"); expect(facts.pendingRequirements).toBe(1);
  });
  it("实时阻断和登记阻断分别统计，并沿显式服务端目标跳转", () => {
    const target = { kind: "input", dataset: "characteristic", record_key: "UI-K", part_side: "LEFT" };
    const facts = getPpapWorkflow(detail({ issues: [{ status: "OPEN", blocks_approval: true } as PpapDetail["issues"][number]] }),
      { findings: [{ blocks_approval: true, navigation: target }, { severity: "MAJOR" }] }, null);
    expect(facts.realtimeBlockers).toBe(1); expect(facts.registeredBlockers).toBe(1); expect(facts.findingsCount).toBe(2);
    expect(facts.next.navigation).toEqual(target); expect(facts.next.stage).toBe("prepare");
  });
  it("历史版本对照不能冒充当前最新版输入一致性", () => {
    const facts = getPpapWorkflow(detail({ artifacts: [artifact] }), { findings: [] }, { ok: true, has_artifact: true, comparison_artifact_id: "UI-OLD", changed: false });
    expect(facts.inputChanged).toBeNull(); expect(facts.reviewReasons).toContain("当前资料与该版本是否一致尚未核对");
  });
  it("实时数据已修正但登记阻断未处理时引导复查记录，不能猜还有缺料", () => {
    const facts = getPpapWorkflow(detail({ issues: [{ status: "OPEN", blocks_approval: true } as PpapDetail["issues"][number]] }), { findings: [] }, null);
    expect(facts.next.stage).toBe("compile"); expect(facts.next.label).toBe("核对已登记问题"); expect(facts.realtimeBlockers).toBe(0);
  });
  it("可靠变更需重新编制，不从任务快照字段猜资料未变", () => {
    const facts = getPpapWorkflow(detail({ artifacts: [artifact] }), { findings: [] }, { ok: true, has_artifact: true, comparison_artifact_id: artifact.artifact_id, changed: true });
    expect(facts.next.stage).toBe("compile"); expect(facts.reviewReasons).toContain("资料已变化，需重新编制");
  });
  it("零阻断且资料一致只允许申请内部审核，不推导客户接受", () => {
    const task = detail({ artifacts: [artifact] });
    const facts = getPpapWorkflow(task, { findings: [] }, { ok: true, has_artifact: true, comparison_artifact_id: artifact.artifact_id, changed: false });
    expect(facts.next.stage).toBe("review"); expect(facts.reviewReasons).toEqual([]);
    expect(task.task.package_state).toBe("NOT_SUBMITTED");
  });
  it("批准版本冻结，新增变化指向新草稿而不是改写批准版", () => {
    const task = detail({ artifacts: [{ ...artifact, status: "APPROVED" }] });
    expect(getPpapWorkflow(task, { findings: [] }, { ok: true, has_artifact: true, comparison_artifact_id: artifact.artifact_id, changed: false }).next.stage).toBe("review");
    expect(getPpapWorkflow(task, { findings: [] }, { ok: true, has_artifact: true, comparison_artifact_id: artifact.artifact_id, changed: true }).next.stage).toBe("compile");
  });
  it("退回版本先展示意见，修订后才指向新编制，不引导批准旧版", () => {
    const task = detail({ task: { package_state: "RETURNED" } as PpapDetail["task"], artifacts: [{ ...artifact, status: "SUPERSEDED" }] });
    const facts = getPpapWorkflow(task, { findings: [] }, { ok: true, has_artifact: true, comparison_artifact_id: artifact.artifact_id, changed: false });
    expect(facts.next.label).toBe("查看退回意见"); expect(facts.next.description).toContain("不能直接再次批准");
  });
});
