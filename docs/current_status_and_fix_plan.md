# 四智能体项目当前状态与整改计划

更新时间：2026-09-28（阶段 1 只读关联完成）

项目路径：`E:\competition\汽车零部件工厂智能体开发`

## 0. 这份文档的用途

这是当前项目唯一的开发对照文档。

后续每完成一个阶段，必须更新本文件中的：

- 实际完成情况
- 实际验证结果
- 未解决问题
- 下一阶段任务
- 验收证据

没有实际运行结果的内容，只能写成“计划”或“待验证”，不能写成“已完成”。

> 本文后续追加的阶段记录优先于早期快照；早期章节保留历史验收上下文。

## 1. 用户的最终要求

用户所说的“demo”是本地可运行版本，但业务数据必须来自本地已经部署的 ERP 和 MES。

目标是连接真实 ERP/MES，跑通四个独立 Agent 的业务流程：

1. 报价 Agent
2. 采购 Agent
3. 跟单 Agent
4. 质量文档 Agent

固定 fixture、MockERP、MockMES、`DEMO_*` 和 `synthetic_demo_only` 只能用于开发测试，不得作为真实业务主流程或验收依据。

## 2. 当前代码和运行状态

### 2.1 已确认完成

- ERPNext 客户读取可用
- ERPNext 物料读取可用
- ERPNext BOM 读取可用
- ERPNext 价格读取可用
- ERPNext 库存读取可用
- ERPNext 登录检查可用
- OpenMES 质量问题读取可用
- OpenMES 健康检查可用，返回 200
- OpenMES 工单列表可用，实际返回 4 条记录（其中 `DEMO_WO_001` 是明确标注的演示记录）
- OpenMES 工单详情和整体进度接口已可调用
- OpenMES 生产完工接口可访问，当前返回 0 条
- 本地 AIP RPC 端点已注册四个 Agent
- 真实业务页面已加入前端并设为默认页面
- 报价分析、报价审批、ERP 销售订单草稿接口已经存在
- 采购分析、采购审批、ERP 采购订单草稿接口已经存在
- 跟单、质量资料汇总、发运门禁接口已经存在

### 2.2 当前仍未完成

- ~~真实 ERP 订单与 MES 工单的关联~~ 已完成（字段确认+写入+LINKED 验证，见 3.4）
- ~~报价 Agent 占位计算~~ 已删除并接入真实数据（见 3.5.1/3.5.3）；数量折扣表去留待用户确认
- ~~采购供应商价格和交期模拟~~ 已删除并接入真实数据（见 3.5.1）；供应商业务数据补录待用户决策
- 采购 Agent 前端页面尚未接入真实业务页面（P1-3）
- 质量文档还没有真实文档归档和真实放行写回闭环（P1-4）
- OpenMES 质量放行/SOP/Control Plan 等文档接口不存在，需明确记录"当前系统不支持"
- 报价、采购方案、审批记录使用内存字典，重启后丢失（P2-1，本轮验证已实际触发一次）
- 跟单 Agent 的 ETA 仍是交期占位（进度为 0 时无速率可用；待积累真实进度数据后实现）
- 旧的场景页面和后台仍然保留 Mock 主流程，需要明确隔离为测试入口

## 3. 已验证的实际结果

### 3.1 ERPNext

已实际读取：

- 客户：上汽集团
- 物料：`BD-2401`
- BOM：`BOM-BD-2401-001`
- BOM 子项：4 个
- 库存：`CI-RAW`、`M10-BOLT` 等

返回数据带有：

```text
authority=ERPNext
```

### 3.2 OpenMES

已实际读取：

- 健康检查：200，`health=ok`
- 工单列表：4 条 OpenMES 记录，其中 3 条业务样例工单、1 条 `DEMO_WO_001` 演示工单
- 工单示例：`WO-2026-001`、`WO-2026-002`、`WO-2026-003`、`DEMO_WO_001`
- 质量问题：2 条
- 生产完工：接口可访问，当前 0 条

当前已确认的工单接口结果：

```text
/api/integrations/openmes/check              200
/api/integrations/openmes/work-orders        200，4 条
/api/mes/work-orders                         200，4 条
```

工单返回数据带有 `authority=OpenMES` 和 `data_source=openmes_api`。

### 3.3 测试

当前测试（含关联查询与写入路径共 20 个）：

```text
20 passed
```

前端构建（2026-09-28 复验，EPERM 未再复现）：

```text
npm run build 成功（29 modules transformed, built in 475ms）
```

### 3.4 阶段 1：真实 ERP 订单 ↔ MES 工单关联（只读）——已完成关联字段确认与只读验证

### 3.5 阶段 2/3 前置：报价与采购 Agent 真实化（2026-09-28）

#### 3.5.1 已删除的占位逻辑（逐项）

| 原占位 | 位置 | 替换为 |
|--------|------|--------|
| BOM 子项固定成本 `qty*10` | analyze_quotation | 真实 Buying Item Price（含记录编号），缺价 → `missing_data` 明确列出 |
| 固定加成 `×1.3` | analyze_quotation | 删除。无 Selling 价时按真实 BOM 成本报价（`pricing_basis=erp_bom_cost_no_markup`，未加成，建议人工确认加成率） |
| 无价格时固定估算 | analyze_quotation | `status=DATA_MISSING`，拒绝出价；SO 草稿创建同步拒绝 |
| 固定交付期 `7`/`15+2×n` 天 | analyze_quotation | 结构化 `delivery_estimate`：库存足够→真实库存；不足→真实 MES 工单 due_date 推算；无数据→`basis=missing` 明确报缺失 |
| 数量折扣表（伪装系统计算） | llm_quotation.py | **已删除**（用户决策）：报价一律按 ERP 真实价格原价；`quantity_discount_source=disabled_pending_business_rule`；折扣规则确认后恢复 |
| 采购价 `×0.7` 系数、固定 `50` 元 | compute_net_requirement | 只用真实 Buying 价格记录；缺失时 `price_status=missing`、金额留空，`missing_data` 列出 |
| 供应商固定加价系数 `1.0+idx*0.05` | analyze_procurement | 删除。ERP 无供应商特定价格（Item Price.supplier 全空），所有方案共享真实标准价并如实说明 |
| 供应商固定交期 `7+idx*3` 天 | analyze_procurement | 删除。`lead_time_days=None, lead_time_source=missing`（ERP 无 Item Supplier 记录、Item.lead_time_days=0） |
| 推荐理由"综合最优"（编造） | analyze_procurement | 确定性规则 `lowest_total_cost_v1`（总价最低者推荐），理由基于真实数字与规则版本 |
| PO 草稿固定公司 `AutoParts Manufacturing` | 两个 draft 函数 | 真实 Company 记录读取（`company_source=erp_company_record`） |
| PO/SO 草稿固定仓库 `Stores - APM` | 两个 draft 函数 | 真实 Warehouse 记录选择（type 字段优先，名称约定次之；`warehouse_source=erp_warehouse_record`） |
| PO schedule_date 基于模拟交期 | create_erp_purchase_order_from_plan | 有真实交期→today+N；缺失→today 并明确提示"需人工确认后调整" |
| 缺价物料静默入 PO | create_erp_purchase_order_from_plan | 拒绝创建，报出缺价物料清单 |

#### 3.5.2 ERP 真实数据现状（2026-09-28 实测，关联字段调查）

- Item Price：9 条（5 Selling + 4 Buying），`supplier` 字段全空 → 无供应商特定价格
- Item Supplier（物料-供应商关系）：**0 条记录**
- Item.min_order_qty=0、lead_time_days=0、purchase_uom=None、default_warehouse=None（全部未配置）
- BOM 成本字段全 0（total_cost/rate），BOM 币种 INR 异常（测试数据问题，不影响报价——报价用 Item Price 的 CNY）
- Warehouse：5 条真实记录（Stores - APM 等）；Company：AutoParts Manufacturing（唯一）
- Supplier：3 家真实存在，无默认价格表/付款条件

#### 3.5.3 真实流程验证记录（APP_ADAPTER_MODE=real）

| 流程 | 结果 |
|------|------|
| 报价分析 BD-2401×500 | 200：真实 Selling 价 85（erp_selling_price）；交付估算=真实 MES 工单 WO-2026-001 排程 17 天；折扣标注 manual_config_v1 |
| 报价分析 BD-2401×2000 | 200：同上，交付估算仍来自 MES 排程 |
| 采购分析 ×500 | 200：真实结论 NO_SHORTAGE（库存足够，正确） |
| 采购分析 ×2000 | 200：真实缺料 3 项（CI-RAW 1200、M10-BOLT 3000、BRG-6204 2500）；3 个供应商方案单价全部来自真实 Buying 价格记录（mrsgdil3h1/mrsk75j80c/mrspjlrfcs，12.5/0.8/8.2）；交期全部如实 missing；`data_limitations` 4 项明示 |
| 采购审批（选 OPT-2 宁波紧固件） | 200：APR 编号返回，selected_option_id=OPT-2 |
| PO 草稿 | 200：PUR-ORD-2026-00003，回读 verified=True（供应商宁波紧固件，3 行 1200/3000/2500，单价 12.5/0.8/8.2，仓库 Stores - APM 来自真实记录）；schedule_date=今日 + 交期缺失人工确认提示；docstatus=0 |
| SO 草稿 | 200：SAL-ORD-2026-00007，回读 verified=True（rate 80.75=85×0.95 折扣），仓库/公司来自真实记录，docstatus=0 |

测试：27 passed（新增 7 个真实化逻辑测试：真实 BOM 成本无加成、缺价 DATA_MISSING、MES 排程估算、无数据报缺失、缺价不伪造、真实价格方案、缺价拒绝 PO）。
前端：`npm run build` 通过（报价卡片改为 delivery_estimate 结构，缺失时显示"数据缺失"）。

#### 3.5.4 本阶段实际跑通 vs 仍不可用

**已实际跑通（真实数据）**：报价分析（真实原价、无折扣）、交付估算（MES 排程路径）、净需求与缺料计算（含 MOQ 约束）、供应商差异化方案（真实 Item Supplier 关系过滤 + 供应商特定价格 + 物料级交期）、采购审批、SO/PO 草稿创建+回读（schedule_date 来自真实交期）、跟单/质量/门禁读取、ERP↔MES 关联查询与写入。

**仍不可用/受限制**：
1. 报价 DATA_MISSING 路径：当前真实数据中所有成品都有 Selling 价，该路径仅由单元测试覆盖（逻辑正确，真实触发需一个无价物料）
2. 交期为物料级而非供应商级：ERPNext 版本限制（Item Supplier 子表无 lead_time_days 字段），如实标注
3. 数量折扣：已按用户决策删除，待用户提供规则或 ERP 配置价格表后恢复
4. 内存状态重启丢失（P2-1）：本次验证中实际触发——服务重启后 plan 丢失需重跑
5. 供应商推荐规则 lowest_total_cost_v2 是确定性代码规则（全覆盖优先+总价最低），业务上是否适用待用户后续确认

#### 3.5.5 用户决策记录（2026-09-28）与执行结果

**决策 1：数量折扣表 —— 删除（已执行）**

- 用户明确："先删除未经确认的数量折扣，不要把测试用的 9 折等数字用于正式报价……如果没有可用价格，就显示'缺少价格数据，无法自动报价'，不要自行编造原价。以后我提供折扣规则或 ERP 中有对应数据时，再加入折扣功能。"
- 已执行：报价一律使用 ERP 真实价格原价，`quantity_discount_factor=1.0`、`quantity_discount_source=disabled_pending_business_rule`；无价格时 `DATA_MISSING` 并拒绝出价与草稿
- 实测：BD-2401×500 报价单价 = 85.0（原价，此前为 76.50=85×0.9）
- 旧 Mock 场景流程（tools/registry.py、main.py 旧报价端点）的折扣逻辑保留未动（该流程属 P2 隔离范围，不用于真实业务）

**决策 2：供应商业务数据补录 —— 批准（已执行并回读验证）**

`backend/seed_supplier_data.py` 写入真实 ERP（幂等，重复运行跳过已存在记录）：

| 物料 | lead_time_days | min_order_qty | 供应商关系 | 供应商特定价格（记录编号） |
|------|---------------|---------------|-----------|--------------------------|
| CI-RAW | 12 | 100 | 上海铸锻厂、江苏轴承 | 12.5(d5v1dj85c6)、13.4(d62nbu3539) |
| M10-BOLT | 7 | 500 | 宁波紧固件、上海铸锻厂 | 0.8(d630laii6j)、0.86(d63ua5r17b) |
| SEAL-RING | 10 | 100 | 宁波紧固件、上海铸锻厂 | 3.5(d64cslut3v)、3.68(d64b0am952) |
| BRG-6204 | 15 | 50 | 江苏轴承、上海铸锻厂 | 8.2(d65qrd6fks)、8.6(d66qj4kvr0) |

回读验证一致。已知版本限制：本 ERPNext 的 Item Supplier 子表无 lead_time_days 字段（实测 417），交期存储为物料级 Item.lead_time_days。

#### 3.5.6 采购 Agent 差异化方案真实验证（数据补录后）

#### 3.6 第 1/2 项：持久化与采购前端（2026-09-28）

**3.6.1 数据库持久化（用户优先级 1）**

- 新增 3 张表：`real_quotations` / `real_approvals` / `real_procurement_plans`（Alembic 迁移 `d11a1a04ea20_real_business_state`，已 upgrade head）
- `backend/app/services/real_order.py`：三个内存字典全部替换为数据库读写（save/get/list），审批校验器读数据库
- **重启验证（实测）**：
  1. 创建报价 QUO-73E16DC603AC + 审批 APPR-C3072D79B26F + 方案 PROC-FB9A1D5C5119
  2. 停止 uvicorn 进程，重新启动
  3. 三条记录全部可查（状态/审批人/金额完整）
  4. 用重启前的审批编号创建 ERP 草稿成功：SAL-ORD-2026-00008，回读 verified=True（审批门禁跨重启有效）
- 测试：28 passed

**3.6.2 采购前端（用户优先级 2）**

- `frontend/src/RealBusinessPage.tsx`：流程扩展为 8 步（新增 4 采购分析 / 5 方案审批 / 6 PO 草稿），新增类型、状态与回调；采购分析按钮要求先创建 SO 草稿
- 采购分析面板展示：缺料清单（毛需求/库存/净需求/采购数量+MOQ 上调标注/交期/供应商）、供应商方案卡片（总价/交期/覆盖度/推荐徽标/每行价格依据+价格记录号/推荐理由）、数据来源与限制（如实标注）、证据列表
- `frontend/src/styles.css`：新增 proc-table / supplier-option-card / data-limitations 等样式
- **浏览器实测**（dev server 5173 + 后端 9000，全流程点击）：
  - 报价 2000 件 → 单价 CNY 85（原价）、交期 17 天（真实 MES 排程）✓
  - SO 草稿 SAL-ORD-2026-00009 回读确认 ✓
  - 采购分析：缺料 3 项表格 + 3 个方案卡片（上海铸锻厂 3/3 推荐 39080、宁波 1/3、江苏 2/3），每行价格记录号（d5v1dj85c6 等）与交期（15/7/15 天）✓
  - 批准 OPT-1 → PO 草稿 PUR-ORD-2026-00005：供应商/交期 2026-10-13（真实 lead_time 15 天）/回读 ✓ 已确认 ✓
  - 截图证据：`gui-test-screenshots/t2_procurement_panel.png`、`t2_quotation_stepper.png`
- 测试中发现并修复：批准后未推进到第 6 步（缺 `setStep(6)`）；修复后重测通过
- 环境修复：dev server 曾连接 9001 端口的过期旧后端（显示旧折扣价格），已终止旧进程并新增 `frontend/.env.local` 固定 API 地址为 9000

BD-2401×2000（缺料 3 项：CI-RAW 1200、M10-BOLT 3000、BRG-6204 2500；SEAL-RING 库存恰好够→不缺，真实判断）：

- 方案按 Item Supplier 真实关系过滤：无关供应商不生成方案；上海铸锻厂覆盖 3/3（唯一全覆盖）、宁波紧固件 1/3（M10-BOLT@0.8 特定价）、江苏轴承 2/3（CI-RAW@13.4、BRG-6204@8.2）
- 每行价格携带真实 Item Price 记录编号，price_basis=supplier_specific_price
- 交期来自 Item.lead_time_days（max(12,7,15)=15 天，真实数据）
- 推荐规则 lowest_total_cost_v2：优先覆盖全部缺料 → 推荐上海铸锻厂（39080 CNY，唯一全覆盖）
- 审批 → PO 草稿 PUR-ORD-2026-00004：schedule_date=2026-10-13（今日+真实交期 15 天），回读 verified=True，docstatus=0，行项价格 12.5/0.86/8.6 与供应商特定价一致

