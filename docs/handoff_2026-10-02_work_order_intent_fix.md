# 交接文档：工单号自然语言识别修复

> 交给下一位 AI 或开发者执行。  
> 项目路径：`E:\competition\汽车零部件工厂智能体开发`  
> 文档日期：2026-10-02  
> 当前基线：`ac60a13`（§3.57，工作区干净）

## 一、任务结论

前端信息架构改版已经完成：协同问答是默认入口，问答结果可以定位到具体报价、采购方案和工单，各业务模块的完整 8 步链已经默认折叠，连接状态和待审批区也已经补齐。

本轮只处理一个仍然影响核心产品目标的后端问题：用户直接用 MES 工单号提问时，确定性上下文层没有识别工单号，反而把工单号前缀识别成物料编码，导致系统错误追问销售订单号。

不要重新设计前端，也不要重写订单流程。本任务的目标是让自然语言入口能够正确识别工单号，并沿用现有的 `tracking.find_real_by_no` 只读查询链。

## 二、可复现问题

在项目解释器中执行：

```powershell
cd E:\competition\汽车零部件工厂智能体开发
$env:PYTHONPATH = "E:\competition\汽车零部件工厂智能体开发\backend"
.\.conda-env\python.exe -c "from app.services.assistant_context import extract_signals; print(extract_signals('WO-2026-001 什么时候能做完？'))"
```

当前结果已经确认是：

```text
work_order_no=''
item_code='WO-2026'
intents=['tracking']
```

因此页面会出现类似“当前沿用物料=WO-2026”，随后追问销售订单号。正确结果应当是：

```text
work_order_no='WO-2026-001'
item_code=''
intents=['tracking']
```

## 三、已确认的根因

文件：`backend/app/services/assistant_context.py`。

`extract_signals()` 当前在提取工单号时显式过滤了所有 `WO-2026-*`：

```python
wos = [wo for wo in _WORK_ORDER_RE.findall(text) if not wo.startswith("WO-2026-")]
```

过滤后，通用物料正则 `_ITEM_RE` 又把 `WO-2026` 当成物料编码，所以产生了错误上下文。

项目已经有真实工单格式和查找能力，不要另造接口或猜测数据库字段。现有 `_WORK_ORDER_RE`、`REQUIRED_SLOTS` 和 `tracking.find_real_by_no` 先阅读后复用。

## 四、实施要求

1. 删除对真实 `WO-2026-*` 工单号的特殊过滤，让已确认格式的工单号进入 `Signals.work_order_no`。
2. 工单号命中后，不得把同一段文本中的 `WO-2026` 前缀再次写入 `Signals.item_code`。建议按匹配位置排除重叠文本，或采用明确的“工单号优先于物料号”规则；不要扩大物料或工单格式范围。
3. 同一字段出现多个不同工单号时继续遵守“不猜”：不要任选一个，应保持该字段未确认并让上层追问。一个 ERP 销售订单号和一个 MES 工单号属于不同字段，可以同时保留，不能因为它们同时出现就互相清空。
4. `WO-2026-001` 进入跟单或质量问题时，应直接满足 `tracking` / `quality` 的工单槽位，不再强制追问销售订单号。
5. 保持 `TEST_WO_*`、`WO-SO-*` 等项目现有已验证编号的行为不回归；先检查现有测试再决定断言。
6. 不修改 ERPNext/OpenMES 数据，不改真实认证、审批门禁、写入 API 或业务规则。该任务应为确定性解析和测试修复。

## 五、必须新增或调整的测试

优先放在已有的 `backend/tests/test_assistant_session.py`，除非现有测试结构要求另建文件。

至少覆盖以下情况：

```python
def test_work_order_number_is_not_material():
    signals = extract_signals("WO-2026-001 什么时候能做完？")
    assert signals.work_order_no == "WO-2026-001"
    assert signals.item_code == ""
    assert "tracking" in signals.intents

def test_erp_order_and_work_order_can_coexist():
    signals = extract_signals("SAL-ORD-2026-00023 什么时候能做完？WO-2026-001 呢？")
    assert signals.erp_order_id == "SAL-ORD-2026-00023"
    assert signals.work_order_no == "WO-2026-001"
    assert signals.item_code == ""

def test_multiple_work_orders_do_not_guess():
    # 同一条消息出现两个不同工单号时，不能任选一个。
    ...
```

还要补一个协调者路径回归：输入 `WO-2026-001 什么时候能做完？` 时，确定性槽位检查应允许调用跟单查询；如果测试使用 mock 协调者，应断言传给 `tracking.find_real_by_no` 的是 `work_order_no="WO-2026-001"`，而不是物料编码或销售订单号。

## 六、页面级验收

在真实模式、只读操作下完成一次页面验证：

1. 打开“AI 协同问答”。
2. 输入：`WO-2026-001 什么时候能做完？`
3. 系统不应先问“请提供销售订单号”，也不应回显“物料=WO-2026”。
4. 回答应显示真实工单证据，并出现“查看该工单跟单”入口。
5. 点击后应进入“生产跟单 · 跟单视图”，加载 `WO-2026-001` 的真实跟单/质量/发运门禁三面板。
6. 全程不自动审批、不写入 ERPNext/OpenMES。

另回归一次已有路径：`SAL-ORD-2026-00001 什么时候能做完？`，确认销售订单号识别和问答后工单定位仍然正常。

## 七、验证命令

使用项目自带解释器，不要直接调用系统 Anaconda：

```powershell
cd E:\competition\汽车零部件工厂智能体开发
$env:PYTHONPATH = "E:\competition\汽车零部件工厂智能体开发\backend"
.\.conda-env\python.exe -m pytest backend\tests -q

cd frontend
npm run test -- --run
npx tsc --noEmit --incremental false --project tsconfig.json
```

正式构建前先关闭占用 5173 的前端开发服务，再运行：

```powershell
npm run build
```

当前已知环境问题：开发服务运行时，Windows 可能锁住 `frontend/tsconfig.tsbuildinfo` 或 `frontend/node_modules/.vite-temp`，导致 `npm run build` 报 `EPERM`。这不是类型错误；不要杀掉用户进程，先说明并在服务关闭后复验。

## 八、完成标准

只有同时满足以下条件，才可以在交接结果中写“已修复”：

- `WO-2026-001` 被识别为 `work_order_no`，不再被识别为 `item_code`；
- 多工单号不猜，ERP 销售订单号与 MES 工单号可并存；
- 工单号问答不再错误追问销售订单号；
- 问答后的“查看该工单跟单”能定位到 `WO-2026-001`；
- 新增测试与原有后端、前端测试全部通过；
- 页面验证为真实只读，未产生 ERPNext/OpenMES 写入；
- 文档同步到 `docs/current_status_and_fix_plan.md`，新增一节记录根因、测试和页面证据；
- 提交前确认 `git status` 干净，并在交接说明中列出仍未解决的问题。

不要把“前端 39 passed”当成这个后端问题已解决的证据。这个任务的验收重点是：用户说出工单号后，系统能正确理解并直接查到对应工单。
