# PPAP 阶段 A：资料与契约确认清单

对应《PPAP编制业务设计与实施说明.md》§11 阶段 A 的产出要求：**模板映射、输入契约、计算方法及权限/审核规则的确认清单**，并建立“已确认 / 缺数据 / 需要业务确认”三态清单。

本文件只记录已核对的事实与已实现的契约，不代替客户要求或正式标准。凡本文件标注为“需要业务确认”的条目，系统一律不得凭经验填值。

- 编制日期：2026-10-13
- 代码基线：`e4f1a9c2d7b0`（project_auth）之后的 PPAP 分支工作副本
- 对应设计文档：`docs/PPAP编制业务设计与实施说明.md`

---

## 1. 资料盘点结果（事实）

### 1.1 设计文档 §6.1 列出的参考资料

| 资料 | 本机可读性 | 说明 |
|---|---|---|
| `ZLB-J-B035《零件品质基准书》…xlsx` 等 6 个采购侧 PPAP/品质基准 xlsx | **不可读** | 未随仓库分发，本机资料目录中不存在同名文件；仓库内也没有任何 PPAP 相关 xlsx 副本（已按文件名与关键词检索确认） |
| 四张用户提供的产品照片 | **不可读** | 同上 |
| `2026汽车零部件加工业OPC智能体白皮书…pdf` | **不可读** | 同上 |

结论：**没有可读取的客户模板样例、没有可读取的历史测量数据样例**。因此：

- 不能声称已完成“一套客户模板的条目与字段映射”。
- 不能把样例里的公式、随机测量值、签字或结论搬运进系统（设计文档 §6.2）。
- 阶段 C 的“模板填充”必须以**项目自定义内部模板**为对象落地，并在产物上明确标注来源为 `project_defined`，不得标注为客户模板。

### 1.2 仓库内已核对的既有能力（可作为真实取证来源）

| 来源 | 入口 | 可取得内容 |
|---|---|---|
| OpenMES 质量资料包 | `real_order.quality_package(work_order_id)` | 质量问题及状态、SOP/Control Plan 文档引用、检验记录引用、质量门禁结论与原因 |
| OpenMES 工单跟单 | `real_order.track_order(work_order_id)` | 状态、完成率、完工/计划数量、交期、报工与工艺步骤（`batch_steps`）、风险预警 |
| 工单编号解析 | `real_order.find_work_order_by_no(work_order_no)` | `WO-…` → 数字 `work_order_id` |
| 订单—工单关联 | `real_order.lookup_order_link` 通道（tracking AIP） | ERP 销售订单 ↔ MES 工单的正式关联（`LINKED` / `NOT_LINKED`） |
| 采购计划 | `real_order.find_latest_plan_by_quotation` / `analyze_procurement` | 物料、供应商方案、净需求、批次信息 |
| 报价单 | `real_order.get_quotation` / `find_quotation_by_erp_order` | 客户、零件、已确认订单关联 |
| 质量问题闭环 | `quality_todo_list` / `get_quality_issue_workflow_states` / `assess_quality_issue_closure` | NCR 状态、处置、解决、关闭前置检查 |
| 发运门禁 | `real_order.ship_gate_check` | 当前发运判定（本阶段**不**修改其规则） |

以上均为**只读**能力，已注册在四个 AIP 智能体上并进入运行时能力目录，PPAP 取证直接复用，不新建旁路。

### 1.3 明确不存在的接口（不得假装存在）

- 没有“供应商试验报告/材质证明”接口。缺证明时只能生成缺项待办，不能把供应商名称当作试验报告（设计文档 §4）。
- 没有“客户工程批准 / 外观批准（AAR）/ 实验室资质”接口。这类条目只能在人工上传原始文件后才置为已取得（设计文档 §5.2）。
- 没有 Get 全量列表的分页之外能力；所有远端列表读取走既有分页参数，截断时如实标记 `truncated=true`，不宣称已取得全量证据（设计文档 §7）。
- 没有针对真实写入的请求级幂等键（现有实现靠一次性 `approval_id`）。PPAP 自身持久化因此**自带基于输入快照哈希 + 业务键 + 请求键的幂等**，不复用也不假设 Mock 运行时的 `request_idempotency`。

