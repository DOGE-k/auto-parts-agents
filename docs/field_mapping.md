# 【已归档，不作为当前验收依据】ERP/MES 字段映射文档

> 该文档中的 OpenMES 工单数量、接口通过和前端构建结论已被后续实际复核部分推翻。当前请以 `docs/current_status_and_fix_plan.md` 为准；本文件仅保留历史映射记录。

> 生成时间: 2026-09-28
> 验证后端端口: 9000 (APP_ADAPTER_MODE=real)

## 1. ERPNext (ERP)

### 1.1 连接配置

| 配置项 | 环境变量 | 值 |
|--------|----------|-----|
| 基地址 | `ERPNEXT_BASE_URL` | `http://localhost:8080` |
| API Key | `ERPNEXT_API_KEY` | 已配置 |
| API Secret | `ERPNEXT_API_SECRET` | 已配置 |
| 草稿写入 | `ERPNEXT_DRAFT_WRITES_ENABLED` | `false` (只读) |

### 1.2 客户查询

- **来源系统**: ERPNext
- **API 路径**: `GET /api/resource/Customer/{name}`
- **请求参数**: `name` = 客户名称 (如 "上汽集团")
- **返回字段映射**:

| ERPNext 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `name` | `customer_id` | 客户唯一标识 |
| `customer_name` | `customer_name` | 客户名称 |
| `customer_group` | `customer_group` | 客户分组 |
| `territory` | `territory` | 销售区域 |
| `default_currency` | `currency` | 币种 (默认 CNY) |
| `payment_terms` | `payment_terms` | 付款条件 |
| - | `authority` | 固定值 "ERPNext" |
| - | `found` | 是否找到 |

### 1.3 物料查询

- **来源系统**: ERPNext
- **API 路径**: `GET /api/resource/Item/{item_code}`
- **请求参数**: `item_code` = 物料编码 (如 "BD-2401")
- **返回字段映射**:

| ERPNext 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `name` | `item_id` | 物料唯一标识 |
| `item_name` | `item_name` | 物料名称 |
| `item_group` | `item_group` | 物料分组 |
| `description` | `description` | 描述 |
| `stock_uom` | `stock_uom` | 库存单位 (Nos) |
| `is_stock_item` | `is_stock_item` | 是否库存物料 (1/0) |
| - | `authority` | 固定值 "ERPNext" |

### 1.4 价格查询

- **来源系统**: ERPNext
- **API 路径**: `GET /api/resource/Item Price?fields=[...]&filters=[["item_code","=",{item_code}]]`
- **返回字段映射**:

| ERPNext 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `name` | `price_id` | 价格记录 ID |
| `item_code` | `item_id` | 物料编码 |
| `price_list` | `price_list` | 价格表名称 |
| `price_list_rate` | `unit_price` | 单价 |
| `currency` | `currency` | 币种 |
| `valid_from` | `valid_from` | 生效日期 |
| `valid_upto` | `valid_upto` | 失效日期 |
| - | `authority` | 固定值 "ERPNext" |

### 1.5 BOM 查询

- **来源系统**: ERPNext
- **API 路径**: `GET /api/resource/BOM?fields=["name","item","quantity","is_active"]&filters=[["item","=",{item_id}],["is_active","=",1]]`
- **详情路径**: `GET /api/resource/BOM/{bom_name}`
- **返回字段映射**:

| ERPNext 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `name` | `bom_id` | BOM ID (如 "BOM-BD-2401-001") |
| `item` | `item_id` | 父物料编码 |
| `quantity` | `quantity` | BOM 基准数量 |
| `is_active` | - | 是否活跃 (1=是) |
| items[].item_code | items[].item_code | 子物料编码 |
| items[].item_name | items[].item_name | 子物料名称 |
| items[].qty | items[].qty_per_product | 单位用量 |
| items[].uom | items[].uom | 单位 |
| items[].source_warehouse | items[].source_warehouse | 来源仓库 |
| - | `authority` | 固定值 "ERPNext" |

### 1.6 库存查询

- **来源系统**: ERPNext
- **API 路径**: `GET /api/resource/Bin?fields=[...]&filters=[["item_code","=",{item_code}]]`
- **返回字段映射**:

| ERPNext 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `item_code` | `item_id` | 物料编码 |
| `warehouse` | `warehouse` | 仓库 (如 "Stores - APM") |
| `actual_qty` | `actual_qty` | 实际库存量 |
| `reserved_qty` | `reserved_qty` | 预留量 |
| `ordered_qty` | `ordered_qty` | 订单量 |
| `projected_qty` | `projected_qty` | 预测量 |
| - | `authority` | 固定值 "ERPNext" |