MOQ 约束已接入：净需求 < MOQ 时按 MOQ 上调（`qty_basis=min_order_qty`），由单元测试覆盖（净需求 100 → 订购 500）。

#### 3.4.1 关联字段确认结论（基于源码与真实数据，非推断）

正式关联字段：`OpenMES work_orders.customer_order_no`（nullable, string, max 100）。

源码证据：

- `services/OpenMes/backend/routes/api.php`：`POST /api/v1/erp/work-orders/import`（X-Api-Key + scope `erp:orders:import`），官方 ERP→MES 工单导入通道
- `services/OpenMes/backend/app/Http/Requests/Api/V1/Erp/ImportWorkOrdersRequest.php`：payload 显式包含 `orders.*.customer_order_no`
- `services/OpenMes/backend/app/Http/Requests/Api/V1/StoreWorkOrderRequest.php` 与 `UpdateWorkOrderRequest.php`：均接受 `customer_order_no`
- `services/OpenMes/backend/app/Services/CsvImport/WorkOrderImportService.php`：`update_or_create` 策略可回填已有工单的该字段
- `services/OpenMes/backend/app/Http/Controllers/Api/V1/Erp/ProductionExportController.php`：完工导出原样返回 `customer_order_no`

产品主数据级标识（仅信息性，不构成订单关联）：OpenMES `product_type.external_system="erpnext"` + `external_code`（BD-2401/SK-3401/TS-4501 有值，DEMO_BRACKET_001 为 null）。

真实数据现状（2026-09-28 读取）：

- OpenMES 4 个工单的 `customer_order_no` 全部为 null（种子创建时未填）
- ERPNext 5 张销售订单全部是 BD-2401：00001(500件,交期2026-10-15)、00002(100,09-28)、00003(500,10-15)、00004(100,09-28)、00005(100,09-28)
- ERPNext Work Order doctype 存在但 0 条记录（未使用）
- 歧义已实证：WO-2026-001（BD-2401×500，due 2026-10-15）同时匹配 00001 和 00003 两张订单，因此产品编码+数量+交期不能作为正式关联依据

#### 3.4.2 修改文件

| 文件 | 修改 |
|------|------|
| `backend/app/adapters/erp/base.py` | 协议新增 `list_sales_orders` / `get_sales_order` |
| `backend/app/adapters/erp/erpnext_adapter.py` | 实现两方法；404 才返回 found=False，连接错误直接抛出 |
| `backend/app/adapters/erp/mock.py` | Mock 实现同方法（authority=MockERP, synthetic_demo_only） |
| `backend/app/adapters/mes/base.py` | 协议新增 `get_work_orders_strict` |
| `backend/app/adapters/mes/openmes_adapter.py` | `_map_work_order` 新增 `customer_order_no`/`product_external_code`/`product_external_system`；新增 `get_work_orders_strict`（连接失败抛异常而非返回空列表） |
| `backend/app/adapters/mes/mock.py` | 同上 Mock 版 |
| `backend/app/services/order_linkage.py` | 新增：关联查询服务（只按 customer_order_no 精确匹配） |
| `backend/app/main.py` | 新增 2 个只读 GET 路由（见 3.4.3） |
| `backend/tests/test_order_linkage.py` | 新增 8 个测试（已关联/未关联/产品编码不等于关联/ERP 不存在/ERP 断连/MES 断连/数量不一致仅作证据/Mock 契约） |
| `backend/investigate_linkage.py` | 新增：只读调查脚本（拉取两边原始记录比对字段） |

#### 3.4.3 新增接口与真实验证结果（APP_ADAPTER_MODE=real，端口 9000）

- `GET /api/real-orders/erp/sales-orders`：列出真实销售订单供选择
- `GET /api/real-orders/erp/sales-orders/{order_id}/mes-link`：订单↔工单关联查询（只读，含证据）

关联接口行为约定：

1. 正式关联仅按 `customer_order_no == ERP 销售订单号` 精确匹配
2. 未匹配时返回 `status=NOT_LINKED`、`message="未建立关联"`、`not_substituted=true`，并附 `candidates_for_manual_review`（产品编码一致工单，显式标注 `not_an_association=true`）
3. ERP/MES 连接失败返回 502 明确报错（不用空结果冒充"未建立关联"，不回退 Mock）
4. ERP 订单不存在返回 404 且不查询 MES

实测记录（全部只读，未写入任何真实系统）：

| 接口 | 状态码 | authority | 结果 |
|------|--------|-----------|------|
| `GET /api/erp/customers/上汽集团` | 200 | ERPNext | found |
| `GET /api/erp/items/BD-2401` | 200 | ERPNext | found |
| `GET /api/erp/items/BD-2401/bom` | 200 | ERPNext | BOM-BD-2401-001 |
| `GET /api/erp/inventory?item_codes=BD-2401,CI-RAW` | 200 | ERPNext | 1 条（当前真实库存状态） |
| `GET /api/real-orders/erp/sales-orders` | 200 | ERPNext | 5 张订单，data_source=erpnext_api |
| `GET /api/integrations/openmes/work-orders` | 200 | OpenMES | 4 条 |
| `GET /api/integrations/openmes/work-orders/2` | 200 | OpenMES | WO-2026-001, customer_order_no=None |
| `GET /api/mes/work-orders` | 200 | OpenMES | 4 条（已含 customer_order_no 字段） |
| `GET /api/mes/work-orders/2/progress` | 200 | OpenMES | 整体进度 |
| `GET /api/mes/quality-records` | 200 | OpenMES | 2 条 |
| `GET /api/real-orders/erp/sales-orders/SAL-ORD-2026-00001/mes-link` | 200 | erp=ERPNext, mes=OpenMES | `NOT_LINKED`："未建立关联"，如实说明 4 个工单 0 个填写 customer_order_no；候选 WO-2026-001 标注 product_code_only + not_an_association |
| `GET /api/real-orders/erp/sales-orders/SAL-ORD-2026-00003/mes-link` | 200 | 同上 | 同上 |
| `GET /api/real-orders/erp/sales-orders/SAL-ORD-2026-99999/mes-link` | 404 | ERPNext | "ERP 中不存在销售订单 …，未查询 MES" |

#### 3.4.4 阶段 1 结论

- 关联字段：已确认（`customer_order_no`），只读关联能力已实现并验证
- 关联值：**已建立**——WO-2026-001 → SAL-ORD-2026-00001（用户批准，经官方导入接口回填，回读验证一致），关联接口返回 LINKED
- `DEMO_WO_001` 及演示记录未参与任何关联逻辑
- 后续跟单/质量/门禁/报价/采购流程已基于该关联完成真实验证（见 3.4.6）
- 关联值回填需要人工决策（00001 还是 00003 与 WO-2026-001 对应只有业务方知道）并写入真实 MES，等待用户审批后进行

#### 3.4.5 关联写入与 LINKED 验证（2026-09-28，已获用户批准）

用户决策更新：测试数据无需人工判断订单归属，明确指令
"统一将 WO-2026-001 → SAL-ORD-2026-00001，按照项目已有的 MES 写入方式完成关联，
然后执行只读验证，确认 NOT_LINKED 变为 LINKED，并继续测试后续流程"。

**使用的 MES 写入方式（项目已有，未编造接口）**：

- `POST /api/v1/erp/work-orders/import`（X-Api-Key + scope `erp:orders:import`，strategy=`update_or_create`）
- 源码依据：`services/OpenMes/backend/routes/api.php`、`ImportWorkOrdersRequest.php`、`WorkOrderImportService.php::updateExisting()/optionalErpFields()`

**执行记录**（脚本 `backend/link_work_order.py`，审批依据已写入返回结果）：

- API Key `agent-erp-v2` 已含 `erp:orders:import` scope，未变更凭据
- 导入 payload：order_no=WO-2026-001, line_code=DEMO_LINE_01, product_type_code=BD-2401, planned_qty=500.0, priority=5, due_date=2026-10-15, customer_order_no=SAL-ORD-2026-00001（当前值原样回传，防止覆盖）
- OpenMES 返回：HTTP 200，`updated=1, errors=[]`
- **回读验证**：customer_order_no=SAL-ORD-2026-00001，数量 500.00、交期 2026-10-15、状态 ACCEPTED 均未被改动
- authority：erp=ERPNext，mes=OpenMES

**是否写入真实系统**：是（真实 MES 一条工单的 customer_order_no 字段）。回读一致。
**只读验证结果**：

- `backend/verify_order_link.py WO-2026-001` → `[已关联] WO-2026-001 → SAL-ORD-2026-00001，数量一致 | 交期一致`
- `GET /api/real-orders/erp/sales-orders/SAL-ORD-2026-00001/mes-link` → HTTP 200，`status=LINKED`，证据链完整（双端记录号、匹配字段值、数量/交期一致性核验、来源系统）

**新增写入能力代码**：

- `backend/app/adapters/mes/openmes.py`：`import_erp_work_orders`（客户端）
- `backend/app/adapters/mes/openmes_adapter.py`：适配器透传 + `_map_work_order` 增加 `line_code/product_type_code/description`
- `backend/app/adapters/mes/base.py`：协议新增 `import_erp_work_orders`
- `backend/app/adapters/mes/mock.py`：Mock 导入实现
- `backend/app/services/order_linkage.py`：`link_work_order_to_erp_order`（强制审批参数、DEMO 隔离、当前值回传、回读验证）
- `backend/link_work_order.py`：一次性执行脚本（含 scope 检查）
- `backend/tests/test_order_linkage.py`：新增 5 个写入路径测试

#### 3.4.6 后续流程真实验证（2026-09-28，APP_ADAPTER_MODE=real）

| 流程 | 接口 | 状态码 | 结果 |
|------|------|--------|------|
| 跟单 Agent | `GET /api/real-orders/mes/track/2` | 200 | OpenMES：WO-2026-001 完成率 0%、ETA=交期、风险=进度偏低+2 个未关闭质量问题 |
| 质量文档 Agent | `GET /api/real-orders/quality/package/2` | 200 | 门禁未通过（如实）：2 个未关闭问题，SOP/Control Plan 缺失（OpenMES 无此类文档） |
| 发运门禁 | `GET /api/real-orders/ship-gate/2` | 200 | can_ship=False，三重阻断（未审批/质量门禁未通过/进度 0%<90%） |
| 报价分析 | `POST /api/real-orders/quotation/analyze`（上汽集团/BD-2401/500） | 200 | 真实证据：价格记录 mrrhhgs4s8 单价 85、BOM 4 子项、4 条库存；单价 76.50（数量折扣 0.9）、总价 38250 |
| 报价审批 | `POST /api/real-orders/quotations/QUO-845E0B1F9BBD/approve` | 200 | APPR-95A69598A15D |
| ERP 销售订单草稿 | `POST /api/real-orders/erp/draft/from-quotation` | 200 | **写入真实 ERP（草稿）**：SAL-ORD-2026-00006，docstatus=0 未提交，回读 verified=True（客户上汽集团，total 38250） |
| 采购分析（500 件） | `POST /api/real-orders/procurement/analyze` | 200 | 真实结论 NO_SHORTAGE（库存充足，无需采购）——真实数据的正确结果 |
| 采购分析（2000 件） | 同上 | 200 | 真实缺料 3 项：CI-RAW 1200、M10-BOLT 3000、BRG-6204 2500；3 个供应商方案（价格/交期仍为模拟占位，见 P1-2） |
| 采购审批 | `POST /api/real-orders/procurement/plans/PROC-77E17739B31A/approve` | 200 | APR-D3FCF8634E6A，选中 OPT-1 上海铸锻厂 |
| ERP 采购订单草稿 | `POST /api/real-orders/erp/draft/po-from-plan` | 200 | **写入真实 ERP（草稿）**：PUR-ORD-2026-00002，docstatus=0，回读 verified=True（供应商上海铸锻厂，3 行缺料 1200/3000/2500，单价来自真实 Buying 价格表 12.5/0.8/8.2） |

写入系统汇总（本次会话）：真实 MES 1 次（customer_order_no 回填，已回读）；真实 ERP 2 次（销售订单草稿、采购订单草稿，均为 docstatus=0 未提交并回读确认，未提交为正式单据）。

### 3.7 第 3/4/5 项：质量接口检查、报价残留清理与全流程验收（2026-09-28）

**3.7.1 质量 Agent OpenMES 接口检查（用户优先级 3）**

源码路由 + 真实调用实测结论：

| 能力 | OpenMES 接口 | 真实数据 | 处理 |
|------|-------------|---------|------|
| 质量问题 / NCR 读取 | `GET /api/v1/erp/quality/issues` | 2 条 | ✓ 已接入（原有） |
| 检验记录 | `GET /api/v1/inspections` | 接口 200，0 条 | ✓ 新接入（inspections 空 → 门禁如实显示） |
| 工程文档（SOP/Control Plan 载体） | `GET /api/v1/work-orders/{id}/engineering-documents` | 接口 200，0 条 | ✓ 新接入（0 条 → SOP/Control Plan 如实判缺失） |
| 质量放行状态 | **未发现订单级放行 API** | — | 仍标注 `NOT_SUPPORTED`（工程文档 release 与检验 disposition 不能直接代表订单级放行） |
| NCR 处置写回 | OpenMES API 路由存在（resolve/close/disposition），本项目尚未接入 | — | 暂不调用；需先完成权限、请求字段和审批门禁验证 |

- `openmes.py` 新增 `list_work_order_engineering_documents` / `list_inspections`；适配器新增 `get_work_order_documents` / `get_inspections`（连接失败抛异常）
- `quality_package` 重写：真实文档/检验数据 + `unsupported_capabilities` 显式列表 + 门禁理由注明"工程文档功能存在但当前无文档记录"
- 前端质量面板新增"当前系统不支持（如实标注，不伪造）"区块
- 实测：`GET /api/real-orders/quality/package/2` → 200，gate=False，open_issues=2，docs=0，inspections=0，unsupported=[质量放行状态, NCR 处置写回]

**3.7.2 报价残留清理（用户优先级 4）**

- 删除 `POST /api/real-orders/erp/draft/sales-order` 端点（使用固定测试数据 BD-2401/500/85.0/Stores - APM 创建草稿，前端未使用）
- SO 草稿交期保护：报价无 delivery_date 时显式拒绝创建（此前默认"今天"冒充交期）
- 审计确认 real_order.py 真实业务链中无残留固定价格/折扣/默认值；`llm_quotation.py` 的折扣函数仅剩 Mock 场景流程引用（P2 隔离范围）
- 测试 28 passed；前端 build 通过

**3.7.3 完整业务流程验收（用户优先级 5）**

APP_ADAPTER_MODE=real，一次性脚本（acceptance_result.json）：

| # | 环节 | 接口 | 状态码 | 结果 |
|---|------|------|--------|------|
| 1 | ERP↔MES 关联 | `GET /api/real-orders/erp/sales-orders/SAL-ORD-2026-00001/mes-link` | 200 | LINKED → WO-2026-001 |
| 2 | 跟单 | `GET /api/real-orders/mes/track/2` | 200 | 完成率 0%，2 个质量风险 |
| 3 | 报价 | `POST /api/real-orders/quotation/analyze`（2000 件） | 200 | erp_selling_price 85 元原价，交付=真实 MES 排程 17 天 |
| 4 | 报价审批 | `POST .../quotations/{id}/approve` | 200 | APPR-BEC4EA858A93（已持久化） |
| 5 | SO 草稿 | `POST /api/real-orders/erp/draft/from-quotation` | 200 | SAL-ORD-2026-00011，回读 True |
| 6 | 采购链 | analyze → approve → po-from-plan | 200 | 缺料 3 项 → OPT-1 → PUR-ORD-2026-00006，交期 2026-10-13（真实 lead_time），回读 True |
| 7 | 质量 | `GET /api/real-orders/quality/package/2` | 200 | 门禁未通过（2 个真实未关闭问题 + SOP/ControlPlan 无文档），2 项不支持标注 |
| 8 | 发运门禁 | `GET /api/real-orders/ship-gate/2?quotation_approved=true` | 200 | can_ship=False，正确阻断（质量门禁未通过 + 进度 0%<90%） |

**验收结论**：报价→审批→SO 草稿→采购→审批→PO 草稿全链路真实跑通（全部持久化）；质量与发运门禁基于真实数据正确阻断交付——这是当前真实数据下的正确业务结果（生产未完成、存在未关闭质量问题），不是功能缺失。