---

## 2. 模板映射（项目自定义内部模板）

### 2.1 模板标识

| 字段 | 值 |
|---|---|
| `template_id` | `internal-default-ppap-v1` |
| `version` | `1.0.0` |
| `source` | `project_defined` |
| `is_customer_template` | `false` |
| `print_layout` | A4 纵向；表头重复行；页眉含“项目自定义模板（非客户模板）”标识 |
| `protected_cells` | 无（全表可编辑，符合 §10“修改草稿”） |

规则：模板目录**可配置**，内部 `element_id` 与工作表顺序解耦（设计文档 §5.1）。客户模板接入时新增 `template_id`，不改内部条目 ID。

### 2.2 条目目录与适用性（`requirement`）

`requirement` ∈ `submit` / `retain` / `on_request` / `not_applicable`，`not_applicable` **必须**带 `applicability_basis`（依据文本），否则检查器报 `BLOCKER`。

| # | element_id | 条目 | 类别 | 默认 requirement | 编制方式 | 必需输入 |
|---|---|---|---|---|---|---|
| 1 | `submission_checklist` | 提交清单 | document | submit | `template_fill` | 任务基础信息 |
| 2 | `design_records` | 设计记录 | document | submit | `reference_document` | 图纸/设计记录文件 |
| 3 | `engineering_change_documents` | 工程变更文件 | document | on_request | `change_history` | 变更记录 |
| 4 | `customer_engineering_approval` | 客户工程批准 | document | on_request | `reference_document` | 客户批准原件 |
| 5 | `dfmea` | 设计 FMEA | document | on_request | `engineering_draft` | 设计基准（人工确认） |
| 6 | `process_flow_diagram` | 过程流程图 | document | submit | `engineering_draft` | 实际工序（工单工艺快照） |
| 7 | `pfmea` | 过程 FMEA | document | submit | `engineering_draft` | 工序 + 特殊特性 + 风险评级方法 |
| 8 | `control_plan` | 控制计划 | document | submit | `engineering_draft` | 工序 + 特殊特性 + 检验方法 |
| 9 | `msa` | 测量系统分析 | document | submit | `deterministic_calc` | 原始 MSA 数据（零件/评价人/测次） |
| 10 | `dimensional_results` | 尺寸结果 | document | submit | `source_data_table` | 测点实测值 + 规格上下限 + 单位 |
| 11 | `material_performance_results` | 材料/性能结果 | document | submit | `source_data_table` | 试验报告或实测值 |
| 12 | `initial_process_study` | 初始过程研究 | document | submit | `deterministic_calc` | 原始测量序列 + 规格上下限 |
| 13 | `qualified_laboratory_documentation` | 实验室资质文件 | document | on_request | `reference_document` | 资质原件 |
| 14 | `appearance_approval_report` | 外观批准报告（AAR） | document | on_request | `reference_document` | 客户签署原件 |
| 15 | `sample_production_part` | 生产件样品 | physical | submit | `physical_record` | 实物流转记录 |
| 16 | `master_sample` | 标准样件 | physical | submit | `physical_record` | 样件登记记录 |
| 17 | `checking_aid` | 检具 | physical | on_request | `physical_record` | 检具登记记录 |
| 18 | `customer_specific_requirements` | 客户特殊要求 | document | on_request | `reference_document` | 客户特殊要求原件 |
| 19 | `psw` | 零件提交保证书 | document | submit | `template_fill` | 任务基础信息 + 各条目状态 |
| 20 | `attachment_index` | 附件目录 | document | submit | `template_fill` | 证据记录 |
| 21 | `equipment_packaging` | 设备及包装表 | document | submit | `template_fill` | 包装/设备确认输入 |
| 22 | `dimensional_layout` | 全尺寸检验布局 | document | on_request | `reference_document` | 图纸标注原件 |

