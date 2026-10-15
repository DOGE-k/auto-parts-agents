// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPpapTask, decidePpapReview, getPpapTasks, getPpapTemplate, getPpapInputAssistance, getPpapChangeImpact, recordPpapCustomerState, registerPpapEvidence, savePpapInput, setRealSessionToken, submitPpapReview } from "../../api";

const fetchStub = vi.fn();
beforeEach(() => {
  window.sessionStorage.clear(); vi.stubGlobal("fetch", fetchStub);
  fetchStub.mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify({ success: true }) });
});
afterEach(() => { window.sessionStorage.clear(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
function call() { const [url, init] = fetchStub.mock.calls.at(-1)! as [string, RequestInit]; return { url, init, body: JSON.parse(String(init.body || "{}")) }; }
describe("PPAP API 会话和契约（隔离网络 stub）", () => {
  it("已有资料识别与指定版本对照只发GET并保留明确对象筛选", async () => {
    await getPpapInputAssistance("UI-TEST/T", "inspection_value", "LEFT", "C/1", "R-1");
    const query = new URL(call().url, "https://local.invalid");
    expect(query.pathname).toContain("UI-TEST%2FT/input-assistance");
    expect(query.searchParams.get("characteristic_id")).toBe("C/1");
    expect(query.searchParams.get("part_side")).toBe("LEFT");
    expect(query.searchParams.get("record_key")).toBe("R-1");
    expect(call().init.method || "GET").toBe("GET"); expect(call().init.body).toBeUndefined();
    await getPpapChangeImpact("UI-TEST-T", "ART/1");
    expect(new URL(call().url, "https://local.invalid").searchParams.get("artifact_id")).toBe("ART/1");
    expect(call().init.method || "GET").toBe("GET");
  });
  it("模板 descriptor 解包，使用独立 /ppap 命名空间", async () => {
    fetchStub.mockResolvedValue({ ok: true, text: async () => JSON.stringify({ template: { template_source: "project_defined" }, datasets: {} }) });
    expect((await getPpapTemplate()).template_source).toBe("project_defined"); expect(call().url).toMatch(/\/api\/ppap\/templates$/);
  });
  it("创建请求依靠Cookie，不携带自填owner/reviewer；包含明确侧别", async () => {
    await createPpapTask({ customer_id: "UI-TEST-C", customer_name: "界面测试客户", part_no: "UI-TEST-L", part_name: "界面测试件", part_side: "LEFT", drawing_version: "A", engineering_change_level: "", submission_reason: "界面测试", submission_level: "", due_date: "" });
    expect(call().init.credentials).toBe("include"); expect(call().init.method).toBe("POST");
    expect(call().body.part_side).toBe("LEFT"); expect(call().body).not.toHaveProperty("owner"); expect(call().body).not.toHaveProperty("reviewer");
    expect(call().body.mode).toBe("REAL");
    expect(new Headers(call().init.headers).has("Idempotency-Key")).toBe(true);
  });
  it("测试模式明确提交，列表模式与零件条件同时传递", async () => {
    await createPpapTask({ customer_id: "UI-TEST-C", customer_name: "界面测试客户", part_no: "UI-TEST-L", part_name: "界面测试件", part_side: "LEFT", drawing_version: "A", engineering_change_level: "", submission_reason: "界面测试", submission_level: "", due_date: "", mode: "TEST" });
    expect(call().body.mode).toBe("TEST");
    await getPpapTasks(20, " PART-1 ", "TEST");
    const url = new URL(call().url, "https://local.invalid");
    expect(url.searchParams.get("mode")).toBe("TEST"); expect(url.searchParams.get("part_no")).toBe("PART-1");
    expect(url.searchParams.get("offset")).toBe("20");
    await getPpapTasks(0, "", "");
    expect(new URL(call().url, "https://local.invalid").searchParams.has("mode")).toBe(false);
  });
  it("审核和客户处理body匹配契约，并且不发送审核人", async () => {
    await submitPpapReview("UI-TEST-T", "UI-TEST-A", "核对资料"); expect(call().url).toMatch(/\/reviews\/submit$/);
    expect(call().body).toEqual({ artifact_id: "UI-TEST-A", note: "核对资料" });
    await decidePpapReview("UI-TEST-T", "UI-TEST-R", "RETURNED", "缺原件");
    expect(call().body).toEqual({ decision: "RETURNED", note: "缺原件" }); expect(call().body).not.toHaveProperty("reviewer");
    await recordPpapCustomerState("UI-TEST-T", "SUBMITTED", "UI-TEST-REF", "实际回执");
    expect(call().body).toEqual({ state: "SUBMITTED", reference: "UI-TEST-REF", note: "实际回执" });
  });
  it("服务结构化422错误明确展示，不解释成没有此项", async () => {
    fetchStub.mockResolvedValue({ ok: false, status: 422, text: async () => JSON.stringify({ detail: { success: false, code: "invalid_input", error: "输入不允许携带判定字段" } }) });
    await expect(savePpapInput("UI-TEST-T", { dataset: "inspection_value", payload: {} })).rejects.toThrow("输入不允许携带判定字段");
  });
  it("保留已提交但回读失败的业务代码，供界面只读恢复而非重复保存", async () => {
    fetchStub.mockResolvedValue({ ok: false, status: 422, text: async () => JSON.stringify({ detail: { success: false, code: "read_back_failed", error: "本次写入已提交，但数据库回读不可用" } }) });
    await expect(savePpapInput("UI-TEST-T", { dataset: "inspection_value", payload: {} })).rejects.toMatchObject({ status: 422, code: "read_back_failed", message: "本次写入已提交，但数据库回读不可用" });
  });
  it("保留服务异常状态，避免把不确定的提交结果显示成确定未写入", async () => {
    fetchStub.mockResolvedValue({ ok: false, status: 503, text: async () => JSON.stringify({ detail: "服务暂时不可用" }) });
    await expect(savePpapInput("UI-TEST-T", { dataset: "inspection_value", payload: {} })).rejects.toMatchObject({ status: 503, message: "服务暂时不可用" });
  });
  it("HTTP成功携带业务回读失败时同样保留恢复代码", async () => {
    fetchStub.mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify({ success: false, code: "read_back_failed", error: "已提交，回读未完成" }) });
    await expect(savePpapInput("UI-TEST-T", { dataset: "inspection_value", payload: {} })).rejects.toMatchObject({ status: 200, code: "read_back_failed", message: "已提交，回读未完成" });
  });
  it("原件登记只传显式绑定的条目，未追加目录全量或身份字段", async () => {
    await registerPpapEvidence("UI-TEST-T", { source_type: "user_upload", source_id: "UI-TEST-DRAWING", summary: "测试原件", locator: "UI-TEST.pdf", element_ids: ["design_records"] });
    expect(call().url).toMatch(/\/evidence$/); expect(call().body.element_ids).toEqual(["design_records"]);
    expect(call().body).not.toHaveProperty("actor"); expect(call().body).not.toHaveProperty("reviewer");
  });
  it("运行时Bearer保留在请求头，路径对任务编号编码", async () => {
    setRealSessionToken("UI_TEST_ONLY_SESSION");
    await savePpapInput("UI-TEST/T", { dataset: "process_operation", payload: { op_id: "10", op_name: "界面测试" }, source_ref: "UI-TEST-REF" });
    expect(call().url).toContain("UI-TEST%2FT"); expect(new Headers(call().init.headers).get("Authorization")).toBe("Bearer UI_TEST_ONLY_SESSION");
    expect(call().body).not.toHaveProperty("access_token");
  });
});