### 3.8 第 5 批任务：运行记录持久化 / Mock 区分 / 质量通过场景（2026-09-28，历史记录；最终结果见 3.11）

用户指令 5 项：① Agent 运行记录持久化 ② Mock 与真实入口区分 ③ 质量门禁阻断验证 ④ 质量通过场景（接口核实→补录→重跑）⑤ 双场景验收。
**详细交接指南见 `docs/handoff_2026-09-28.md`**（后续开发以该文档为操作手册）。

#### 3.8.1 ① Agent 运行记录持久化（完成）

- 新表 `real_agent_runs`（Alembic 迁移 `1a1aade41dfd`，已 upgrade head），记录输入/完整结果（含证据链）/错误/起止时间
- 装饰器 `@_agent_run(agent_type, operation)` 套在 10 个真实 Agent 函数上（报价/跟单/质量/门禁/采购/审批×2/ERP 写入×2/关联查询）
- 新接口：`GET /api/real-orders/agent-runs`（摘要列表，可按 agent_type 过滤）、`GET /api/real-orders/agent-runs/{run_id}`（完整记录）
- **重启验证通过**：报价 → 停服重启 → 5 条报价运行记录可查；最新记录 RUN-A7A3F87FAAA3 的 result.evidence 完整列出所用 ERP 记录编号（价格 mrrhhgs4s8、BOM-BD-2401-001、4 条库存）——决策依据可追溯已实证
- 实施中修复：装饰器首次插入漏 `@` 前缀导致记录为空，已修复并重验

#### 3.8.2 ② Mock 与真实入口区分（完成）

- 导航栏 6 个 Mock 页面加黄色 `Mock` 徽标；每个 Mock 页面标题区加橙色横幅"Mock 演示数据——来自固定种子合成场景（synthetic_demo_only），不是真实 ERP/MES 业务结果，真实流程请使用「真实业务」页面"
- `frontend/src/App.tsx`（navigation.mock 标记 + PageHeading mode 属性）、`styles.css`（.nav-mock-badge/.mode-banner）
- 前端 build 通过；浏览器截图确认徽标与横幅可见

#### 3.8.3 ③ 质量门禁阻断（API 级完成；页面级两个前端问题已修复，待复验）

- API 已验证：`GET /api/real-orders/ship-gate/2?quotation_approved=true` → can_ship=False，blocking_reasons=['质量门禁未通过','生产进度不足 (0.0% < 90%)']，原因清晰
- 前端走查中发现并修复两个问题：
  1. 报价表单缺"客户要求交期"必填输入（后端交期保护会拒绝无交期草稿，但表单无输入项）→ 已新增日期输入 + 校验
  2. 采购 NO_SHORTAGE（库存充足）时流程卡死：`analyzeProcurement` 无条件 setStep(5)，而步骤 5/6 面板要求 has_shortage → 无按钮推进。已修复：NO_SHORTAGE 时 setStep(7) 直接进入跟单质量
- 前端 build 通过；**页面级复验待做**（100 件走 NO_SHORTAGE 路径 + 2000 件走缺料路径各一遍并截图）

#### 3.8.4 ④ 质量通过场景 / ⑤ 双场景验收（当时未开始，已由 3.11 完成）

需先核实 OpenMES 写接口（问题关闭/工程文档上传/produced_qty 更新），再补录明确标记的测试数据重跑；操作指南在 handoff 文档 §7.1/§7.2。缺接口则记录为当前不可用。

#### 3.8.5 本批验证快照

- 测试 28 passed；compileall 通过；前端 build 通过
- 后端 9000（real 模式）与前端 5173 运行中；agent-runs 接口可用

## 4. 当前问题清单

### P0：必须先解决

#### P0-1 真实 ERP 订单与真实 MES 工单建立关联（已完成）

- 关联字段确认：`customer_order_no`（见 3.4.1）
- 只读关联接口：已上线并验证（见 3.4.3）
- 关联值写入：WO-2026-001 → SAL-ORD-2026-00001 经官方导入接口回填，回读验证一致（见 3.4.5）
- 关联查询返回 LINKED，证据链完整；找不到关联时明确返回”未建立关联”，不拿其他工单代替
- 产品编码/数量/交期推断已被实证排除作为匹配依据（仅作一致性核验与人工确认候选）

#### P0-2 禁止真实模式悄悄回退 Mock

真实业务模式下，ERP/MES 连接失败必须明确报错；不能让真实页面继续显示 Mock 数据。

需要检查：

- `backend/app/adapters/factory.py`
- `backend/app/main.py`
- 前端真实业务页面

验收：

- `APP_ADAPTER_MODE=real` 时连接失败直接阻断业务
- 页面显示连接错误和来源状态
- 不出现 `MockERP`、`MockMES` 或 `synthetic_demo_only`

### P1：真实业务链必须完成

#### P1-1 报价 Agent 去除占位计算（已完成，见 3.5.1/3.5.3）

固定成本/固定加成/固定生产周期/固定公司仓库均已删除并接入真实数据；
数量折扣表保留但显式标注为人工配置参数（去留待用户确认）。

#### P1-2 采购 Agent 接入真实采购数据（已完成代码层，数据层待用户决策，见 3.5.1/3.5.3）

模拟价格系数、固定交期、固定占位金额均已删除；单价来自真实 Buying 价格记录
（含编号），方案/审批/PO 草稿/回读全链验证通过。ERP 缺少的供应商业务数据
（Item Supplier 关系、供应商特定价格、交期、MOQ）已如实标注 data_limitations，
是否补录测试数据待用户决策。

#### P1-3 接入采购前端

真实业务页面需要补齐：

1. 物料需求清单
2. 缺料数量
3. 供应商方案
4. 方案选择
5. 人工审批
6. ERP 采购订单草稿
7. 草稿回读结果

#### P1-4 质量文档 Agent 形成真实闭环

需要确认 OpenMES 是否存在真实的：

- SOP
- Control Plan
- 检验记录
- NCR 处置
- 质量放行

当前只能读取质量问题和生成本地汇总，不能宣称已经完成质量文档业务闭环。

### P2：系统可持续使用

#### P2-1 业务数据持久化（核心三项已完成，见 3.6）

报价记录、审批记录、采购方案已迁移到 SQLite（real_quotations / real_approvals /
real_procurement_plans 三张表，Alembic 迁移 d11a1a04ea20），重启后数据可查、
审批门禁跨重启有效（实测创建 SAL-ORD-2026-00008）。剩余：ERP 草稿回读结果已嵌在
报价/方案的 data_json 中随之持久化；订单-MES 关联查询为实时只读无需持久化；
Agent 运行记录待后续。

#### P2-2 修复前端构建

2026-09-28 复验：`npm run build` 已通过（EPERM 未再复现，此前可能是文件锁临时占用）。
保留本条目持续观察若干次构建；若复发，排查 `tsconfig*.tsbuildinfo` 与 `vite.config.js` 的写入占用。

#### P2-3 清理和保护凭据

检查未跟踪脚本，尤其是：

- `backend/check_openmes.py`
- `backend/check_erpnext.py`
- `backend/get_openmes_token.py`
- `backend/create_erp_api_key*.py`
- `backend/seed_*.py`

不得在脚本中硬编码 Token、API Key、Secret 或密码。已经暴露的凭据应轮换。

## 5. 真实业务目标流程

第一条必须跑通的垂直链：

```text
真实 ERP 客户/物料
    ↓
报价 Agent 读取真实价格、BOM、库存
    ↓
人工报价审批
    ↓
ERP 销售订单草稿
    ↓
ERP 物料需求
    ↓
真实 MES 工单关联
    ↓
跟单 Agent 读取进度和交期
    ↓
质量文档 Agent 读取真实质量资料
    ↓
人工质量审批
    ↓
真实质量门禁
    ↓
发运判断
```

这条链没有完整跑通之前，不能扩展更多演示场景，也不能宣称四 Agent 已完成。

## 6. 开发顺序

### 阶段 1：真实 ERP 订单到 MES 工单关联（✅ 已完成 2026-09-28）

- ✅ 确认真实关联字段（customer_order_no，见 3.4.1）
- ✅ 新增关联查询（只读接口 + 证据链，见 3.4.3）
- ✅ 处理找不到关联的情况（明确"未建立关联"+人工确认候选，见 3.4.3）
- ✅ 只读验收 + 关联值回填与回读（LINKED 验证，见 3.4.5）

### 阶段 2：报价 Agent 真实化

- 删除固定成本和固定交期占位
- 使用真实价格和库存
- 生成报价证据链
- 审批后创建草稿并回读

### 阶段 3：采购 Agent 真实化

- 确认供应商价格和交期字段
- 删除模拟价格和固定交期
- 完成采购前端
- 审批后创建采购草稿并回读

### 阶段 4：跟单和质量闭环

- 真实工单进度
- 真实质量记录
- 真实质量文档
- 真实质量放行状态
- 发运门禁

### 阶段 5：持久化、构建和安全

- 迁移内存状态到数据库
- 修复前端构建
- 清理硬编码凭据
- 完成端到端验证

## 7. 每阶段必须记录的验收证据

每次改动后更新本文件，记录：

1. 修改文件
2. 使用的真实 ERP/MES 记录编号
3. 实际调用的接口
4. 返回状态码
5. 返回 authority
6. 是否写入真实系统
7. 写入后的回读结果
8. 测试命令和结果
9. 仍然存在的问题
10. 下一步任务

## 3.9 本轮继续开发：质量问题工单范围修正（2026-09-28）

- 发现：OpenMES `GET /api/v1/erp/quality/issues` 返回的是 `work_order_no`，适配器原先只读取 `work_order_id`，且未按当前工单过滤；这会把其他工单的质量问题计入当前工单门禁。
- 修改：`backend/app/adapters/mes/openmes_adapter.py` 先读取当前工单 `order_no`，再只保留同号质量问题；同时合并工单详情中的嵌套问题并去重。缺少工单号或质量接口异常时失败关闭，不把异常当成空质量结果。
- 测试：新增 `backend/tests/test_openmes_quality_scope.py`，覆盖跨工单过滤、嵌套问题去重、缺少工单号和接口异常；与订单关联测试合计 `16 passed`。
- 编译：`compileall` 受现有 `backend/app/adapters/mes/__pycache__` 文件权限锁影响，未完成写入；需清理/解锁缓存后重跑。
- 当前真实验证：WO-2026-001 的质量包仍返回 2 条未关闭问题、SOP/Control Plan 各缺 1 项，质量门禁应保持未通过；本轮未修改 OpenMES 测试数据。
- 重启验证：使用独立 9001 临时实例加载新代码后，WO-2026-001 返回 1 条同工单质量问题（带 `work_order_no=WO-2026-001`），其他工单问题已被过滤；质量门禁仍为 False。原 9000 进程因权限无法停止，未强行处理。
- 质量写回进展（历史记录）：已接入 OpenMES `POST /api/v1/issues/{id}/resolve` 与 `/close` 的客户端适配；后续已补充服务端令牌、两步审批和回读验证，详见 3.9。
- 测试数据库隔离：直接使用 E 盘运行数据库执行全量测试时受到运行进程/权限锁，出现 SQLite 只读错误；复制数据库到临时可写位置后全量测试 `32 passed`，临时文件已删除。该问题不属于业务逻辑失败，但后续应为测试配置固定独立数据库。
- 写入安全门禁：新增 `REAL_WRITE_API_TOKEN` 服务端令牌校验；令牌未配置时质量写回相关接口返回 503，令牌错误返回 403。质量写回仍需经过本地审批记录校验。新增安全测试后，隔离数据库全量测试 `35 passed`。
- 质量写回实测：使用临时令牌和隔离本地审批库，`issue_id=1`（WO-2026-001）通过“申请→批准→resolve→回读”写入 OpenMES；回读 `status=RESOLVED`、`read_back_verified=true`。这是明确标记的 TEST 操作，未修改 ERP、生产数量或工程文档。
- 写回后的真实门禁：`open_issues=0`，`missing_documents=[SOP, Control Plan]`，`quality_gate=false`；发运仍为 `can_ship=false`，阻断原因为质量文档缺失和生产进度 `0% < 90%`。

### 3.10 本轮继续开发：工程文档类型核验与质量门禁复验（2026-09-28）

- 发现：当前运行的 OpenMES 工单冻结快照和 `/api/v1/work-orders/{id}/engineering-documents` 返回文档 ID、文件名、版本和生命周期，但旧运行版本没有返回 `document_type`；因此仅看摘要会把真实 SOP/Control Plan 误判为缺失。
- 修复：`backend/app/adapters/mes/openmes.py` 新增 `GET /api/v1/engineering-documents/{id}` 客户端方法；`openmes_adapter.py` 对缺少类型的冻结文档按 ID 读取详情，使用 OpenMES 返回的真实 `document_type`，不从文件名猜类型。新增契约测试后 `tests/test_real_integrations.py` 为 `10 passed`。
- 真实测试数据：OpenMES 文档 `id=1`（`TEST_SOP_WO2026-001.html`，SOP，released）和 `id=2`（`TEST_ControlPlan_WO2026-001.html`，Control Plan，released）；测试工单 `id=6 / TEST_WO_QUALITY_002` 的工单冻结快照已包含两份文档引用。
- 质量门禁复验：`quality_package(6)` 返回 `open_issues=0`、`documents=2`、`missing_documents=[]`、`quality_gate_passed=true`。`ship_gate_check(6, quotation_approved=true)` 仍正确返回 `can_ship=false`，唯一阻断为生产完成率 `0% < 90%`。
- 当前限制（截至 3.10）：OpenMES 正在运行的服务仍是旧构建，尚未加载 `WorkOrderService.php` 的 `document_type` 快照字段补丁；项目适配器已通过详情接口兼容该版本。生产完工路径已在 3.11 通过官方批次/工序完成接口验证。

### 3.11 本轮继续开发：OpenMES 官方生产完工路径与发运门禁通过（2026-09-28）

- 工艺模板：通过 `POST /api/v1/product-types/2/process-templates` 创建 `TEST_BD2401_QUALITY_FLOW`（template id=2），再通过 `POST /api/v1/process-templates/2/steps` 创建 `TEST_Final_Assembly`（step id=2）。
- 测试工单：通过官方 `POST /api/v1/work-orders` 创建 `TEST_WO_QUALITY_003`（work_order id=7，计划 500 件），随后 `POST /api/v1/work-orders/7/accept` 接受。
- 测试批次与完工：创建 `TEST_LOT_QUALITY_003`（batch id=2），调用 `POST /api/v1/batch-steps/1/start`，再调用 `POST /api/v1/batch-steps/1/complete`，请求 `produced_qty=500`、实际工时 30 分钟；回读工单状态 `DONE`、`produced_qty=500.00`、完成率 100%。
- 四 Agent 门禁复验（隔离本地数据库，仅读取真实 OpenMES）：`quality_gate_passed=true`、未关闭质量问题 0、缺失文档 0；报价审批参数为 true 时，`ship_gate_check(7)` 返回 `can_ship=true`、`production_ready=true`、`blocking_reasons=[]`。
- 本轮结论：报价→采购→跟单→质量文档→质量门禁→发运判断的主链已经有一条真实 ERP/MES 测试数据路径跑通；仍需补页面级验收、检验记录/NCR 完整处置和真实身份权限治理，不能把该 `TEST_` 场景当作生产数据。

### 3.12 本轮继续开发：页面禁止跨订单选择 MES 工单（2026-09-28）

- 发现：页面原先在进入跟单步骤时默认选择工单列表第一条，用户也可以手动选择任意工单。因此即使 ERP 新建的销售订单草稿是 `SAL-ORD-2026-000020`，页面仍可能加载 `TEST_WO_QUALITY_003`（其 `customer_order_no` 仍是其他订单号），造成“门禁通过但不是同一业务订单”的假通过。
- 修复：`frontend/src/RealBusinessPage.tsx` 增加 `customer_order_no` 字段；创建 ERP 销售订单草稿后刷新 MES 工单，并只保留 `customer_order_no == erp_draft_id` 的精确匹配项。没有匹配项时禁用工单选择和加载按钮，明确提示先在 OpenMES 建立正式关联；加载前再次校验，不能通过前端状态绕过。
- 验证：`frontend` TypeScript 检查通过（`npx tsc --noEmit --incremental false`）。由于当前运行中的前端进程占用构建缓存，未强制结束该进程；完整构建使用临时输出目录继续验证。
- 影响：现有 `TEST_WO_QUALITY_003` 只能在其 `customer_order_no` 与当前 ERP 草稿号一致时用于通过场景。系统不再用其他工单替代未建立关联的订单，测试数据需要先通过 OpenMES 官方关联接口回填并回读。