### 2.3 字段映射契约（统一基础信息 → 各表）

同一字段在所有表中只有一个来源字段，避免“重复字段不同来源”（设计文档 §5.3、§6.2-2）。

| 统一字段 | 来源 | 左右件敏感 | 说明 |
|---|---|---|---|
| `customer_id` / `customer_name` | 任务业务对象（订单/报价） | 否 | 空则阻断 PSW/提交清单 |
| `part_no` | 任务 | **是** | 左右件必须各自独立零件号（§3.1） |
| `part_name` | 任务 | **是** | 不允许用相近名称推断归属 |
| `part_side` | 任务 | — | `LEFT`/`RIGHT`/`BOTH`/`NONE`；`LEFT` 与 `RIGHT` 不得共用同一份尺寸/材料数据 |
| `drawing_version` | 任务 | 是 | 与 `engineering_change_level` 成对使用 |
| `engineering_change_level` | 任务 | 是 | 变更等级 |
| `weight` / `weight_unit` | 人工确认输入 `product_base` | 是 | 单位必须显式，缺失不推断 |
| `material_spec` | 人工确认输入 `product_base` 或采购物料主数据 | 是 | 与材料证明必须一致，否则 `CONFLICT` |
| `submission_reason` | 任务 | 否 | 客户提交原因 |
| `submission_level` | 任务 | 否 | 提交等级 |
| `owner` / `reviewer` | 项目会话身份 | 否 | 来自会话，不接受请求体自填 |
| `run_*`（编制运行） | 编制运行时 | 否 | 见 §7 |

左右件约束的强制点（AC01）：
- 任务创建时 `part_side` 必填；`part_no` 与 `part_side` 组合唯一。
- 结构化输入表 `ppap_inputs` 带唯一键 `(task_id, dataset, record_key, part_side)`；写入时若 `part_side` 与他任务/他侧冲突则拒绝。
- 检查器 `PPAP-CHK-SIDE-001`：若某一侧引用了另一侧的记录（`source_ref` 属于其他 `part_side`），报 `CONFLICT/BLOCKER`。

---

## 3. 输入契约（结构化工程与检验输入）

存储：`ppap_inputs`（见迁移 `ppap_task_and_compilation`）。每条记录必须带 `source_type`、`source_ref`、`confirmed_by`、`confirmed_at`。**系统不接受无来源、无确认人的输入。**

| `dataset` | `record_key` 含义 | 载荷（`payload_json`） | 必需字段 | 缺失时行为 |
|---|---|---|---|---|
| `product_base` | 单条 `base` | 名称、重量+单位、材料规格、图纸版本 | 重量单位、材料规格 | 相关表 `WAITING_DATA` |
| `process_operation` | 工序号 | `op_no`、`name`、`sequence`、`source`（工单工艺快照/人工确认） | `op_no`、`name` | 流程图 `WAITING_DATA`，PFMEA/控制计划同时阻断 |
| `characteristic` | 特性编号 | `char_no`、`name`、`kind`(`special`/`normal`)、`op_no`、`spec_lsl`、`spec_usl`、`unit`、`class` | `unit`、上下限至少一个 | 该特性无法判定，报缺项 |
| `inspection_value` | `{char_no}#{sample_no}` | `char_no`、`sample_no`、`value`、`unit`、`measured_at`、`instrument` | `value`、`unit` | 尺寸结果表留空并报缺项，**不填“合格”** |
| `msa_study` | 测量系统名 | `parts`（零件号列表）、`operators`、`trials`、`values[operator][part][trial]`、`unit`、`tolerance`（或 `percent_tolerance_basis`） | 完整三维数据、单位 | MSA `WAITING_DATA`，不估算 |
| `process_study` | 特性编号 | `char_no`、`values[]`、`lsl`、`usl`、`unit`、`subgroup_size`（可选） | 数值序列、上下限、单位 | 过程研究 `WAITING_DATA` |
| `material_evidence` | 报告号 | `report_no`、`supplier`、`lot_no`、`test_items[]`、`verdict_source`（报告原文结论文本） | `report_no` | 材料结果 `WAITING_DATA` |
| `packaging_equipment` | 单条 `packing` | 包装形式、每箱数量、箱重+单位、托盘数、设备清单 | 数量、单位 | 设备及包装表 `WAITING_DATA` |
| `customer_requirement` | 要求编号 | 要求文本、来源文件、是否已确认 | 来源文件 | 客户特殊要求 `WAITING_DATA` |

