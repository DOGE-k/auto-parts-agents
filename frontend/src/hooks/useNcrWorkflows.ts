// NCR 处置/关闭工作流状态机（阶段八+十：真实审批-写回-回读-幂等；10.5 从 RealBusinessPage 抽出为 hook）
import { useState, useCallback } from "react";
import {
  getQualityWorkflowStates,
  requestQualityIssueDisposition,
  approveQualityIssueDisposition,
  writeQualityIssueDisposition,
  checkQualityIssueClosure,
  requestQualityIssueClose,
  approveQualityIssueClose,
  writeQualityIssueClose,
} from "../api";
import type { QualityWorkflowStates, RealQualityIssue } from "../api";
import type { NcrForm, NcrWorkflow, QualityInfo } from "../types/realBusiness";

const emptyForm: NcrForm = {
  disposition: "",
  non_conforming_qty: "",
  root_cause: "",
  containment_action: "",
  nc_source: "",
};

type Options = {
  workOrderId: string;
  refreshQualityAndGate: () => Promise<void>;
  onError: (message: string) => void;
  notify: (message: string) => void;
};

export function useNcrWorkflows({ workOrderId, refreshQualityAndGate, onError, notify }: Options) {
  const [ncrWorkflows, setNcrWorkflows] = useState<Record<string, NcrWorkflow>>({});

  const updateNcrWorkflow = useCallback((issueId: string, patch: Partial<NcrWorkflow>) => {
    setNcrWorkflows((prev) => ({
      ...prev,
      [issueId]: { ...prev[issueId], ...patch, form: patch.form ?? prev[issueId]?.form ?? emptyForm },
    }));
  }, []);

  const updateNcrForm = useCallback((issueId: string, field: keyof NcrForm, value: string) => {
    setNcrWorkflows((prev) => {
      const current = prev[issueId] ?? { form: emptyForm };
      return { ...prev, [issueId]: { ...current, form: { ...current.form, [field]: value } } };
    });
  }, []);

  // 阶段十：刷新后按后端审批记录恢复 NCR 在途工作流（不覆盖进行中的状态）
  const restoreNcrWorkflowStates = useCallback(async (qualityData: QualityInfo | null) => {
    if (!qualityData?.quality_records?.length) return;
    try {
      const result: QualityWorkflowStates = await getQualityWorkflowStates();
      setNcrWorkflows((prev) => {
        const next = { ...prev };
        for (const record of qualityData.quality_records) {
          const issueId = String(record.record_id ?? "");
          const state = result.states?.[issueId];
          if (!issueId || !state) continue;
          const existing = next[issueId] ?? {};
          const restored: NcrWorkflow = { ...existing, form: existing.form ?? emptyForm };
          if (state.disposition && !restored.dispositionApprovalId) {
            restored.dispositionApprovalId = state.disposition.approval_id;
            restored.dispositionApproved = state.disposition.approved;
            restored.form = {
              disposition: state.disposition.disposition || restored.form.disposition,
              non_conforming_qty: restored.form.non_conforming_qty,
              root_cause: restored.form.root_cause,
              containment_action: restored.form.containment_action,
              nc_source: restored.form.nc_source,
            };
          }
          if (state.close && !restored.closeApprovalId) {
            restored.closeApprovalId = state.close.approval_id;
            restored.closeApproved = state.close.approved;
          }
          if (restored.dispositionApprovalId || restored.closeApprovalId) next[issueId] = restored;
        }
        return next;
      });
    } catch {
      // 恢复失败不阻塞面板；数据层幂等保护仍然生效
    }
  }, []);

  const requestNcrDisposition = useCallback(async (issue: RealQualityIssue) => {
    const issueId = String(issue.record_id ?? "");
    if (!issueId || !workOrderId) return;
    const form = ncrWorkflows[issueId]?.form;
    if (!form?.disposition || !form.root_cause.trim() || !form.containment_action.trim()) {
      onError("请先选择 NCR 处置，并填写根因与遏制措施；系统不会替你推断处置结论。");
      return;
    }
    updateNcrWorkflow(issueId, { busy: "requesting_disposition" });
    onError("");
    try {
      const result = await requestQualityIssueDisposition(issueId, {
        work_order_id: workOrderId,
        disposition: form.disposition,
        non_conforming_qty: form.non_conforming_qty.trim() ? Number(form.non_conforming_qty) : undefined,
        root_cause: form.root_cause.trim(),
        containment_action: form.containment_action.trim(),
        nc_source: form.nc_source || undefined,
      });
      updateNcrWorkflow(issueId, {
        dispositionApprovalId: result.approval?.approval_id,
        dispositionApproved: false,
        dispositionResult: { status: result.status ?? "待审批", error: result.error },
        busy: "",
      });
      notify(`NCR ${issueId} 处置审批已建立${result.approval?.approval_id ? `（${result.approval.approval_id}）` : ""}`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", dispositionResult: { error: e instanceof Error ? e.message : "创建处置审批失败" } });
      onError(e instanceof Error ? e.message : "创建处置审批失败");
    }
  }, [ncrWorkflows, updateNcrWorkflow, workOrderId, onError, notify]);

  const approveNcrDisposition = useCallback(async (issueId: string) => {
    const approvalId = ncrWorkflows[issueId]?.dispositionApprovalId;
    if (!approvalId) return;
    updateNcrWorkflow(issueId, { busy: "approving_disposition" });
    onError("");
    try {
      const result = await approveQualityIssueDisposition(approvalId);
      updateNcrWorkflow(issueId, { dispositionApproved: true, dispositionResult: { status: "已批准", ...result }, busy: "" });
      notify(`NCR ${issueId} 处置审批已批准`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", dispositionResult: { error: e instanceof Error ? e.message : "处置审批失败" } });
      onError(e instanceof Error ? e.message : "处置审批失败");
    }
  }, [ncrWorkflows, updateNcrWorkflow, onError, notify]);

  const writeNcrDisposition = useCallback(async (issueId: string) => {
    const workflow = ncrWorkflows[issueId];
    const approvalId = workflow?.dispositionApprovalId;
    if (!approvalId || !workflow.dispositionApproved || !workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "writing_disposition" });
    onError("");
    try {
      const result = await writeQualityIssueDisposition(issueId, workOrderId, approvalId);
      updateNcrWorkflow(issueId, {
        dispositionResult: {
          status: result.status,
          error: result.success ? undefined : result.error,
          read_back_verified: result.read_back_verified,
          idempotent: result.idempotent,
        },
        busy: "",
      });
      await refreshQualityAndGate();
      if (!result.success) throw new Error(result.error ?? "NCR 处置写回未验证");
      notify(`NCR ${issueId} 处置已写回并完成回读验证`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", dispositionResult: { error: e instanceof Error ? e.message : "NCR 处置写回失败" } });
      onError(e instanceof Error ? e.message : "NCR 处置写回失败");
    }
  }, [ncrWorkflows, refreshQualityAndGate, updateNcrWorkflow, workOrderId, onError, notify]);

  const checkNcrClosure = useCallback(async (issueId: string) => {
    if (!workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "checking_closure" });
    onError("");
    try {
      const result = await checkQualityIssueClosure(issueId, workOrderId);
      updateNcrWorkflow(issueId, { closureCheck: result, busy: "" });
      notify(result.closure_ready ? `NCR ${issueId} 已满足关闭前置条件` : `NCR ${issueId} 仍有关闭前置条件未满足`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closureCheck: undefined });
      onError(e instanceof Error ? e.message : "关闭前置校验失败");
    }
  }, [updateNcrWorkflow, workOrderId, onError, notify]);

  const requestNcrClose = useCallback(async (issueId: string) => {
    if (!workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "requesting_close" });
    onError("");
    try {
      const result = await requestQualityIssueClose(issueId, workOrderId);
      updateNcrWorkflow(issueId, { closeApprovalId: result.approval?.approval_id, closeApproved: false, closeResult: { status: "待审批" }, busy: "" });
      notify(`NCR ${issueId} 关闭审批已建立${result.approval?.approval_id ? `（${result.approval.approval_id}）` : ""}`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closeResult: { error: e instanceof Error ? e.message : "创建关闭审批失败" } });
      onError(e instanceof Error ? e.message : "创建关闭审批失败");
    }
  }, [updateNcrWorkflow, workOrderId, onError, notify]);

  const approveNcrClose = useCallback(async (issueId: string) => {
    const approvalId = ncrWorkflows[issueId]?.closeApprovalId;
    if (!approvalId) return;
    updateNcrWorkflow(issueId, { busy: "approving_close" });
    onError("");
    try {
      await approveQualityIssueClose(approvalId);
      updateNcrWorkflow(issueId, { closeApproved: true, closeResult: { status: "已批准" }, busy: "" });
      notify(`NCR ${issueId} 关闭审批已批准`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closeResult: { error: e instanceof Error ? e.message : "关闭审批失败" } });
      onError(e instanceof Error ? e.message : "关闭审批失败");
    }
  }, [ncrWorkflows, updateNcrWorkflow, onError, notify]);

  const writeNcrClose = useCallback(async (issueId: string) => {
    const workflow = ncrWorkflows[issueId];
    const approvalId = workflow?.closeApprovalId;
    if (!approvalId || !workflow.closeApproved || !workOrderId) return;
    updateNcrWorkflow(issueId, { busy: "writing_close" });
    onError("");
    try {
      const result = await writeQualityIssueClose(issueId, workOrderId, approvalId);
      updateNcrWorkflow(issueId, {
        closeResult: { status: result.status, error: result.success ? undefined : result.error, read_back_verified: result.read_back_verified, idempotent: result.idempotent },
        busy: "",
      });
      await refreshQualityAndGate();
      if (!result.success) throw new Error(result.error ?? "NCR 关闭写回未验证");
      notify(`NCR ${issueId} 已关闭并完成回读验证`);
    } catch (e) {
      updateNcrWorkflow(issueId, { busy: "", closeResult: { error: e instanceof Error ? e.message : "NCR 关闭写回失败" } });
      onError(e instanceof Error ? e.message : "NCR 关闭写回失败");
    }
  }, [ncrWorkflows, refreshQualityAndGate, updateNcrWorkflow, workOrderId, onError, notify]);

  const resetNcrWorkflows = useCallback(() => {
    setNcrWorkflows({});
  }, []);

  return {
    ncrWorkflows,
    restoreNcrWorkflowStates,
    updateNcrForm,
    requestNcrDisposition,
    approveNcrDisposition,
    writeNcrDisposition,
    checkNcrClosure,
    requestNcrClose,
    approveNcrClose,
    writeNcrClose,
    resetNcrWorkflows,
  };
}