### 3.13 本轮继续开发：测试隔离、失败验证、Agent 运行面板与页面级双场景验收（2026-09-29）

**测试与安全（next_development_plan 阶段四）**

- 新增 `backend/tests/conftest.py`：pytest 全套使用临时目录独立 SQLite（导入 app 前设置 `DATABASE_URL`，`Base.metadata.create_all` 建表，测试结束删除）。验证：全量测试期间 `backend/data/demo.db` mtime 无变化，运行库不被测试触碰。此前"复制业务库跑测试"的做法不再需要。
- 失败验证矩阵补齐：`tests/test_real_integrations.py` 新增 `JsonHttpFailureMappingTests` 4 例——超时（`httpx.ConnectTimeout`→`IntegrationError code=timeout, retryable=true`）、网络中断（`ConnectError`→`network_error`）、远端 500（`remote_http_error`，retryable）、远端 404（不重试、保留 detail）。连接失败（ERP/MES 传播）、权限（写门禁 403/503、IntegrationPermissionDenied）、空数据（DATA_MISSING）此前已有测试覆盖。
- 凭据扫描结论：未跟踪脚本（get_openmes_token/seed_*/investigate_* 等）均从 `.env` 读取凭据，无硬编码兜底值；`.gitignore` 覆盖 `.env`/`.env.*`。无需轮换（未发现新暴露）。
- `compileall` 此前的 `__pycache__` 权限锁已随旧进程退出自行解除，本轮通过（无代码改动）。
- 全量测试：**42 passed**（38→42）；`compileall` 通过；`npm run build` 通过。

**阶段三：Agent 运行记录页面查询（新功能）**

- `frontend/src/api.ts`：新增 `AgentRunSummary`/`AgentRunDetail`/`AgentRunEvidenceItem` 类型与 `agentRunTypeNames` 映射（8 类：报价/采购/跟单/质量文档/发运门禁/人工审批/ERP 草稿写入/ERP↔MES 关联）。
- `frontend/src/RealBusinessPage.tsx`：页面底部新增"Agent 运行记录"面板——展开/收起、按 Agent 类型过滤、刷新；行点击展开详情：输入参数、**证据链（source/record_type/record_id/summary 逐条显示真实 ERP 记录编号）**、错误信息、数据限制、起止时间。
- 页面实测：展开 `RUN-99BCF77551D4`（报价 analyze），证据链完整显示客户"上汽集团"、物料 BD-2401、价格记录 mrrhhgs4s8、BOM-BD-2401-001、4 条库存记录；2026-09-28 的运行记录跨重启可查。截图 `gui-test-screenshots/2026-09-29_agent_runs_panel_evidence.png`。

**页面级 8 步走查发现并修复 3 个前端问题**（`RealBusinessPage.tsx`）：

1. **缺料路径步骤 6 卡死**（与 §6.1 NO_SHORTAGE 卡步同类，此前 §6.2 走查未完成所以漏网）：`createPoDraft` 成功后不推进步骤且无任何进入步骤 7 的入口。修复：PO 草稿创建成功后刷新工单列表并 `setStep(7)`。
2. **步骤 7 无法刷新工单列表**：页面提示"先在 OpenMES 建立正式关联"，但建立关联后工单列表无从刷新（顶部 ↻ 只刷新 Mock 场景数据）。修复：步骤 7 增加"↻ 刷新工单列表"按钮。
3. **步骤 8 无法重新加载门禁**：MES 生产进度更新后只能重新开始整个流程。修复：步骤 8 增加"↻ 重新加载跟单与门禁"按钮（复用 `loadTracking`）。
- 三个修复均经浏览器实测复验；`npm run build` 通过。

**页面级双场景最终验收（真实记录，全部 TEST_ 标记数据）**

本批真实写入记录（写入均为官方 API，全部回读确认）：

| 记录 | 编号 | 说明 |
|------|------|------|
| ERP SO 草稿 | SAL-ORD-2026-00021 | 100 件路径（QUO-B406CF260BBF，85 元原价，docstatus=0） |
| ERP SO 草稿 | SAL-ORD-2026-00022 | 2000 件第一次走查（QUO-2A531CB13585；流程止步于旧步骤 6 卡死 bug，已由 00023 重走复验） |
| ERP SO 草稿 | SAL-ORD-2026-00023 | 2000 件完整路径（QUO-8D8CEE7515DD，docstatus=0） |
| ERP PO 草稿 | PUR-ORD-2026-00007 / 00008 | 分别对应 00022/00023 的上海铸锻厂方案（CNY 39,080，交期 2026-10-14=真实 lead_time 15 天，docstatus=0） |
| MES 工单 | id=8 `TEST_WO_PAGE_00022` | `POST /api/v1/work-orders` 创建（201）+ accept，`customer_order_no=SAL-ORD-2026-00022`，0% |
| MES 工单 | id=9 `TEST_WO_PAGE_00023` | 同上（201/200），`customer_order_no=SAL-ORD-2026-00023`；冻结快照继承已发布工程文档 id=1(SOP)/2(Control Plan) |
| MES 批次 | id=3 `TEST_LOT_PAGE_9` | `POST /work-orders/9/batches`（201）→ `POST /batch-steps/2/start`（200）→ `/complete` produced_qty=1800（200）→ 回读 `produced_qty=1800.00`（90%） |

- **场景 A（应阻断）**：100 件 NO_SHORTAGE 路径跳步复验通过（不再卡步骤 5/6，直接到步骤 7，截图 `2026-09-29_no_shortage_step7.png`）；2000 件路径在工单 0% 时发运门禁页面显示"禁止发运"——报价审批 ✓/质量门禁 ✓/生产进度 ✗ (0%<90%)，阻塞原因"生产进度不足"。截图 `2026-09-29_scenarioA_ship_gate_blocked.png`。
- **场景 B（应通过）**：同一工单按官方批次工序接口补产 1800/2000（90%）后，页面重新加载门禁：三项全 ✓，"可以发运"。截图 `2026-09-29_scenarioB_ship_gate_passed.png`。
- API 交叉验证：`GET /api/real-orders/ship-gate/9?quotation_approved=true` → `can_ship=true, completion_rate=90.0, blocking_reasons=[]`，与页面一致。
- 走查辅助脚本：`backend/walkthrough_page_scenarios.py`（create/produce 两模式，凭据从 .env 读取，输出不含凭据）。

**遗留问题（更新）**

- ~~Agent 运行记录页面查询~~ 已完成（本轮）
- ~~页面级双场景验收截图~~ 已完成（本轮）
- ~~NO_SHORTAGE 卡步~~ 已复验收口（本轮）
- ETA 仍取交期占位（produced_qty 已可积累，速率 ETA 待真实排产数据）
- 质量写回的身份来源仍是服务端令牌（REAL_WRITE_API_TOKEN）+本地审批记录，未接真实用户/角色体系；close/disposition 与纠正措施校验未接入（页面 unsupported_capabilities 如实标注）
- MES 质量数据补录（检验记录 0 条）仍待用户决策
- 旧 Mock 场景隔离（Mock 页面已有横幅，后端场景注册表与真实链共用进程）

### 3.14 本轮继续开发：阶段五动态协同——协调智能体（2026-09-29）

**背景**：用户提供团队讨论稿《汽车零部件四智能体功能与流程_团队讨论稿_动态协作版》，明确要求 Agent 调用拓扑与业务生命周期分开（不写死调用顺序），交互形态为"输入自然语言问题（如订单什么时候能做完）→ 智能体自行调用四个智能体查真实数据 → 汇总回答"。计划先落 docs/next_development_plan.md 阶段五（用户要求先写文档再开工）。

**已完成**：

1. **四个 AIP 智能体接入真实技能**（`app/aip/agents/*_aip.py`，8 个只读技能，原 Mock 演示技能保留并注释区分）：
   - quotation: `quotation.analyze_real`（real_order.analyze_quotation）、`quotation.get_real`（get_quotation）
   - procurement: `procurement.analyze_real`（analyze_procurement）、`procurement.get_real_plan`（get_procurement_plan）
   - tracking: `tracking.track_real`（track_order）、`tracking.lookup_order_link`（order_linkage.get_order_mes_link）、`tracking.check_real_ship_gate`（ship_gate_check）
   - quality-document: `quality.get_real_package`（quality_package）
2. **协调智能体** `app/services/coordinator.py`：DeepSeek 工具调用循环（≤8 轮）；能力目录 `REAL_SKILL_TOOLS`（8 技能含用途描述+参数 schema+aip_agent 路由键，即"能力发现"依据）；每个工具调用走真实 AIP RPC（acps_sdk start→poll→complete → `POST /aip/{agent}/rpc`）；调用链留痕（caller→callee→技能→参数→结果摘要→状态→耗时）；协调运行持久化 real_agent_runs（agent_type=coordinator）。system prompt 明确：禁编造数字、数据缺失如实说、只读不承诺写入。
3. **API** `POST /api/real-orders/assistant/ask`：DeepSeek 未配置返回 503 `llm_not_configured`（不伪造回答）；question 必填 422。
4. **前端**：真实业务页顶部"智能协同问答"面板（textarea + 提问 → 回答 + 调用链可视化：序号/子智能体/技能/参数/状态/耗时 + coordination_run_id）；agent-runs 面板与映射支持 coordinator 类型；`api.ts` 修复结构化错误 detail.message 提取（原先显示 [object Object]）。
5. **测试** `tests/test_coordinator.py` 10 例：假 LLM 工具循环→链与持久化、工具失败回喂、轮次上限、未知技能不致命、能力目录↔AIP 注册一致性、tool specs 格式、无 DEEPSEEK key 明确报错、真实技能接线验证（patch real_order 函数验证调用与参数）。全量 **52 passed**，compileall 通过，前端 build 通过。

**真实验证（AIP 通道，无 LLM 部分）**：重启后端（9000）后用 AipAgentClient 直连验证——
- `tracking.lookup_order_link(SAL-ORD-2026-00023)` → LINKED，`association.links[0].mes_record.record_id=9`（ERPNext+OpenMES 双 authority）
- `tracking.track_real(9)` → TEST_WO_PAGE_00023 完成率 90.0%（authority OpenMES）
- `quality.get_real_package(9)` → 质量门禁 True、未关闭问题 0（authority OpenMES）
- 即协调者的调用通道（AIP RPC → 真实技能 → 真实 ERP/MES）已全链打通；缺的只有 LLM 决策层。

**阻塞项**：`.env` 中 `DEEPSEEK_API_KEY` 为**空值**（键存在无值），`LLM_MODEL=deepseek-flash`、`DEEPSEEK_BASE_URL` 已配置。因此"LLM 驱动完整问答"的真实验收暂未执行——页面与 API 均如实返回 503 明确提示"尚未配置 DEEPSEEK_API_KEY。不使用固定话术伪造回答"（截图 `gui-test-screenshots/2026-09-29_assistant_panel_llm_not_configured.png`）。**待用户提供 API Key 后即可跑阶段五任务 6 的两问动态性验收**。

**修改文件**：backend/app/services/coordinator.py（新增）、backend/app/aip/agents/{quotation,procurement,tracking,quality_document}_aip.py（真实技能）、backend/app/main.py（ask 端点）、backend/tests/test_coordinator.py（新增）、backend/tests/conftest.py（上轮）、frontend/src/api.ts、frontend/src/RealBusinessPage.tsx、frontend/src/styles.css、docs/next_development_plan.md（阶段五计划）。

**阻塞解除与真实验收（2026-09-29，用户提供 DEEPSEEK_API_KEY 后）**：

- 修复：DeepSeek 工具名约束 `^[a-zA-Z0-9_-]+$` 不允许技能 ID 中的点——协调者增加技能 ID↔LLM 工具名双向映射（`.`→`__`，`_LLM_NAME_TO_SKILL`）；`http.py` 错误提取兼容 OpenAI 风格错误体 `{"error":{"message":...}}`（此前只显示"远端服务拒绝请求"）。测试同步更新，52 passed。
- **问 1**"SAL-ORD-2026-00023 这个订单什么时候能做完？现在进展如何？"（HTTP 200，4.5s，RUN-COORD-77C8CCC320D7）：
  - 调用链（2 步，LLM 自主决定）：`lookup_order_link(SAL-ORD-2026-00023)` 243ms → `track_real(9)` 461ms；
  - 回答：完成率 90%（1800/2000）、交期 2026-10-31、关联字段精确一致，依据含 ERP 订单记录、MES 工单 id=9、产线、ETA；**主动发现真实数据不一致**（工单状态 PENDING vs 完成率 90%、工序 TEST_Final_Assembly completed=0）并建议核实，未编造结论。
- **问 2**"SAL-ORD-2026-00023 为什么还不能发运？"（HTTP 200，6.2s，RUN-COORD-920A6B000D5A）：
  - 调用链（4 步，与问 1 不同——动态性证据）：`lookup_order_link` → `track_real` → **`quality.get_real_package`** → **`check_real_ship_gate`（LLM 自主传 quotation_approved=false）**；
  - 回答：唯一阻塞"报价/订单未审批（ERP Draft）"，质量门禁通过（SOP/Control Plan 2 份已发布、0 未关闭问题）、生产 90% 达标；明确"只读权限无法代办审批"；并如实标注 OpenMES 无订单级质量放行 API 的边界。
- 页面级：问答面板实测通过，回答与调用链（4 次调用·3 轮推理）正常显示，截图 `gui-test-screenshots/2026-09-29_assistant_dynamic_coordination_q2.png`。
- 协调运行持久化复验：`GET /api/real-orders/agent-runs?agent_type=coordinator` 返回 3 条（问 1/问 2/页面问）。
- **动态性验收结论**：同一入口两个问题产生两条不同的真实调用链，每个数字可追溯到工具返回的真实记录；全程 ERP/MES 零写入。

**写入说明**：本阶段全部为只读查询通道（ERP/MES 零写入）；analyze_procurement/get_real 仅写本地业务记录表（与 8 步流程一致），不写真实 ERP/MES。

### 3.15 本轮继续开发：阶段六方案化协同（2026-09-29）

**已完成**（按 next_development_plan.md 阶段六任务清单 0-7 全部执行）：

1. **git 提交保护**：提交 `3a14196`（2015 文件，阶段 1-5 全部工作；acps-sdk-src 以 vendored 文件入库并移除其上游 .git，origin=AIP-PUB/ACPs-community v2.2.0 已记录；services/（OpenMes、frappe_docker 独立克隆）与 .trae-html-share-packages/ 加入 .gitignore）。
2. **新增真实技能函数**（real_order.py，均带 @_agent_run 留痕）：
   - `assess_cost_impact(plan_id, option_id)`：缺料换供应商方案的成本影响（订单收入=报价真实 Selling 价；基准材料成本=方案 net_requirement 真实 Buying 价；方案成本=供应商特定价合计；输出差值/单件加价/材料毛利前后/证据链）。价格缺失返回 DATA_MISSING 明确拒绝估算；口径如实标注"仅材料成本，无工时/制费"。
   - `assess_delivery_impact(work_order_id, material_ready_date)`：物料到货（由方案 lead_time_days 推算）vs 工单交期（真实 track_order 数据），输出 buffer_days 与 verdict（arrival_in_time/arrival_after_due/not_needed）；无速率数据不推算剩余产量 ETA（assessment_scope 如实声明）。
   - `find_quotation_by_erp_order(erp_order_id)`：按 ERP 订单号反查已保存报价（erp_draft_id 精确匹配，最新优先），补齐"工单→报价→采购方案"问答链。
3. **AIP 接线**：quotation_aip 增加 `quotation.find_real_by_erp_order`/`quotation.assess_cost_impact`；tracking_aip 增加 `tracking.assess_delivery_impact`。
4. **协调者方案合成**：能力目录扩至 11 个真实工具（含用途描述引导方案场景）；system prompt 增加方案类问题规则（多方案+真实数字+人工确认、数据缺失不得编造第三方案）；`_collect_proposal` 把工具结果原样汇集为结构化 `proposal_options`（缺料/供应商选项/成本评估/交期评估/缺失项，零二次计算）。
5. **前端**：方案对比卡片（推荐徽标/覆盖/总价/交期/成本影响/单件加价/毛利变化/推荐理由/交期影响/数据缺失块，"最终由人工确认"标注）；回答轻量 Markdown 渲染（标题/加粗/表格，HTML 先转义）。
6. **修复**：DeepSeek 工具循环偶发空回答 → 确定性恢复机制（追加"只基于以上工具结果用中文直接回答"的无工具调用，recovered 标志透明记录）；MAX_TOOL_ROUNDS 8→12（复杂方案链 13-14 步调用会顶格）。
7. **测试** `tests/test_proposal_skills.py` 12 例：成本差值/单件加价/毛利计算断言、DATA_MISSING 分支、缺失方案/选项报错、延期/提前/非法日期、范围诚实性、订单反查（含无匹配）、_collect_proposal 汇集与 data_missing、新技能 AIP 注册一致性。全量 **64 passed**，compileall/build 通过。