硬约束：
- 数值字段一律 `Decimal`/字符串原样保存，单位与数值分开存，**不做隐式单位换算**；单位不一致时报 `CONFLICT` 而不是自动换算（§5.3）。
- 任何 `dataset` 都**不允许**写入“结论=合格”字段；结论只能由确定性计算或人工审核产生。
- 空值与不适用必须区分：`value=null` 且有 `not_applicable_basis` → 不适用；否则 → 缺失。

---

## 4. 计算方法契约（确定性，原始数据入）

实现位置：`backend/app/services/ppap_calc.py`。原则：**只做确定性计算，不生成任何测量数值**。

| 计算 | 方法 | 输入 | 输出 | 边界处理 |
|---|---|---|---|---|
| 平均极差法 GRR（MSA） | AIAG 平均极差法：`R̄`、`X̄_diff`、`EV=K1·R̄`、`AV=√((K2·X̄_diff)²−EV²/(n·r))`、`GRR=√(EV²+AV²)`、`PV=K3·Rp`、`TV=√(GRR²+PV²)` | 评价人 × 零件 × 测次的三维原始值、公差 | `EV/AV/GRR/PV/TV`、`%EV/%AV/%GRR/%PV`、`ndc` | 缺任一维度 → `INSUFFICIENT_DATA`；`AV` 内根号为负 → 记为 0 并显式标注 `av_clamped=true`；`TV=0` → `%GRR` 置 `null` 并标 `ZERO_VARIATION` |
| 过程能力 | 样本标准差（`n−1`）：`Cp=(USL−LSL)/(6σ)`、`Cpk=min((USL−μ)/(3σ),(μ−LSL)/(3σ))`、`Pp/Ppk` 用总体标准差 | 数值序列、LSL、USL | 计数、均值、σ、`Cp/Cpk/Pp/Ppk` | `n<2` → `INSUFFICIENT_DATA`；`σ=0` → `ZERO_VARIATION`，`Cp/Cpk` 置 `null`；只有单侧规格 → 只给 `Cpk`/`Ppk`，`Cp` 置 `null` |
| 尺寸判定 | 与规格逐点比较 | 实测值、LSL、USL、单位 | `OK`/`NG`/`null` | 单位不一致 → `CONFLICT` 且不改判定；任一为空 → `null`（**不填“合格”**） |
| 规格一致性 | 检查 `LSL ≤ USL`、单位一致、值域合理 | 特性与测点输入 | 冲突列表 | `LSL>USL` → `BLOCKER` |
| 汇总核对 | 检查是否混入汇总值/均值 | 输入序列 | `SUSPECT_AGGREGATE` | 检测到与 `mean` 完全相同的重复值达到阈值时仅**提示**，不自动剔除 |

**验收阈值不在代码中写死。** 判定逻辑读取 `ppap_acceptance_rules`（配置，默认空）。默认空时：

- `acceptance_state = "NEEDS_CONFIRMATION"`，附带 `rule_source = null`。
- 计算出的指标照常展示，但**不产生“合格/不合格”结论**，也不阻断/放行审核（除结构性阻断项外）。

在 UI 与导出中另附 `reference_bands`（AIAG MSA 4th ed. 公开指引的 %GRR 分段），字段 `source` 明确写为“AIAG 公开指引参考值——未经本项目业务规则确认，仅作参考”。这是把已知公开标准作为**参考信息**展示，不是把未确认阈值当判定依据。

---