### 1.7 数据权限

| 操作 | 允许 | 说明 |
|------|------|------|
| 读取客户/物料/BOM/价格/库存 | ✅ | 只读 |
| 创建报价草稿 | ❌ | `ERPNEXT_DRAFT_WRITES_ENABLED=false` |
| 创建销售订单草稿 | ❌ | 同上 |
| 创建采购订单草稿 | ❌ | 同上 |

## 2. OpenMES (MES)

### 2.1 连接配置

| 配置项 | 环境变量 | 值 |
|--------|----------|-----|
| 基地址 | `OPENMES_BASE_URL` | `http://localhost` |
| 用户 Token (Bearer) | `OPENMES_TOKEN` | 已配置 |
| ERP API Key (X-Api-Key) | `OPENMES_ERP_API_KEY` | 已配置 |

### 2.2 认证方式

| 端点类型 | 认证方式 | 用途 |
|----------|----------|------|
| `/api/v1/*` | `Authorization: Bearer {token}` | 工单、产线、产品类型等业务数据 |
| `/api/v1/erp/*` | `X-Api-Key: {key}` | ERP 集成专用（生产完工、质量问题） |
| `/api/auth/login` | 无认证 | 获取 Bearer Token |

### 2.3 工单查询

- **来源系统**: OpenMES
- **API 路径**: `GET /api/v1/work-orders` (Bearer)
- **筛选参数**: `status`, `search`, `per_page`, `page`
- **返回字段映射**:

| OpenMES 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `id` | `work_order_id` | 工单 ID |
| `order_no` | `work_order_no` | 工单编号 (如 "WO-2026-001") |
| `product_type.code` | `product_id` | 产品编码 |
| `product_type.name` | `product_name` | 产品名称 |
| `planned_qty` | `quantity` | 计划数量 |
| `produced_qty` | `completed_qty` | 已完工数量 |
| - | `rejected_qty` | 不良数量 (字段不存在，默认 0) |
| `status` | `status` | 状态 (CREATED/ACCEPTED/IN_PROGRESS/COMPLETED/BLOCKED) |
| `priority` | `priority` | 优先级 |
| `due_date` | `due_date` | 交期 |
| `line_id` | `line_id` | 产线 ID |
| `line.name` | `line_name` | 产线名称 |
| `planned_start_at` | `planned_start` | 计划开始时间 |
| `planned_end_at` | `planned_end` | 计划结束时间 |
| `completed_at` | `actual_start` | 完工时间 |
| - | `authority` | 固定值 "OpenMES" |
| - | `data_source` | 固定值 "openmes_api" |

### 2.4 工单进度查询

- **来源系统**: OpenMES
- **API 路径**: `GET /api/v1/work-orders/{id}` (Bearer)
- **说明**: OpenMES 无独立工序端点，用工单级别进度作为整体工序
- **返回字段映射**:

| OpenMES 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `planned_qty` | `planned_qty` | 计划数量 |
| `produced_qty` | `completed_qty` | 已完工数量 |
| `packed_qty` | `packed_qty` | 已包装数量 |
| `status` | `status` | 工单状态 |
| `line.name` | `work_center` | 工作中心（产线名称） |
| `planned_start_at` | `start_time` | 计划开始时间 |
| `planned_end_at` | `end_time` | 计划结束时间 |
| `due_date` | `due_date` | 交期 |
| - | `authority` | 固定值 "OpenMES" |

### 2.5 质量记录查询

- **来源系统**: OpenMES
- **API 路径**: `GET /api/v1/erp/quality/issues` (X-Api-Key)
- **补充**: 工单详情中的 `issues` 嵌套数组 (Bearer)
- **返回字段映射**:

| OpenMES 字段 | 内部字段 | 说明 |
|--------------|---------|------|
| `id` | `record_id` | 质量问题 ID |
| `issue_type.name` | `record_type` | 问题类型名称 |
| `issue_type.code` | `issue_code` | 问题类型编码 |
| `work_order_id` | `work_order_id` | 关联工单 ID |
| `title` | `title` | 问题标题 |
| `description` | `description` | 问题描述 |
| `severity` | `severity` | 严重程度 (CRITICAL/HIGH/MEDIUM/LOW) |
| `status` | `status` | 状态 (OPEN/ACKNOWLEDGED/RESOLVED/CLOSED) |
| `disposition` | `disposition` | 处置状态 (pending等) |
| `non_conforming_qty` | `non_conforming_qty` | 不良数量 |
| `root_cause` | `root_cause` | 根本原因 |
| `containment_action` | `containment_action` | 围堵措施 |
| `reported_at` | `reported_at` | 报告时间 |
| `resolved_at` | `resolved_at` | 解决时间 |
| - | `authority` | 固定值 "OpenMES" |