**真实验收（DeepSeek 驱动，全部真实 ERP/MES 数据）**：

- **问 3（缺料方案）**"SAL-ORD-2026-00023 的物料有缺口…给我几套可执行的采购方案"（HTTP 200，约 30s，13-14 步调用链）：LLM 自主链 = 查关联 → 反查报价（新技能）→ 报价分析 → 工单进度 → 采购分析 → 逐方案成本评估（OPT-1/2/3）→ 逐方案交期评估（中途一次参数为空的调用失败后自恢复重试）→ 汇总回答。输出：OPT-1 上海铸锻厂（唯一 3/3 全覆盖，+1180 元、单件 +0.59、毛利 132100→130920、物料 10-14 到货早交期 17 天不延期，推荐）；OPT-2/OPT-3 部分覆盖，LLM **批判性指出其负成本差异是"未覆盖全部缺料造成的口径假象，不可直接比较"**；proposal_options 结构化卡片页面渲染正常（截图 `gui-test-screenshots/2026-09-29_assistant_proposal_cards.png`）。
- **问 4（加急）**"客户希望交期尽量提前，能不能做到"（HTTP 200，约 42s，14 步调用链，与问 3 又不同——多了质量包与发运门禁）：结论诚实——"物料不是瓶颈（所有方案到货都早于交期），但能否提前无法给出确切日期，因 MES 无剩余产量排程数据，工具明确不推算 ETA"；指出真正限制在审批（Draft）与生产排程。全程未编造"能提前 X 天"。
- 协调运行记录持久化可查（agent_type=coordinator）。

**写入说明**：本阶段新增技能全部只读或本地记录读写；ERP/MES 零写入。采购分析每次调用生成新本地方案记录（PROC-*），属正常业务留痕。

### 3.16 本轮继续开发：阶段七执行闭环（2026-09-29）

**已完成**（next_development_plan.md 阶段七任务 1-7；写入审批依据：用户 2026-09-29 指令"开始"执行阶段七计划，计划中明确包含卡片批准→PO 草稿写入）：

1. **新真实技能** `procurement.assess_combination(plan_id, option_ids)`：分单采购组合的确定性评估——组合成本=各选项真实价格记录合计（无折扣/系数假设）、覆盖并集、最长物料级交期；同一物料被多选项重复覆盖时明确告警（组合成本含重复采购）；任一选项价格不完整返回 DATA_MISSING。修复了初版漏返回 evidence 字段的问题（测试抓出）。
2. **协调者**：能力目录扩至 13 个真实工具；prompt 规则 E（组合方案必须用 assess_combination 计算，禁止自行相加；重复覆盖/未覆盖告警必须原样转告）；_collect_proposal 增加 combination_assessments 汇集。
3. **前端执行闭环**：方案卡片"选择此方案并起草 PO（需人工确认）"→ 内联确认面板（明示写入内容：方案号/选项/供应商/金额/docstatus=0 + 审批人输入框）→ 调既有审批门禁端点 approve → po-from-plan → 卡片回显审批号与 PO 草稿号 + 回读状态；执行中/已执行状态防重复提交。
4. **验收发现并修复状态联动断链**：首轮验证"方案批准了吗"时协调者如实回答查不到（未乱猜）——根因是问答链 analyze_real 生成的报价没有与 ERP 订单的关联（erp_draft_id 仅在 8 步流程创建草稿后存在）。修复：①analyze_quotation 新增可选 source_erp_order_id（**声明式上下文**：仅当调用方明确给出订单号时记录，不做事后推断）；②find_quotation_by_erp_order 同时匹配 erp_draft_id 与 source_erp_order_id，返回值附带最新方案摘要；③新技能 procurement.find_real_plan_by_quotation（按报价查最新方案状态：审批号/选定选项/PO 草稿号）。
5. **真实验收（修复后）**：
   - 卡片批准：PROC-F7422D68E79C + OPT-1（上海铸锻厂 39080 CNY）→ 审批 APR-BCC4EE93ACF6 → PO 草稿 PUR-ORD-2026-00009（首轮）；修复后复验 APR-9005E70E94FC → PUR-ORD-2026-00010（回读确认，docstatus=0）。
   - 状态联动：问"SAL-ORD-2026-00023 的采购方案批准了吗？PO 草稿生成了吗？"→ 协调者 2 步链（find_real_by_erp_order → find_real_plan_by_quotation）准确回答：方案 PROC-959804A88AA9 状态 PO_DRAFT_CREATED、审批号 APR-9005E70E94FC、PO 草稿 PUR-ORD-2026-00010，与卡片操作一致；并主动指出"报价单本身状态仍为 DRAFT，与方案侧审批状态不一致，建议一并核实"（真实治理观察：问答链报价未走报价审批，记录为待办）。截图 `2026-09-29_proposal_exec_closed_loop.png`、`2026-09-29_proposal_status_linkage.png`。
   - LLM 在方案问答中主动调用 assess_combination 计算分单组合（方案 B），并原样转告工具告警"无重复覆盖、无未覆盖"。
6. **测试**：assess_combination 5 例（组合数学/覆盖并集/重复覆盖告警/单选项未覆盖告警/DATA_MISSING/未知选项报错/AIP 注册）；find_latest_plan_by_quotation 间接覆盖。全量 **69 passed**，compileall/build 通过。

**写入说明（真实 ERP 写入记录）**：PUR-ORD-2026-00009/00010 采购订单草稿（docstatus=0，回读确认）；对应审批记录 APR-BCC4EE93ACF6/APR-9005E70E94FC（本地持久化）。协调者本身无写权限；写入全部经人工卡片确认 + 审批门禁。

**遗留（新增）**：问答链报价未走报价审批（方案侧已批准 vs 报价 DRAFT 不一致）——建议方案卡片批准时同步校验报价审批状态或引导先审批报价，列为下一迭代。

### 3.17 阶段八进行中快照：质量异常协同 + 审批一致性（2026-09-29，⚠️ 暂停交接）

> 本节按用户 2026-09-29 指令如实记录阶段八中断状态，供下一个执行者（人或 AI）接手。
> 完整任务清单与交接说明见 docs/next_development_plan.md 阶段八；写入依据：用户"开始"（阶段八计划批准）。

**已完成并验证**：

1. **新真实技能 `quality.assess_quality_impact(work_order_id)`**（real_order.py，@_agent_run("quality","impact")）：质量问题清单（未关闭按严重度排序）+ 批次关联 + 质量门禁 + 生产进度交叉 → 影响结论（发运阻断/交期风险）；数据缺口如实列出（检验 0 条、SN 无 API、NCR NOT_SUPPORTED、批次读取失败记为缺口不伪造为空）；处理选项均标注需人工确认。
2. **MES 批次读取**：客户端 `list_work_order_batches`（GET /api/v1/work-orders/{id}/batches，Bearer）、适配器 `get_work_order_batches`、base.py 协议、mock.py 空实现。
3. **AIP 接线**：quality_document_aip 注册 `quality.assess_quality_impact`（9000 健康检查实测列出）。
4. **协调者**：工具目录新增该技能（描述引导质量异常场景）；`_collect_proposal` 汇集 `quality_impacts` 与 `quotation_status`。
5. **前端**：方案卡片双审批线（报价未审批→确认面板①报价审批②方案审批两行，各自审批人输入，依次 approveQuotationApi→approveProcurementPlan→createPoFromPlan，回显两笔审批号）+ 质量影响问答区块（异常表/批次/结论/处理选项/数据缺口）；api.ts 新增 QualityImpactResult/quotation_status 类型与 approveQuotation 封装；样式追加。**npm run build 通过**。
6. **测试**：quality impact 5 例（未关闭排序、干净工单无阻断、批次经适配器、批次失败为数据缺口、汇集器采集）；全量 **74 passed**；compileall 通过。

**做到一半（未完成，接手者从这里继续）**：

- **Q6 真实验收中断**："WO-2026-001 这个工单有质量异常吗？会影响交付吗？该怎么处理？"已真实执行（HTTP 200）：协调者 4 步链交叉核实后如实回答"WO-2026-001 在系统中查不到"（assess_quality_impact/get_real_package 因参数非数字 id 被远端拒绝、track_real NOT_FOUND、lookup_order_link ERP_ORDER_NOT_FOUND），给出 3 条补数路径——**行为正确但暴露缺口：用户说工单编号、工具只收数字 id**。
- **修复做了一半**：`real_order.find_work_order_by_no(work_order_no)` 函数已写完并编译通过（精确编号匹配、未命中返回 existing_work_orders 建议），但 **AIP 未注册、协调者目录未加、测试未写、后端未重启**（当前 9000 进程无该技能）。
- **剩余验收**：重启后端 → Q6 重跑（期望 find_by_no→assess_quality_impact，回答含 WO-2026-001 真实质量状态）→ 双审批线卡片批准验收（两笔 approval_id + PO 草稿回读）→ 状态一致性复查（报价不再是 DRAFT 不一致）→ 截图 → 全量回归 → git 提交。

**本阶段 ERP/MES 写入**：暂无（find_work_order_by_no 为只读；双审批线功能未做真实验收写入）。测试 74 passed；前端 build 通过；**阶段八全部改动未提交 git**。

**遗留问题清单（更新，优先级从高到低）**：

1. 阶段八收尾（见上：编号→id 接线 + 剩余验收）；
2. 检验记录补录 seed（用户未批准，检验维度如实显示"无数据"）；
3. 质量写回真实用户/角色身份治理（现为服务端令牌 + 页面自报 approved_by）；
4. NCR close/disposition 接入（NOT_SUPPORTED 如实标注）；
5. 旧 Mock 场景后端隔离（Mock 注册表与真实链共用进程）；
6. 速率 ETA（依赖 MES 排程/报工时序数据，当前无数据源）；
7. Wutong 外部注册发现 + ACS 能力文件同步真实技能。

### 3.18 阶段八继续开发：工单编号桥接与审批状态一致性（2026-09-30）

**已完成并验证**：

1. `tracking.find_real_by_no` 已注册到 `tracking` AIP 服务；协调者能力目录和系统提示已明确要求：用户给出 `WO-...` 编号时，必须先精确换取数字 `work_order_id`，禁止把编号中的数字当数据库 ID。
2. 新增编号桥接单元测试和协调者两步调用链测试（`find_real_by_no → assess_quality_impact`）；未命中时返回已有工单编号建议，不猜测 ID。
3. `_collect_proposal` 不再把采购方案状态误当报价状态；采购分析结果新增 `quotation_status`，取自持久化报价记录。真实报价审批状态为 `APPROVED` 时，方案卡片不会重复要求报价审批。
4. 报价审批、采购方案审批和 PO 草稿创建增加幂等回读：重复点击复用原 `approval_id`/草稿编号；已批准方案不能改选供应商；已存在 PO 草稿时不会再次调用 ERP 创建单据。
5. 9001/9002 临时后端已加载新代码并完成真实只读复验：
   - `GET /aip/tracking/health` 列出 `tracking.find_real_by_no`；
   - AIP `tracking.find_real_by_no({work_order_no: WO-2026-001})` 返回 `work_order_id=2`、`customer_order_no=SAL-ORD-2026-00001`、`authority=OpenMES`；
   - AIP `quality.assess_quality_impact({work_order_id: 2})` 返回 `open_issues_count=0`、`missing_documents=[SOP, Control Plan]`、生产完成率 `0%`，并列出 `inspections/sn_traceability/ncr_full_disposition` 数据缺口。
6. 阶段八中间快照验证结果：后端 **80 passed**，`compileall` 通过，前端 `npm run build` 通过；收尾后的全量结果为 **83 passed**（见 §3.20）。

**仍未完成**：

- DeepSeek 驱动的 Q6 自然语言问答与 ERP/MES 数据链已在用户授权后完成，详见 §3.20 的 `RUN-COORD-24CA66BD6EC5`。
- 双审批线到 ERPNext 的真实写入/回读已完成，报价、采购方案、审批号和 PO 草稿状态已通过协调者状态问答复核，详见 §3.20。
- 检验记录仍为 0 条，NCR close/disposition 和真实用户角色治理仍按原计划保留。

### 3.19 工程可维护性：修正核心服务代码被误忽略（2026-09-30）

- 根目录 `.gitignore` 原规则 `services/` 会匹配任意层级目录，意外忽略核心 `backend/app/services`；现已改为保留第三方服务目录忽略，同时显式放行 `backend/app/services/**`，并继续忽略其 `__pycache__`。
- `git status` 现在能够发现 `backend/app/services/{real_order,coordinator,order_linkage,llm_quotation}.py`，不会再把核心业务代码当成不可追踪文件。
- 该修复只影响版本控制可见性，不改变业务运行逻辑；阶段八的 ERPNext 草稿写入记录见 §3.20。
- 阶段八中间快照回归结果为后端 **80 passed**，收尾后的全量结果为 **83 passed**（见 §3.20）。
- 9000 端口已重启并加载阶段八修复；9001/9002 临时验证进程已关闭。

### 3.20 阶段八收尾：方案汇总一致性与真实双审批验收（2026-09-30）

**代码修复**：

1. `_collect_proposal` 优先使用本轮 `procurement.analyze_real` 生成的当前方案；`procurement.get_real_plan` 只在方案编号相同的情况下补充审批/PO 状态，历史方案不会覆盖当前 `plan_id`、缺料清单或供应商选项。
2. 成本与组合评估若携带其他 `plan_id` 会被汇总层过滤，避免前端方案卡片混入旧方案数字；系统提示同时要求后续评估沿用分析返回的 `plan_id`。
3. 新增 3 个回归用例，覆盖旧方案覆盖、同方案状态补充和跨方案成本评估过滤。

**真实验收**：

- 缺料问答运行 `RUN-COORD-24CA66BD6EC5`：报价 `QUO-93AAB835646A`、本轮分析方案 `PROC-5217CEF9507D`，方案卡片已指向当前分析方案，报价状态正确显示 `DRAFT`；调用链 13 步，成本/组合/交期数据均来自 ERPNext/OpenMES。
- 报价双审批：`QUO-93AAB835646A` → `APPR-2DD01946C804`（`sales_manager`），状态 `APPROVED`。
- 采购方案审批：`PROC-5217CEF9507D` + `OPT-1` → `APR-A6595BD82781`（`purchase_manager`），状态 `APPROVED`。
- ERPNext PO 草稿：`PUR-ORD-2026-00011`，供应商上海铸锻厂，总额 `39080.00 CNY`，交期 `2026-10-14`，回读 `docstatus=0` 且 `read_back_verified=true`；方案状态更新为 `PO_DRAFT_CREATED`。
- 状态问答运行 `RUN-COORD-3DF81B85C2FA`：协调者两步反查准确返回报价 `APPROVED`、方案 `PO_DRAFT_CREATED`、审批号和 PO 草稿号。

**验证结果**：后端 **83 passed**，`compileall` 通过，前端 `npm run build` 通过。外部 DeepSeek 调用和 ERPNext 草稿写入已在用户授权后完成，不再是待验收项。

### 3.21 ACS 能力描述与真实 AIP 注册表同步（2026-09-30）

- `backend/app/aip/generate_acs.py` 新增真实技能同步逻辑：从 `REAL_SKILL_TOOLS` 读取当前协调者能力目录，按四个 AIP 智能体追加真实技能，并按技能 ID 去重。
- 重新生成 `backend/acs/{quotation,procurement,tracking,quality_document}_acs.json`；机器可读 ACS 现在包含阶段五至阶段八接入的真实技能（报价 4、采购 4、跟单 5、质量 2）。
- 新增 `backend/tests/test_acs_sync.py`，校验每个 ACS 文件存在、技能 ID 不重复、真实技能集合与能力目录一致。
- 验证：ACS 生成脚本通过，ACS 同步测试 **1 passed**，`compileall` 通过；本步骤只修改本地能力描述文件，不写入 ERPNext/OpenMES。

### 3.22 真实审批身份与角色来源（2026-09-30）

**实现内容**：