## 5. 一致性检查契约（设计文档 §5.3）

检查器：`backend/app/services/ppap_checks.py`。结果写入 `ppap_issues`，带 `severity`、受影响 `element_id`/`field_path`、`basis`（依据）、`status`。严重程度：`BLOCKER` / `MAJOR` / `MINOR` / `INFO`。**只有 `BLOCKER` 阻断批准**（AC09）。

| 检查 ID | 内容 | 严重程度 |
|---|---|---|
| `PPAP-CHK-BASE-001` | 零件号/名称/图纸版本/工程变更等级在任务与各条目间一致 | BLOCKER |
| `PPAP-CHK-SIDE-001` | 左右件数据未混用 | BLOCKER |
| `PPAP-CHK-MAT-001` | 材料规格在基础信息、材料证明、采购物料主数据间一致 | MAJOR |
| `PPAP-CHK-OP-001` | 工序编号在流程图、PFMEA、控制计划间一一对应 | BLOCKER |
| `PPAP-CHK-CHAR-001` | 特殊特性编号在特殊特性清单、PFMEA、控制计划、检验表间对应 | BLOCKER |
| `PPAP-CHK-UNIT-001` | 单位转换、上下限、测量值、结论一致 | MAJOR |
| `PPAP-CHK-MSA-001` | MSA 评价人数/零件数/测次数与所选方法要求一致 | MAJOR |
| `PPAP-CHK-STUDY-001` | 过程研究样本范围正确，未混入汇总值 | MAJOR |
| `PPAP-CHK-VER-001` | 未混用其他零件/其他版本的检验数据 | BLOCKER |
| `PPAP-CHK-EVID-001` | 必需证据、客户确认、审核条件齐备 | BLOCKER |
| `PPAP-CHK-TPL-001` | 模板公式、外链、嵌入对象、打印布局处理正确 | INFO |
| `PPAP-CHK-REQ-001` | `not_applicable` 条目必须给出依据 | BLOCKER |

“错误数为零”**不等于**“完整资料已读取”，因此任务状态另带 `coverage` 字段：已读取来源数 / 声明来源数 / 读取失败数 / 截断标记。

---

## 6. 权限与审核规则契约

`identity.ROLE_REQUIREMENTS` 新增（全部映射到 `quality_manager`，与现有质量能力一致；**不擅自升级为岗位分离**）：

| capability | 角色 | 说明 |
|---|---|---|
| `ppap_task_start` | quality_manager | 启动任务 |
| `ppap_evidence_collect` | quality_manager | 请求自动取证 |
| `ppap_input_confirm` | quality_manager | 确认/补录结构化输入 |
| `ppap_compile` | quality_manager | 请求编制 |
| `ppap_check` | quality_manager | 运行一致性检查 |
| `ppap_review_submit` | quality_manager | 提交审核 |
| `ppap_review_decide` | quality_manager | 批准/退回 |
| `ppap_archive` | quality_manager | 归档 |
| `ppap_issue_resolve` | quality_manager | 处理缺项/冲突 |

规则：
1. 所有持久化端点同时要求 `require_real_write_access`（项目登录 Cookie）+ `require_real_human_identity`（必须是项目会话，不接受上游服务账号）。
2. 操作者身份**只**取自已解析的项目会话；请求体里的 `requested_by` / `reviewer` 仅作兼容提示，与会话身份不一致即 403（复用 `_actor_for_request`）。
3. **编制人与审核人分离尚未确认**（§14.6）。当前实现：同一账号可先提交再批准，但每次决定都独立写入 `ppap_reviews` 且带 `reviewer` + `reviewer_role` 留痕；当任务上的 `author` 与 `reviewer` 相同时，审核记录附加 `separation_warning: "author_reviewer_same_account"`，页面与导出均展示该提示。不静默宣称已满足岗位分离。
4. 只读查询（任务列表、状态、缺项解释、版本、下载）要求 `require_real_identity`（项目会话或已配置的上游身份）。**文件下载**额外校验：文件路径必须落在配置的 PPAP 存储根目录内，且内容哈希与 `ppap_artifacts.content_hash` 一致。
5. 问答能力目录**只增加只读技能**（`read_only is True` + `requires_approval is False`）。保存草稿、注册文件、变更业务状态一律走上述受控端点，不进目录、不放松目录校验。
6. 归档动作由显式端点触发；**不新增任何自动归档/自动批准**。
7. 不因引入 PPAP 修改发运门禁、NCR 关闭条件或既有质量审批链（设计文档 §9）。