### 2.6 产线查询

- **来源系统**: OpenMES
- **API 路径**: `GET /api/v1/lines` (Bearer)
- **返回字段**: `id`, `code`, `name`, `description`, `is_active`, `workstations_count`, `work_orders_count`

### 2.7 生产完工事件

- **来源系统**: OpenMES
- **API 路径**: `GET /api/v1/erp/production/completions` (X-Api-Key)
- **说明**: 当前无完工数据 (0 items)

### 2.8 数据权限

| 操作 | 允许 | 说明 |
|------|------|------|
| 读取工单/产线/产品类型 | ✅ | Bearer Token 认证 |
| 读取质量记录/生产完工 | ✅ | X-Api-Key 认证 |
| 修改工单状态/优先级 | ❌ | 写操作默认禁用 |
| 创建返工/补料请求 | ❌ | 默认禁用 |

## 3. 测试数据清单

### 3.1 ERPNext 数据

| 数据类型 | 数量 | 示例 |
|----------|------|------|
| 公司 | 1 | AutoParts Manufacturing (APM) |
| 客户 | 3 | 上汽集团, 比亚迪, 长城汽车 |
| 物料 | 9 | BD-2401(制动盘), CI-RAW(铸铁毛坯), M10-BOLT(螺栓) |
| 供应商 | 3 | 上海铸锻厂, 宁波紧固件, 江苏轴承 |
| BOM | 1 | BOM-BD-2401-001 (4个子项) |
| 价格 | 9 | BD-2401 @ 85.00 CNY |
| 库存 (Bin) | 4 | CI-RAW:800, M10-BOLT:5000, SEAL-RING:2000, BRG-6204:1500 |
| 销售订单 | 1 | SAL-ORD-2026-00001 (上汽集团, BD-2401, 500件) |
| 物料需求 | 1 | MAT-MR-2026-00001 (CI-RAW:550, M10-BOLT:2200) |
| 库存过账 | 1 | MAT-STE-2026-00001 (docstatus=1, 已提交) |

### 3.2 OpenMES 数据

| 数据类型 | 数量 | 示例 |
|----------|------|------|
| 产品类型 | 6 | BD-2401, BD-2402, SK-3401, TS-4501, CP-5601, DEMO_BRACKET_001 |
| 产线 | 1 | DEMO Assembly Line 01 |
| 工单 | 4 | WO-2026-001(BD-2401,500), WO-2026-002(SK-3401,300), WO-2026-003(TS-4501,200) |
| 质量问题 | 2 | 制动盘外径超差(MEDIUM), 铸铁毛坯库存不足(CRITICAL) |

## 4. API 端点验证结果

| 端点 | 方法 | 状态 | authority | 备注 |
|------|------|------|-----------|------|
| `/api/health` | GET | ✅ | - | 数据库连接正常 |
| `/api/integrations/status` | GET | ✅ | - | mode=real, ERP/MES 均配置 |
| `/api/erp/customers/{name}` | GET | ✅ | ERPNext | found=true |
| `/api/erp/items/{code}` | GET | ✅ | ERPNext | found=true |
| `/api/erp/items/{code}/prices` | GET | ✅ | ERPNext | 返回价格列表 |
| `/api/erp/items/{code}/bom` | GET | ✅ | ERPNext | found=true, 4 子项 |
| `/api/erp/inventory` | GET | ✅ | ERPNext | 4 条库存记录 |
| `/api/mes/work-orders` | GET | ✅ | OpenMES | 4 个工单 |
| `/api/mes/work-orders/{id}/progress` | GET | ✅ | OpenMES | 整体进度 |
| `/api/mes/quality-records` | GET | ✅ | OpenMES | 2 条质量记录 |
| `/api/mes/production-documents` | GET | ✅ | OpenMES | 0 条 (无虚构占位) |

## 5. 禁止项确认

- ✅ 返回数据中不含 `synthetic_demo_only` 标记
- ✅ ERP 查询 authority 为 "ERPNext"，不是 "MockERP"
- ✅ MES 查询 authority 为 "OpenMES"，不是 "MockMES"
- ✅ 连接失败时记录错误日志，不静默退回 Mock
- ✅ `APP_ADAPTER_MODE=real` 时，未配置凭证抛出 IntegrationNotConfigured
- ✅ 前端构建无 `tsconfig.tsbuildinfo` EPERM 错误