1. 新增 `backend/app/services/identity.py`：从 ERPNext `frappe.auth.get_logged_user` + `User` 详情，或 OpenMES `GET /api/auth/me` 解析当前认证主体；不再把浏览器提交的 `approved_by` 当作身份凭证。
2. 新增 `REAL_IDENTITY_PROVIDER=auto|erpnext|openmes` 与可选 `REAL_IDENTITY_ROLE_MAP` JSON 配置。ERPNext/OpenMES 返回的 `Sales Manager`、`Purchase Manager`、`Quality Manager` 等角色统一归一化为业务角色；平台管理员保留真实用户名并按管理员权限通过角色门禁。
3. 报价审批、销售订单草稿、采购方案审批、采购订单草稿及质量处理审批接口均校验后端解析的主体与业务角色。请求体中的 `approved_by/requested_by` 仅作兼容提示，缺省时由后端填入真实主体，若与真实主体不一致返回 403 `actor_mismatch`。
4. 新增 `GET /api/real-orders/identity/me`；真实业务页面显示当前身份/来源/角色，审批卡片移除 `sales_manager`/`purchase_manager` 自报输入，使用解析出的 `actor_id`。
5. 审批写入返回 `authenticated_identity`，使审批记录同时可追溯外部身份源、主体和角色。

**验证**：

- 9000 重启后 `GET /api/real-orders/identity/me` 返回 HTTP 200：`subject=Administrator`、`authority=ERPNext`、`provider=erpnext`，角色来自真实 ERPNext `User.roles` 列表。
- 已对既有报价 `QUO-93AAB835646A` 重放审批请求（不产生新记录），HTTP 200，返回原审批号 `APPR-2DD01946C804` 并附当前认证身份。
- 定向身份测试 **7 passed**；后端全量 **91 passed**；`compileall` 与前端 `npm run build` 通过。
- 本次没有新增 ERPNext/OpenMES 业务写入；报价审批重放命中既有幂等记录，未产生新审批或草稿。

**遗留与下一步**：

- 当前真实部署使用 ERPNext 集成账号 `Administrator` 作为上游认证主体；接入企业 SSO/OIDC 后，将 `REAL_IDENTITY_PROVIDER` 替换为对应 provider 或扩展同一解析接口即可，审批接口无需改动。
- NCR `close/disposition` 与纠正措施校验已接入（见 §3.23）；订单级质量放行仍为 `NOT_SUPPORTED`，随后处理速率 ETA、Wutong 外部发现。

### 3.23 NCR disposition / close 与纠正措施校验（2026-09-30）

**实现内容**：

1. 根据仓库内 OpenMES 源码核对并接入真实契约：`PUT /api/v1/issues/{id}/disposition`、`GET /api/v1/issues/{id}/actions`、`POST /api/v1/issues/{id}/close`；请求字段来自 `SetDispositionRequest`，纠正措施状态来自 `IssueActionService` 的 `open → in_progress → done → verified` 生命周期。
2. OpenMES 客户端与真实适配器新增 disposition 写回和纠正措施读取；Mock 适配器明确拒绝这些真实写操作，避免 Mock 冒充真实结果。
3. 新增三段式人工审批门禁：disposition 请求/批准/写回，close 请求/批准/写回；写回前校验问题属于工单、根因和遏制措施非空、审批编号/真实身份匹配，close 额外要求问题已 `RESOLVED`、disposition 已登记、所有纠正措施均为 `VERIFIED`。
4. 每次真实写回都重新读取质量包并比较 disposition、根因、遏制措施或 `CLOSED` 状态；不一致返回 `WRITE_UNVERIFIED`，不宣称成功。
5. 协调者新增只读技能 `quality.check_issue_closure`；质量资料包将 NCR 能力标记为 `AVAILABLE_WITH_APPROVAL`，只有订单级质量放行仍为 `NOT_SUPPORTED`。

**验证**：

- 真实工单 `work_order_id=2`、问题 `issue_id=1` 执行只读 `GET /api/real-orders/quality/issues/1/closure-check?work_order_id=2`：HTTP 200，真实状态 `RESOLVED`，`disposition=pending`，关闭校验明确 `closure_ready=false`，纠正措施读取成功且无数据伪造。
- 真实 OpenMES 质量包返回 NCR 能力 `AVAILABLE_WITH_APPROVAL`，`unsupported_capabilities` 仅剩订单级质量放行。
- 新增客户端/服务层生命周期测试 **5 passed**；阶段相关定向回归 **18 passed**；全量回归、compileall 和前端构建在提交前复验。
- 本阶段没有对真实 NCR 执行 disposition 或 close 写入；所有真实验证均为只读，写入仍必须经过真实用户角色 + 服务端令牌 + 两步审批。

### 3.24 Mock 后端与真实业务表面隔离（2026-09-30）

**实现内容**：

1. 新增 `MOCK_DEMO_ENABLED` 运行时开关。未显式配置时，`APP_ADAPTER_MODE=real` 自动只暴露真实业务表面；设置为 `true` 才允许合成场景与真实链共存。
2. 真实表面中间件拦截旧 Mock 路由（`/api/projects`、`/api/scenarios`、`/api/approvals`、`/api/agents`、`/api/plans`、`/api/audit`、`/api/replay`、旧 `/api/quotation`），返回 404 `mock_surface_disabled`，不会把 synthetic_demo_only 数据误当业务数据。
3. AIP 服务新增技能收缩能力；真实表面只暴露协调者 `REAL_SKILL_TOOLS` 中的真实技能，Mock AIP 技能不再从真实 RPC 路由可见。开发/演示表面仍保留原 Mock 技能。
4. 新增 `GET /api/runtime/surface`；前端按返回能力隐藏 Mock 导航，真实页面和 MES 完工读取入口保持可用。

**验证**：

- 9000 重启后 `GET /api/runtime/surface` 返回 HTTP 200：`mock_demo_enabled=false`、`aip_mock_skills_enabled=false`；访问 `GET /api/projects` 返回 HTTP 404 `mock_surface_disabled`。
- `GET /aip/quotation/health` 只列出 4 个真实报价技能，未暴露 `quotation.calculate_cost/create_draft` 等 Mock 技能。
- 定向隔离测试 **3 passed**；相关协调者/ACS 回归 **16 passed**；全量后端 **99 passed**；前端构建通过。
- 真实 ERPNext/OpenMES 数据链没有写入；本阶段仅改变路由暴露和页面导航。

## 8. 当前结论

阶段 1（ERP↔MES 关联）已完成：字段确认、只读接口、关联值回填（WO-2026-001 → SAL-ORD-2026-00001）、LINKED 验证。

阶段 2（2026-09-28）：报价与采购 Agent 真实化完成——

- 已删除全部占位计算（固定成本/加成/固定交期/固定公司仓库/0.7 系数/50 元占位/编造推荐理由），逐项清单见 3.5.1
- 数量折扣已按用户决策删除（报价按真实价格原价）；供应商业务数据已按用户批准补录并回读（3.5.5）
- 报价：真实 Selling 价、真实 BOM 子价（无加成）、真实库存、真实 MES 排程交付估算；缺价 → DATA_MISSING 并拒绝出价与草稿
- 采购：真实缺料计算（含 MOQ）、供应商差异化方案（Item Supplier 过滤+供应商特定价+物料级交期）、推荐规则 lowest_total_cost_v2、缺价拒绝创建 PO

阶段 3（2026-09-28，见 3.6/3.7）：五项优先级任务完成——

1. **持久化**：报价/审批/采购方案迁移 SQLite，重启后可查、审批门禁跨重启有效（实测 SAL-ORD-2026-00008）
2. **采购前端**：8 步流程（采购分析/方案审批/PO 草稿），浏览器实测全流程通过（含真实价格记录号、交期、MOQ、推荐理由、数据限制标注）
3. **质量接口**：检验记录与工程文档接口接入（0 条数据如实显示）；质量放行与 NCR 写回标注 NOT_SUPPORTED，不伪造
4. **残留清理**：删除固定数据草稿端点、交期默认值改为显式拒绝
5. **全流程验收**：ERP↔MES 关联→报价（85 元原价+17 天真实排程）→审批→SO 草稿→采购（3 缺料差异化方案）→审批→PO 草稿（真实交期）→质量门禁→发运门禁，全链路 200 且交付被真实质量状态正确阻断

当前验收结论（2026-09-30 第五次更新，依据 3.13/3.14/3.15/3.16/3.17/3.20）：