---

## 7. 编制运行与幂等契约

- 每次编制生成一条 `ppap_compile_runs`，记录 `input_snapshot_hash`、`template_id`、`template_version`、`rule_version`、`steps_json`（有序步骤与状态）、`call_chain_json`（真实 AIP 调用记录）、`error_json`。
- `input_snapshot_hash` = SHA-256(任务业务对象 + 全部输入 + 全部证据 `content_hash` 排序后)。**同一 `(task_id, input_snapshot_hash, template_id, template_version, rule_version)` 的重复编制请求返回既有运行，不重复生成产物。**
- 请求级幂等：客户端可传 `Idempotency-Key` 头，服务端以 `(task_id, request_key)` 去重，命中则返回既有结果并标 `idempotent_replay=true`。
- 批准后的产物版本冻结（`frozen_at`），后台编制**不得**覆盖；变更产生新版本，旧版本保留（AC10）。
- 上游读取结果按来源分三态：`OK` / `EMPTY`（合法空结果）/ `ERROR`（401、超时、解析失败）。`EMPTY` 不得渲染成“资料齐全”，`ERROR` 不得渲染成“没有问题”（设计文档 §7）。

---

## 8. 三态清单汇总

### 8.1 已确认（可直接实现）

1. 现有 ERP/MES 只读接口清单及其返回结构（§1.2），PPAP 取证复用它们。
2. 项目登录身份、审批门禁、能力目录只读约束、审计留痕机制（已核对代码）。
3. PPAP 数据结构：任务 / 条目 / 证据 / 结构化输入 / 编制运行 / 产物版本 / 缺项冲突 / 审核记录（迁移 `ppap_task_and_compilation`）。
4. 项目自定义内部模板的 22 个条目、字段映射与左右件隔离规则。
5. 确定性计算算法（平均极差法 GRR、Cp/Cpk/Pp/Ppk）与边界行为。
6. 一致性检查项与其严重程度。
7. 迁移头为 `e4f1a9c2d7b0`，新迁移以其为 `down_revision`。

### 8.2 缺数据（不阻断实现，但阻断相关条目成为“可批准”）

| 缺失项 | 影响的条目 | 当前行为 |
|---|---|---|
| 客户模板原件（条目扩展、字段映射、打印布局） | 全部模板相关条目 | 使用项目自定义模板并在产物显著标注 `project_defined` |
| 产品/图纸/工艺/材料基准 | 设计记录、DFMEA、流程图、PFMEA、控制计划 | 流程图读实际工序快照；PFMEA/控制计划输出结构化草稿并标“待工程确认” |
| 实际原始检测数据 | 尺寸结果、材料/性能结果、MSA、初始过程研究 | 条目 `WAITING_DATA` + 缺项待办，导出留空，**不填“合格”** |
| 客户工程批准 / AAR / 实验室资质原件 | 对应 reference 条目 | `WAITING_DATA`，需人工上传原件后置为已取得 |
| 实物流转记录（样品、标准样件、检具） | 三个 physical 条目 | 仅登记记录，不因生成表格而视为已提供 |
| 客户特殊要求原件 | 客户特殊要求条目 | `WAITING_DATA` |

### 8.3 需要业务确认（阶段 A 结束时一次性提出，见 §9）

