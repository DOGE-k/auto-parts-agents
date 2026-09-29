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

当前验收结论（2026-09-29 第二次更新，依据 3.13/3.14）：

```text
四 Agent 真实业务链已端到端跑通；报价→采购→跟单→质量→发运门禁全链可用
页面级双场景验收完成：场景 A（0% 进度）正确阻断、场景 B（90%+文档+审批）正确放行，均有截图证据
动态协同（阶段五）已完成并验收：自然语言提问→协调智能体经 AIP 动态调用四个真实智能体→
带证据的中文回答；两问产生两条不同调用链（动态拓扑达成）；调用链与运行记录可查
测试与安全：pytest 独立测试库（52 passed）、失败验证矩阵补齐、无硬编码凭据
仍余（生产可用验收前）：质量写回真实身份/角色治理、close/disposition 闭环、检验数据补录决策、
旧 Mock 场景后端隔离、速率 ETA、Wutong 外部发现与 ACS 文件同步
```