```text
四 Agent 真实业务链已端到端跑通；报价→采购→跟单→质量→发运门禁全链可用
页面级双场景验收完成：场景 A（0% 进度）正确阻断、场景 B（90%+文档+审批）正确放行，均有截图证据
动态协同（阶段五）验收通过：自然语言提问→协调智能体经 AIP 动态调用四个真实智能体→带证据回答；
两问产生两条不同调用链（动态拓扑达成）
方案化协同（阶段六）验收通过：缺料场景输出 ≥3 套方案对比（含"部分覆盖口径假象"批判性审查）；
加急场景诚实给出"物料不是瓶颈、ETA 缺排程数据"；讨论稿例子二/例子四落地
执行闭环（阶段七）验收通过：方案卡片批准→审批门禁→PO 草稿回读（PUR-ORD-2026-00009/00010）；
状态联动复验通过（协调者查到方案状态/审批号/PO 草稿号）
阶段八（质量异常协同 + 审批一致性）✅ 已完成（§3.17/§3.18/§3.20/§3.22/§3.23/§3.24/§3.26）：编号→数字 id 桥接、报价状态一致性、审批/草稿幂等保护、真实身份角色门禁、NCR 处置校验、Mock/真实表面隔离、真实 DeepSeek 问答和双审批 ERP 写入均已完成；后续收尾补上身份 envelope、NCR 对象绑定/幂等和 WebSocket 隔离（103 passed）。
阶段八及后续收尾提交：`8ef554c`、`fcf0d0b`、`e8d09e4`、`f92a78a`、`2e03100`，本轮治理收尾待提交。
工程可维护性已修正：核心 `backend/app/services` 已重新纳入 Git 可见范围
测试与安全：pytest 独立测试库（103 passed）、失败验证矩阵补齐、无硬编码凭据；身份来源与角色门禁已接入，ACS 已与真实 AIP 注册表同步。
仍余（生产可用验收前）：检验数据补录决策、订单级质量放行、
请求级企业 SSO/OIDC 身份源接入、NCR 真实写入验收、Wutong 外部发现

### 3.25 速率 ETA：真实执行速率或明确缺数（2026-09-30）

**已完成**：

1. OpenMES 适配器保留 `process_snapshot.steps` 和批次工序中的实际执行字段：`completed_qty/passed_qty`、`started_at/completed_at`、`actual_elapsed_minutes/actual_run_minutes`；计划开始/结束时间单独保留，不能被当作实际耗时。
2. `track_order` 新增观测速率 ETA：使用最后一个有效工序样本的实际产量 ÷ 实际耗时，计算剩余产量和剩余小时；输出 `eta_status=RATE_BASED`、`eta_basis=observed_production_rate`、`observed_rate` 和来源字段。顺序工序可能重复报告同一数量，因此不跨工序累加。
3. 没有同时具备实际产量与实际耗时/开始时间时，输出 `eta=null`、`eta_status=DATA_MISSING` 和明确 `eta_data_gaps`；`due_date` 仅作为交期字段和风险依据，禁止作为 ETA 占位。已完成工单仅在真实状态为终态时标记 `COMPLETED`。
4. 跟单页面显示 ETA 状态、速率依据或缺失原因，避免把交期日期误显示成生产预测。

**验证**：

- `GET /api/real-orders/mes/track/2`：HTTP 200，真实 OpenMES 工单 `WO-2026-001`，`completed_qty=0`；返回 `eta=null`、`eta_status=DATA_MISSING`，明确说明没有实际速率记录，未使用 `due_date=2026-10-15` 冒充 ETA。
- 9000 后端已重启并加载新代码；`/aip/tracking/health` 真实技能注册正常，Mock 表面仍关闭。
- 速率 ETA 定向测试 3 passed；后端全量 **102 passed**；`compileall` 和前端 `npm run build` 通过。
- 本阶段没有写入 ERPNext/OpenMES；真实复验为只读。

**边界与遗留**：当前生产工单没有历史报工速率，因此真实页面只能给出 `DATA_MISSING`。积累含实际耗时和实际产量的 OpenMES 工序/批次记录后，下一次查询会自动切换到速率 ETA；在此之前不提供固定天数预测。身份请求级会话、NCR 写入验收和 Wutong 发现仍是后续任务。

### 3.26 治理缺口收尾：身份解析、NCR 幂等与 Mock 表面（2026-09-30）

**已修复**：

1. OpenMES `auth/me` 身份解析支持 `data.user` 等嵌套 envelope；角色对象只读取 `role/role_name/name`，排除随机 `id`，避免把数据库行标识当业务角色。服务端仍明确使用集成账号，尚未把浏览器会话接入审批身份。
2. NCR resolve 写回要求审批 `reference_id` 与 URL 的 `issue_id` 一致；disposition/close 写回要求审批载荷中的 `work_order_id` 与请求一致。disposition 回读比较 disposition、根因、遏制措施及存在的数量/来源字段，已登记的相同处置和已关闭问题返回幂等成功而不重复调用 OpenMES。
3. Mock 表面隔离覆盖 WebSocket `/api/projects/{id}/stream`；真实模式握手直接以 1008 `mock_surface_disabled` 拒绝。前端加载运行时表面前默认隐藏 Mock 导航，流地址支持 `VITE_WS_BASE_URL`，默认跟随当前主机连接 9000。
4. `generate_acs.py` 现在生成与真实 AIP 运行表面一致的 ACS；四个静态 ACS 已重新生成，仅含当前真实技能（报价 4、采购 4、跟单 5、质量 3）。

**验证**：身份、NCR 生命周期、ACS 同步定向回归 **14 passed**；前端构建和后端 `compileall` 通过。尚未对真实 NCR 执行写入，WebSocket 拒绝路径未写入业务数据。

### 3.27 Wutong/ACPs 外部发现只读通道（2026-09-30）

**已完成**：

1. 根据仓库内 vendored ACPs Discovery 契约确认并实现 `WUTONG_DISCOVERY_URL` 客户端：`POST /discover` 请求 `type/query/limit/filter`，严格读取 `result.agents`、`result.acsMap`、`result.routes` 和可选 `aliveMap`；远端 `error`、格式缺失和 HTTP/网络错误均转为明确集成错误，不回退本地静态目录冒充外部发现。
2. 新增只读 API `POST /api/real-orders/discovery/search` 与配置状态 `GET /api/runtime/discovery`。未配置 discovery URL 时返回 HTTP 503 `discovery_not_configured`；当前没有实现注册/写入远端 Registry，避免在未确认 Wutong 鉴权契约时误写外部系统。
3. `IntegrationSettings.public_status()` 增加 Wutong discovery/registry/tenant 配置状态，不返回 URL 中的凭据；`.env.example` 标注 discovery 为可选只读接入。

**验证**：Wutong 客户端成功响应、租户请求头、远端错误和无 `acsMap` 数据缺口测试通过；后端全量 **106 passed**、`compileall` 和前端构建通过。当前运行环境 `WUTONG_DISCOVERY_URL` 为空，因此真实 API 验证为 HTTP 503 配置缺口，没有外部网络写入。

**边界与下一步**：跨实例调用仍需在获得 Wutong Registry 注册和鉴权契约后接入；当前 discovery 结果只读返回给业务侧，不改变协调者本地真实技能目录。

### 3.28 请求级 Bearer 身份会话门禁（2026-09-30）

**已完成**：

1. `require_real_identity` 读取请求 `Authorization: Bearer <token>`；请求会话存在时，身份解析优先使用该令牌调用 OpenMES `GET /api/auth/me`，而不是继续使用服务端集成 token。
2. 请求 Bearer 解析失败、格式错误或当前 provider 不支持会话时直接返回身份错误，禁止静默降级为 `Administrator` 等服务账号；CORS 同时允许 `Authorization` 请求头。
3. 没有请求会话时保留原有服务端 ERPNext/OpenMES 身份解析，兼容当前本地部署；`REAL_IDENTITY_PROVIDER=auto|openmes` 才接受请求 Bearer，会话主体和角色仍由上游返回。

**验证**：新增请求会话优先级、ERPNext provider 拒绝降级、嵌套 OpenMES 用户响应测试；后端全量 **108 passed**、`compileall` 通过。当前运行环境仍使用服务端 ERPNext `Administrator`（没有浏览器 Bearer 会话），因此没有新增真实系统写入。

**边界与下一步**：前端尚未内置企业 SSO 登录页；接入 OIDC/ERPNext/OpenMES 登录后，只需把短期 Bearer 会话附加到 API 请求即可复用现有角色门禁。长期 token 不写入项目配置或审计日志。

### 3.29 真实身份会话与 NCR 人工操作面板（2026-09-30）

**前端实现**：

1. `frontend/src/api.ts` 新增浏览器会话级 Bearer 注入：`sessionStorage` 仅保存短期会话，所有 API 请求自动附加 `Authorization: Bearer <token>`；新增本地写入令牌的会话级注入，仍不写入项目配置、URL、Agent 运行参数或日志。
2. `RealBusinessPage.tsx` 新增可选会话设置区，可保存/清除短期 Bearer 与 `REAL_WRITE_API_TOKEN` 对应的本地写入令牌，并重新解析后端身份；未配置时继续使用服务端集成身份，不伪造企业登录流程。
3. 质量面板新增真实 NCR 操作链：处置方案由人工选择（`scrap/rework/return_to_supplier/use_as_is`），依次执行“建立处置审批 → 批准 → OpenMES 写回”；关闭依次执行“读取 closure-check → 建立关闭审批 → 批准 → OpenMES 写回”。每一步展示审批号、身份来源、前置校验、失败原因、幂等命中和回读验证状态。
4. 写回成功后重新读取质量资料包与发运门禁，避免页面继续显示旧状态；任何失败都不会宣称真实系统已完成。

**验证**：

- `npm run build`：通过。
- `..\\.conda-env\\python.exe -m pytest -q tests`：**108 passed**。
- `python -m compileall -q app`、`git diff --check`：通过。
- 9000 实时只读探针：`GET /api/health`、`GET /api/runtime/surface`、`GET /api/real-orders/identity/me` 均 200；身份为 ERPNext `Administrator`。
- `GET /api/real-orders/quality/package/2` 返回 OpenMES 真实质量记录 `issue_id=1`；`GET /api/real-orders/quality/issues/1/closure-check?work_order_id=2` 返回 200，`status=ok`、`closure_ready=false`（`RESOLVED=true`、`disposition_recorded=false`、纠正措施已验证）。本子任务没有调用 disposition/close 写回接口，没有新增 ERPNext/OpenMES 业务记录。

**边界与下一步**：当前没有企业 OIDC/SSO 登录契约，前端只提供安全的短期 Bearer 注入入口；真实 NCR 仍需业务人员明确选择处置并具备角色与写入令牌后才能执行。下一步继续核对 Wutong Registry 注册契约，或在获得真实业务处置决策后做最小范围 NCR 写入回读验收；订单级质量放行仍保持 `NOT_SUPPORTED`。

### 3.30 Wutong Registry 只读契约适配（2026-09-30）

**实现内容**：

1. 根据仓库内 `acps-sdk-src/registry-server` 与 `acps-cli` 的真实路由确认 Registry 公共契约：`GET /health`、`GET /api/v1/agent/public/recent`；客户端支持根 URL 或 `/api/v1` URL，并透传可选 `X-Wutong-Tenant`。
2. 新增 `WutongRegistryClient`，严格校验 URL、响应 JSON 和 `items` 列表；HTTP/网络/格式错误转为明确集成错误，不回退本地 ACS 或协调者静态目录。
3. 新增 `GET /api/runtime/registry` 配置状态与只读声明，以及 `GET /api/real-orders/registry/agents?limit=5` 最近已审批 Agent 查询。没有 `WUTONG_REGISTRY_URL` 时返回 HTTP 503、`registry_not_configured`；没有实现 `/agent/client` 注册/更新/提交写操作。

**验证**：

- `tests/test_wutong.py`：**5 passed**（Discovery 3 + Registry 2），覆盖租户头、路径推导、健康/列表响应和缺失 `items` 不回退。
- `python -m compileall -q app`、`git diff --check`：通过。
- 重启 9000 后：`GET /api/health` 200；`GET /api/runtime/registry` 200，返回 `configured=false`、`read_only=true`、`writes_enabled=false`；`GET /api/real-orders/registry/agents` 返回 HTTP 503 `registry_not_configured`。当前 `WUTONG_REGISTRY_URL` 为空，没有外部网络访问或写入。

**边界与下一步**：Registry 账户/OIDC/mTLS 资料和注册审批策略仍未配置；在部署方明确认证契约前，继续保持只读。下一项优先处理真实 NCR 处置的业务输入与最小范围回读验收，或在取得 Registry 认证资料后扩展跨实例 AIP 调用。

### 3.31 页面级渲染检查（2026-09-30）

- 本地前端 `http://127.0.0.1:5173/` 刷新后真实业务页正常读取 9000：显示 ERPNext/OpenMES 真实连接、解析身份 `Administrator（ERPNext）`、客户/物料真实列表，页面不再出现 `Failed to fetch`。
- 通过页面键盘交互展开“会话设置”，确认 Bearer 会话、本地写入令牌输入框、保存/清除按钮和“仅当前浏览器会话保存”提示均可见；检查过程中没有输入令牌、没有提交表单、没有调用任何写入接口。
- NCR 操作控件已由 TypeScript 构建验证；真实工单 2 的 quality package 当前有 issue 1，页面进入跟单质量步骤后按该记录渲染处置/关闭门禁，处置下拉默认空值，不自动选择业务决策。
```

### 3.32 OpenMES 短时会话登录与身份噪音过滤（2026-09-30）

**实现内容**：

1. 按 vendored OpenMES 认证契约（`POST /api/auth/login`，Sanctum token，默认 15 分钟 TTL）新增 `backend/app/services/session_auth.py`：转发用户名/密码换取短时会话，并用登录令牌回读 `GET /api/auth/me` 解析业务身份（与请求级 Bearer 会话同一路径，共用 `resolve_openmes_identity`）。
2. 新增 `POST /api/real-orders/auth/login`：凭据只转发给已配置的 OpenMES 认证端点，不落日志、不持久化；上游 401/403/422 映射为 401 `invalid_credentials`，未配置 503 `not_configured`，空输入 422。刻意不代理 logout/refresh——OpenMES 的 logout 会吊销该上游用户全部 token、refresh 会轮换当前 token，都可能波及服务端集成令牌；本地登出只清除浏览器会话。
3. Laravel 契约适配：校验失败仅在携带 `Accept: application/json` 时返回 422 JSON，否则 302 回登录页 HTML（真实部署实测：无头 302 text/html，带头 422 application/json）；登录请求已显式携带该头。
4. 前端会话面板新增“OpenMES 账号登录（推荐）”表单：登录成功把短时令牌写入 `sessionStorage` 并重新解析身份；上游 `force_password_change` 约束如实提示；手工粘贴 Bearer 保留为后备入口。
5. ERPNext 身份噪音过滤：`_from_erpnext` 剔除 User 文档上随机生成的 10 位字母数字角色 docname（如 hn3orhl918），只影响展示，角色门禁不受影响（Administrator 角色列表 85 → 45 条，`Translator` 等真实角色保留）。

**验证**：

- `pytest tests -q`：**117 passed**（新增 session_auth 6 例 + ERPNext 噪音角色过滤 1 例；身份回读断言使用登录会话令牌而非服务端集成令牌）。
- `compileall`、`npm run build`、`git diff --check`：通过。
- 真实错误链路（页面级）：假凭据 → 后端 401 `invalid_credentials`（透传上游真实报错 "The provided credentials are incorrect."）→ 前端错误横幅显示；截图 `gui-test-screenshots/2026-09-30_openmes_session_login_error_path.png`。
- 真实身份接口：噪音剔除后 subject=Administrator、45 条角色、无随机 docname。
- 未执行任何 ERPNext/OpenMES 业务写入；未使用真实账号执行成功路径登录（部署未提供业务账号凭据）。

**边界与下一步**：

- ERPNext 侧用户名/密码登录依赖 frappe 会话/OIDC 契约，部署未提供，保持服务端集成身份不变；
- **成功路径已于本日完成 API 与页面双重验收**：部署只有 `admin` 一个 OpenMES 账号，经用户同意后通过 artisan 重置其密码；`POST /api/real-orders/auth/login` 返回 provider=openmes、subject=admin、roles=[Admin]、force_password_change=false（真实 OpenMES Sanctum 令牌）；页面上用该凭据登录成功，审批身份行变为 `Administrator（admin，OpenMES，角色：…）`，截图 `gui-test-screenshots/2026-09-30_openmes_session_login_success_page.png`；登录失败提示已改为会话面板内联显示（`fba5609`）；ERPNext 密码登录/OIDC 仍待部署契约；
- **登录会话的角色门禁配套**：OpenMES admin 会话原生只有 `Admin` 角色，会话下执行审批会被 403 拦截；已在部署 `.env` 配置 `REAL_IDENTITY_ROLE_MAP={"admin": ["sales_manager", "purchase_manager", "quality_manager"]}`（仅显式授予部署管理员业务审批角色，不提交仓库），重启后页面身份行显示 `Admin、purchase_manager、quality_manager、sales_manager`，会话内审批可用；
- NCR 真实写入最小范围验收仍等待业务处置决策 + 具备质量角色的会话 + 写入令牌。

### 3.33 检验/报工数据补录：已批准，交由外部执行（2026-09-30）

- 用户批准按演示故事线补录 `TEST_` 标记的检验记录与带实际耗时的报工数据（解锁质量检验维度展示与速率 ETA）；
- 执行方式由用户决定为**外部执行**，本项目不代跑；完整执行提示词（四条故事线设计、幂等要求、验收标准、文档收尾要求）已固化到 `docs/prompt_seed_test_data.md`；
- 本节为占位记录：外部执行完成后，由执行者在下方追加实际写入的表/接口、验收返回、测试结果与截图；在此之前检验维度仍如实显示"无数据"、ETA 保持 `DATA_MISSING`。

### 3.33 阶段九：质量待办面板（2026-09-30）

**实现内容**：

1. OpenMES 客户端新增 `list_issues(status, page)`（`GET /api/v1/issues`，契约以 vendored 源码 `routes/api.php:663` + `Api/V1/IssueController::index` 与 2026-09-30 实测 payload 为准：status 过滤、固定 20/页分页、`work_order.order_no`/`issue_type.severity`/`assigned_to` 用户对象或 null）；同时给 OpenMES 客户端全部请求补上 `Accept: application/json`（Laravel 认证/校验失败仅在带该头时返回 401/422 JSON，否则 302 HTML 使错误信息失真——与 §3.32 登录路径同根因，本次在只读路径一并修复）。
2. 适配器新增 `list_open_issues()`：OPEN/ACKNOWLEDGED/RESOLVED 三态聚合（CLOSED 不进待办），跟随 `meta.last_page` 翻页（封顶 10 页），按 reported_at 倒序、跨态去重；任一态读取失败直接抛出，不回退空列表。
3. `real_order.quality_todo_list()`：聚合 + 每条附 `reported_days`（按 reported_at 与当前时间差；缺失/无法解析如实返回 null）。
4. 新端点 `GET /api/real-orders/quality/todo`（只读，走 `require_real_identity`；集成错误按 `_integration_status_error` 映射，payload 无效 502 `openmes_issues_invalid`）。
5. 前端"质量待办"面板（智能协同问答下方）：工单号/标题/严重度/状态/处置/已报告天数（>3 天红色）/去处置；仅 OpenMES 登录会话（identity.provider=openmes）可见数据，否则显示"登录后查看质量待办"；空列表如实显示"当前没有未关闭质量问题"；失败显示原因 + 重试。
6. "去处置"：先反查工单关联 ERP 订单与持久化报价拿真实报价审批状态（不猜），再加载该工单 track/quality/ship-gate 并跳到第 8 步，滚动高亮该 issue 的既有 NCR 卡片（`ncr-issue-focus`）；NCR 审批处置流程零改动。

**验证**：

- `pytest tests -q`：**125 passed**（新增 test_quality_todo.py 8 例：三态聚合与字段映射、分页跟随、失败传播、reported_days 计算、端点接线与错误映射）；`compileall`、`npm run build`、`git diff --check` 通过。
- 真实接口验收：`GET /api/real-orders/quality/todo` 返回 2 条 OpenMES 真实 issue——issue 2（WO-2026-002，铸铁毛坯库存不足，CRITICAL，OPEN）与 issue 1（WO-2026-001，制动盘外径尺寸超差，MEDIUM，RESOLVED，disposition=pending，按验收要求出现在待办中）；`authority=OpenMES`、`data_source=openmes_issues`、`reported_days=2`。
- 页面验收（登录会话 admin/OpenMES）：待办表格两条真实记录渲染正常（截图 `gui-test-screenshots/2026-09-30_phase9_quality_todo_panel.png`）；点击"去处置"跳到第 8 步并高亮 issue 1 的 NCR 卡片，处置表单与"① 建立处置审批/读取关闭前置条件"按钮原样（截图 `2026-09-30_phase9_todo_go_dispose_ncr_focused.png`）。
- 无会话/过期会话不泄露数据：过期令牌下 identity 解析 503、面板仅显示登录提示，无数据渲染。
- 本阶段无任何 ERPNext/OpenMES 写入。

**遗留与下一步**：

- OpenMES 登录会话 15 分钟 TTL 过期后需重新登录（refresh 刻意不代理，见 §3.32）；过期时面板表现符合"如实报错"要求。
- 质量待办的"去处置"仍不代选处置方案；真实 disposition 写入验收仍等待业务决策（执行计划第 2 项）。
- issues 列表分页封顶 10 页（200 条/状态），超出时不再向后翻页（当前部署 2 条，余量充足）。

### 3.34 检验/报工数据补录（TEST_ seed，2026-09-30，依据 prompt_seed_test_data.md 执行）

**写入方式**（任务规则要求记录）：`materials` 表为空导致官方 `POST /api/v1/inspections`（要求已存在 material_id）不可用，因此统一经 `docker exec openmes-postgres psql` 直接写 `openmmes` 库；全部数据 `TEST_` 前缀、幂等可重跑（按 code/lot_number 查存在再插 + 固定时间戳覆盖写入）。**未触碰 ERPNext；未改动任何业务代码；未动 id=2（WO-2026-001）的任何记录。**

**补录内容**（`backend/seed_inspection_eta.py`）：

1. 检验线：1 个 TEST_ 物料（`TEST-BD-2401-SEED`）+ 3 条检验记录（`TEST-IQC-20260930-01` / `TEST-IPQC-20260930-01/02`，status=pass、disposition=accept、检验员 Administrator）；
2. ETA 线：工单 9（TEST_WO_PAGE_00023）批次 3（`TEST_LOT_PAGE_9`）及其步骤 `TEST_Final_Assembly` 补真实执行窗口 `2026-09-29 12:00→14:30 UTC`（150 分钟），并将此前官方 complete API 写入的 `actual_elapsed_minutes=30`（与窗口不一致）统一为 150，速率 1800 件/150 分钟 = **720 件/小时**；
3. 阻断线（id=2）与缺失线（SN 追溯）刻意不动。

**验收结果（对照 prompt_seed_test_data.md 五节）**：

1. `GET /api/real-orders/quality/package/9`：检验维度返回 3 条 TEST_ 记录（pass/accept，inspection_id 7/8/9）；SOP/Control Plan released、`quality_gate_passed=true`；
2. `GET /api/real-orders/mes/track/9`：`eta_status=RATE_BASED`、`eta_basis=observed_production_rate`、`observed_rate={quantity:1800.00, elapsed_minutes:150.0, units_per_hour:720.0, remaining_qty:200.00, estimated_remaining_hours:0.28}`；
3. 协调者问答（DeepSeek）"SAL-ORD-2026-00023 什么时候能做完"：回答含 RATE_BASED 估算（2026-09-30 06:08 UTC）、真实记录编号与调用链 2 步留痕（`RUN-COORD-1F88B4AF6609`：lookup_order_link → track_real），并主动提示"ETA 为实测外推、工单状态仍 PENDING"的口径提醒；"质量检验数据怎么样"回答 3 条检验记录全部合格 + 如实标注"无订单级质量放行 API / 无 SN 追溯 API"缺口；
4. id=2 阻断线不受影响：`quality/package/2` 仍 `open_issues=0` + SOP/Control Plan 缺失；`mes/track/2` 仍 `completion=0.0%`、`eta_status=DATA_MISSING`；
5. `pytest tests -q` **125 passed**、`compileall` 通过、`npm run build` 通过（本阶段零业务代码改动）；
6. seed 脚本重复执行 3 次，materials=1、inspections=3、批次时间戳不变，无重复记录；
7. 截图：`gui-test-screenshots/2026-09-30_seed_eta_rate_based_answer.png`（ETA 展示）、`2026-09-30_seed_inspection_data_answer.png`（检验数据展示）。

**遗留与说明**：

- seed 过程中发现并修复脚本自身 bug（scalar() 双次执行导致 INSERT 重复），已清理重复数据并复验幂等；
- 批次步骤 `duration_minutes` 同步写为 150；批次级 started/completed 同步更新；
- 工单 9 状态仍为 PENDING（与 90% 进度不一致）——协调者已在回答中如实提示，状态口径属 OpenMES 业务数据，不在本 seed 范围内擅改。

### 3.35 NCR 真实写入最小范围验收 + 演示加固（2026-09-30）

**NCR 处置真实写入（用户批准处置 = 返工 rework）**：

1. 前置就绪：`REAL_WRITE_API_TOKEN` 已配置（写入门禁激活，无效令牌 403）；OpenMES admin 登录会话（REAL_IDENTITY_ROLE_MAP 授予 quality_manager）。
2. 页面真实执行链（身份 admin · 来源 OpenMES）：质量待办 → "去处置" issue 1 → 人工填表（rework / 不合格数量 1800 / NC 来源=内部 / 根因 / 遏制措施）→ ① 建立处置审批（**审批号 QDISP-1BC2B3498AAE**）→ ② 批准 → ③ 写回 OpenMES。
3. 验收中发现并修复一个真 bug：首次写回的回读比对把 `non_conforming_qty` 按**字符串**比较（审批载荷 JSON number → `1800.0`，OpenMES 回读 → `"1800"`），OpenMES PUT 实际已成功写入，但被误报 `WRITE_UNVERIFIED`。修复为 Decimal 数值比较（`_same_quantity`），未验证分支补充明确错误信息"已写入但回读比对不一致，请人工核对"；新增回归测试 `test_disposition_qty_format_difference_does_not_fail_verification`。修复后页面重按 ③ → `DISPOSITION_RECORDED · 回读已验证 · 幂等命中`，卡片状态"已登记处置"。
4. OpenMES 真实状态回读确认：issue 1 `status=RESOLVED`、`disposition=rework`、root_cause/containment_action/nc_source=internal/non_conforming_qty=1800 全部与审批载荷一致；质量待办行同步显示 `rework`。
5. 关闭前置条件：`closure_ready=true`（问题已解决/已登记处置/已记录根因/已记录遏制措施/纠正措施已验证 五项全 ✓）。按最小范围验收**不执行 close**（保留 issue 1 在待办中作演示；close 链路审批/写回/回读代码与门禁就绪）。
6. id=2 阻断线无扰动（0% 进度 + SOP/Control Plan 缺失不变）。

**演示加固**：

1. `scripts/start_demo.ps1` 一键启动 + 环境预检：幂等（端口占用跳过）、等待就绪、真实连通性检查（后端/表面/ERPNext/OpenMES/身份/DeepSeek/写入令牌），预检失败如实退出。实测全部通过（UTF-8 BOM 修正 PowerShell 5.1 解析、OpenMES check 端点改 POST）。
2. 加急场景（例子四）页面级验收补齐：提问"客户要求把 SAL-ORD-2026-00023 提前交货……" → 三维度结论（`can_ship=true`：报价已审批 + 质量门禁通过 + 完成率 90%）+ 方案 A/B 成本风险对比（真实价格记录，方案 `PROC-5585148630C1`）+ 边界如实说明（"最多提前多少被物料交期锁死 15 天，剩余 200 件完工数据不足不编造"）。截图 `gui-test-screenshots/2026-09-30_expedite_scenario_page_acceptance.png`。
3. `docs/demo_script.md` 演示剧本：六条故事线（动态协同/速率 ETA/缺料双审批/质量待办+处置写回/加急/诚实性）+ 关键记录编号速查 + 注意事项。

**验证**：`pytest tests -q` **126 passed**（新增 1 例）、`compileall`、`npm run build` 通过。本阶段真实写入：OpenMES issue 1 处置字段（经审批）；ERPNext 无写入。

**遗留**：close 写回链路未真实执行（有意保留待办演示项）；工单 9 状态 PENDING 与 90% 进度不一致属 OpenMES 业务数据口径，协调者会如实提示。

### 3.36 全功能本地重测报告（2026-09-30，用户授权全量重测含故障注入）

**范围与方法**：6 组共 49 项检查 + 1 次真实写回链重跑，覆盖单元/接口/页面/LLM 问答/幂等门禁/故障注入。零业务代码改动；含一次 OpenMES 停机注入与一次 DeepSeek key 摘除注入（均已恢复）。

| 组 | 项数 | 结果 |
|---|---|---|
| T1 基础回归（pytest/compileall/npm build） | 3 | ✅ 3/3（126 passed） |
| T2 真实接口逐项探针（健康/表面/身份/待办/质量包×2/ETA×2/发运门禁×2/报价/方案/运行记录/关闭前置/ERP 客户/OpenMES 工单） | 17 | ✅ 17/17 |
| T3 页面级（登录会话/待办渲染/无会话不泄露/8 步表单 ERP 数据/运行记录面板/MES 完工页） | 6 | ✅ 6/6 |
| T4 协调者问答（ETA 速率线/诚实缺数线/质量协同线/报价线/SN 诚实缺口） | 5 | ✅ 5/5 |
| T5 幂等与门禁（重复报价审批幂等复用 APPR-2DD01946C804/错写入令牌 403/缺令牌 403/Mock 表面 404 mock_surface_disabled/非 Bearer 401/NCR 全链重走幂等命中） | 6 | ✅ 6/6 |
| T6 故障注入（停 openmes-backend：todo 502、package 500、track NOT_FOUND+如实缺口，不回退空数据，恢复验证 ✓；摘 DEEPSEEK_API_KEY：ask 503 llm_not_configured"不使用固定话术伪造回答"，恢复验证 ✓；Mock 404） | 3 | ✅ 3/3 |

**关键证据**：

- T5.1 NCR 全链重走：新审批 `QDISP-95702E8DA994` → 批准 → 写回 → `DISPOSITION_RECORDED · 回读已验证`（OpenMES 已是 rework，数据级幂等，无重复写入）；
- T2.5 质量待办 2 条（#1 RESOLVED/rework 即本次写回结果）；T2.9 track/9 `RATE_BASED` 720 件/h；T2.10 id=9 `can_ship=true` 与 T2.11 id=2 正确拦截对照；
- T4.3 质量问答正确反映写回后现状（RESOLVED + 返工已登记），并如实指出真正阻塞是"文档缺失 + 未开工"；
- T4.4 报价链引用真实价格记录（ERPNext Standard Selling 85 元/件）。

**发现与结论（无系统缺陷，3 条小项）**：

1. 探针笔误：`/api/real-orders/mes/work-orders` 不存在（404），正确端点为 `/api/mes/work-orders`（9 条工单）——测试脚本问题，非系统缺陷；
2. 错误码不一致（改进项）：OpenMES 停机时 `quality/todo` 502 而 `quality/package` 500，均如实失败但映射不统一，后续可统一为 502；
3. 措辞精度（改进项）：MES 停机时 `track` 返回 `NOT_FOUND`+"工单不存在"，更准确表述应为"MES 不可达"；不构成数据伪造。
4. 测试过程新增 1 条 NCR 处置审批记录（QDISP-95702E8DA994，幂等写回），审计留痕属正常业务数据。

**结论**：全部功能在本地真实环境重测通过；两条演示线（id=2 阻断 / id=9 通过）、治理门禁、诚实性故障行为均符合设计。

### 3.37 阶段十：工厂级工程质感（2026-09-30，比赛定位）

**定位**：仅为比赛——目标是让系统在评委审视下站得住"工厂级"标准，不做真实试点部署（HTTPS/真实账号/监控告警等生产部署项按定位跳过）。

**10.1 NCR 工作流状态刷新后恢复**：新只读端点 `GET /api/real-orders/quality/workflow-states`（按 issue 聚合最近的处置/关闭审批，含载荷里的 work_order_id/disposition）；前端在质量面板加载（loadTracking / 去处置）后自动合并恢复审批号、批准态与表单处置值，不覆盖进行中的状态。页面刷新后处置进度不再丢失，配合数据层幂等不会重复建立审批。实测：issue 1 正确恢复 `QDISP-95702E8DA994`。

**10.2 会话过期提醒**：登录时在 sessionStorage 记录签发时间；会话有效且超过 13.5 分钟（15 分钟 TTL 前 90 秒）显示黄色提醒横幅，20 秒轮询；无签发时间的历史会话保守立即提醒。过期后既有兜底（面板回退登录提示）不变。

**10.3 错误码统一与措辞修正**：`quality/package` 增加 IntegrationError 映射（OpenMES 停机 500 → 502）；`track_order` 前置 `get_work_orders_strict` 可达性探测——MES 不可达时返回 `status=MES_UNREACHABLE`、`eta_basis=mes_unreachable` + 如实缺口，不再把"连不上"误报成"工单不存在"（真不存在的工单仍报 NOT_FOUND）。

**10.4 检验维度按批次 lot 关联**：`quality_package` 拉取工单批次 lot，检验记录按"精确相等或前缀扩展"过滤（全局计数保留在 `gate_details.inspections_total`），`inspections_scope` 标注口径；批次读取失败时如实返回空集。adapter 的 inspections 映射补 `lot_number`。seed 补录 lot 改为批次前缀（`TEST_LOT_PAGE_9-IQC/…`）。实测 package/9：`inspection_count=3 / total=3 / scope=work_order_batch_lots`；package/2（无批次）如实为 0。开发中发现并修复自建过滤器的"精确相等漏掉前缀扩展"缺陷，以单元测试锁定。

**10.5 组件拆分（第一批）**：纯函数 `formatAssistantAnswer` 抽出至 `src/lib/markdown.ts`；质量待办面板抽出为 `src/components/QualityTodoPanel.tsx`（props 化），主文件瘦身并建立 lib/ + components/ 结构，后续面板按同模式继续。

**10.6 前端关键行为测试（vitest，6 例）**：markdown 渲染（HTML 转义防注入/加粗/表格）、会话令牌（sessionStorage 隔离/空白清除/签发时间）、写入头注入（X-Real-Write-Token + 幂等键）。`npm test` 脚本可用。

**10.7 业务库迁移 PostgreSQL**：专用容器 `autoparts-db`（postgres:17-alpine，15432）；一次性迁移脚本 `backend/migrate_sqlite_to_postgres.py`（按外键拓扑排序、pg 列类型驱动的布尔强转、幂等跳过非空表），17 表 2765 行迁入；alembic stamp head 后应用启动校验通过。实测：报价 52 / 方案 27 / 协调者运行 50 / 工作流恢复，全部从 PG 读取。SQLite 文件保留作回退（.env 的 DATABASE_URL 一行切换）。测试套件仍使用独立临时库，不受影响。

**验证**：后端 **128 passed**（新增 2 例：工作流恢复、检验关联过滤）、`compileall` 通过；前端 vitest **6 passed** + `npm run build` 通过。本阶段无 ERPNext/OpenMES 业务写入（仅本地业务库迁移）。

**遗留（阶段十未完成项）**：主文件剩余面板（问答/NCR/8 步流程）继续按 10.5 模式拆分；CI 配置；问答流式输出（可选）。

### 3.38 交接状态（2026-09-30，交由下一任 AI 继续）

> 本节是交接快照：正在做的事、已完成的事、后面要做的事。接手者从这里开始，先读本节再读 §3.37 及之前各节。

#### 一、当前系统状态（交接时实测）

- 分支 `codex/real-integration-layer`，全部工作已提交（最新 `d581a30`），工作区仅 4 个约定不提交的本地文件（见下）。
- 测试基线：后端 `pytest tests -q` **128 passed**；前端 `vitest` **6 passed** + `npm run build` 通过；`compileall` 通过。
- 运行时（全部健康）：后端 9000（业务库已切 PostgreSQL `autoparts-db` 容器，端口 15432）、前端 5173、OpenMES（caddy/backend/reverb/postgres）与 ERPNext 容器组运行中。
- 演示入口：`powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1`（幂等 + 环境预检），剧本 `docs/demo_script.md`（六条故事线）。

#### 二、正在做的事（阶段十"工厂级工程质感"，比赛定位）

阶段十 7 项中 **5 项完成、2 项部分/未开始**，交接点如下：

| 任务 | 状态 | 交接说明 |
|---|---|---|
| 10.1 NCR 工作流刷新后恢复 | ✅ 完成 | `GET /api/real-orders/quality/workflow-states` + 前端自动合并恢复 |
| 10.2 会话过期提醒 | ✅ 完成 | 13.5 分钟阈值 + 20s 轮询横幅 |
| 10.3 错误码统一 + MES 不可达措辞 | ✅ 完成 | package 502 映射；track `MES_UNREACHABLE` |
| 10.4 检验按批次 lot 关联 | ✅ 完成 | 精确相等/前缀扩展均关联；seed 已对齐 |
| 10.5 前端组件拆分 | 🟡 **第一批完成，进行中** | 已抽出 `src/lib/markdown.ts` + `src/components/QualityTodoPanel.tsx`；**剩余**：问答面板、NCR 面板、8 步流程面板、Agent 运行记录面板，按同模式继续（props 化、纯函数进 lib/、每抽一个跑 vitest+build） |
| 10.6 前端关键行为 vitest | ✅ 完成 | `npm test` 可用（6 例） |
| 10.7 业务库迁移 PostgreSQL | ✅ 完成 | 17 表 2765 行迁入；`migrate_sqlite_to_postgres.py` 幂等；SQLite 文件保留回退（.env 的 DATABASE_URL 一行切回） |

#### 三、后面要做的事（优先级从高到低，接手者按序做）

1. **完成 10.5 剩余组件拆分**（进行中的任务）：RealBusinessPage.tsx 仍约 2200 行；每抽一个面板：跑 `npx vitest run` + `npm run build`，页面冒烟（问答提问一次 + 待办渲染），完成后更新本文件与 next_development_plan.md 再继续下一个。
2. **CI 配置**（未开始）：GitHub Actions 单 workflow——backend（pytest+compileall，注意 conda 环境在自托管机器；或改用 requirements 安装）、frontend（vitest+build）。仓库无 CI 时保持最小：至少跑前端两项（纯 Node）。
3. **可选：问答流式输出（SSE）**：协调者回答 20-30 秒干等，流式可改善演示观感；改动点：后端 ask 端点改 StreamingResponse，前端问答面板增量渲染。不改也不阻塞演示。
4. **长期遗留（有外部依赖，勿擅自推进）**：Wutong Registry 写路径（等部署方鉴权/租户契约）；完整 OIDC/SSO（等契约，现有 OpenMES 账号登录已够演示）；NCR close 链路真实执行（**有意保留** issue 1 在待办中做演示，勿关闭）；订单级质量放行/SN 追溯（OpenMES 无 API，保持 NOT_SUPPORTED）。
5. **演示前检查**（若要再次演示）：跑 start_demo.ps1 预检全绿；登录会话用 OpenMES 账号 admin（密码已于 2026-09-30 重置，具体值询问用户，勿写入文档/代码/提交）。

#### 四、接手必读的约束（铁律，与项目历史一致）

- 所有业务数据必须来自本地真实 ERPNext/OpenMES；接口失败/无数据如实报错，禁止空数组/编造冒充；不绕过审批门禁（写入=人工审批→写回→回读→幂等）。
- 测试命令：`cd backend && ../.conda-env/python.exe -m pytest tests -q`（基线 128，不许下降）；`../.conda-env/python.exe -m compileall -q app`；`cd frontend && npm test && npm run build`。
- 每完成一个子任务：先更新 `docs/current_status_and_fix_plan.md`（追加 § 小节）与 `docs/next_development_plan.md`（任务表勾稽），再 git 提交（逐个 add，勿 `git add -A`）。
- 不提交：`backend/ask_a6.json`、`backend/ask_q6.json`、`backend/uvicorn-9000.log`、`backend/uvicorn-9000.err.log`。
- 不输出/提交 `.env` 中的任何密钥（ERPNEXT_*/OPENMES_*/DEEPSEEK_API_KEY/REAL_WRITE_API_TOKEN/DATABASE_URL 等）。
- 后端重启模式：杀 9000 监听进程 → `cd backend && ../.conda-env/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 9000`。
- 业务库现为 PostgreSQL（autoparts-db 容器）：若容器未启动，`docker start autoparts-db`；数据迁移历史见 §3.37。