1. 第一套正式采用的客户模板及条目适用规则，生成对象所属的业务场景。
2. 产品/图纸/工艺/材料基准、实际原始检测数据，以及它们的确认责任。
3. MSA、过程能力和风险评级的方法、阈值、样本要求及版本。
4. PPAP 的客户提交原因、自动触发规则，以及已有有效包的引用/重新提交策略。
5. 哪些质量异常或偏差影响内部审核、是否存在客户批准的例外。
6. 岗位权限、审核人员及是否需要编制人与审核人分离。
7. 文件访问、备份、保存期限及客户处理记录的取得方式。

### 8.4 已按“不猜测”处理的行为（暂停点）

| 暂停点 | 状态 |
|---|---|
| 客户模板条目与字段映射 | 用项目自定义模板代替，**不**伪装成客户模板 |
| MSA / 过程能力 / 风险评级的合格判定 | 计算出指标但 `NEEDS_CONFIRMATION`，不产生合格结论 |
| PFMEA 严重度/发生度/探测度评分与 RPN 阈值 | 仅结构化草稿 + 待工程确认，不套用其他零件的评分 |
| 自动触发 PPAP 任务 | **未实现**（只支持人工授权启动），等待 §14.4 |
| 内审阻断的质量异常范围 | 仅登记证据与状态，不自动判定阻断，等待 §14.5 |
| 编制人与审核人分离强制 | 已留痕 + 提示，**不**强制，等待 §14.6 |
| 文件保存期限与清理策略 | 未实现自动清理，等待 §14.7 |
| 客户处理记录（提交/退回/接受）登记 | 仅提供人工登记入口，无自动获取，等待 §14.7 |

---

## 9. 一次性提出的关键问题（设计文档 §14）

> 以下 7 个问题在阶段 A 结束时一次性提出。在取得答复前，上表 §8.4 的条目保持暂停；其余工作（任务、取证、结构、编制引擎、计算、检查、导出、审核归档闭环）继续独立推进。

1. **客户模板**：第一套正式采用的客户模板是哪一份？其条目的适用性规则（提交/保留/按要求提供/不适用）如何判定？该模板对应的业务场景（量产前新品、工程变更、年度复评、客户指定）是什么？
2. **工程与检测基准**：产品/图纸/工艺/材料的基准文件由谁提供并确认？实际原始检测数据（尺寸、材料、性能、MSA、过程研究）由哪个岗位、以什么格式、多久提供一次？确认责任人是哪一位？
3. **计算方法与阈值**：MSA 采用哪种方法（平均极差法/ANOVA）？`%GRR` 的接收准则与样本要求（评价人数、零件数、测次数）是什么？过程能力用 `Cp/Cpk` 还是 `Pp/Ppk`、门槛值多少？PFMEA 的风险评级方法（AIAG-VDA AP 或 RPN）及其阈值是什么？以上各项的版本号？
4. **提交触发与复用**：PPAP 的客户提交原因清单是什么？哪些业务事件应自动触发任务创建（还是维持纯人工启动）？客户已有有效资料包时，引用与重新提交的判定规则是什么？
5. **质量异常与内审**：哪些质量问题状态、偏差或让步（含客户批准的例外）会影响 PPAP 内部批准？客户批准的例外如何登记与引用？
6. **权限与人员**：各岗位（报价/采购/跟单/质量文档）对 PPAP 的操作权限如何细分？审核人是谁？是否强制编制人与审核人分离（若强制，需要几位账号、如何配置角色）？
7. **文件与留存**：PPAP 文件与生成包应存放在哪个受控目录？备份策略与保存期限？客户处理记录（提交、客户退回、客户接受）如何取得并登记？

---

## 10. 本阶段不做的事（明确边界）

- 不修改发运门禁规则、NCR 关闭条件、既有质量审批链。
- 不新增 `PPAP 全部批准才可发运` 之类的自动条件。
- 不把生成接口标成只读以进入能力目录，也不放宽目录校验。
- 不在仓库中提交原始企业资料、真实生成包或任何凭据。
- 不实现客户模板自动识别、自动触发、自动批准、自动归档。
- 不生成测量数值、评分、签字或“合格/批准”结论。
