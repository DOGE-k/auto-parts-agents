# 【已归档，不作为当前验收依据】汽车零部件工厂四智能体项目：AI 接手开发说明

> 该交接说明是上一阶段版本。当前问题和任务已更新到 `docs/current_status_and_fix_plan.md`；本文件仅保留历史交接内容。

更新时间：2026-09-28

项目路径：`E:\competition\汽车零部件工厂智能体开发`

## 1. 用户真正要实现的目标

用户所说的“demo”不是用固定数据做演示，而是：

> 连接本地已经部署的 ERP 和 MES，用真实业务数据跑通报价、采购、跟单、质量文档四个 Agent 的业务流程。

因此，`MockERP`、`MockMES`、`DEMO_*`、固定 fixture 和 `synthetic_demo_only` 只能保留为开发测试工具，不能作为最终业务主流程。

## 2. 当前项目状态

当前分支：`codex/real-integration-layer`

已经存在：

- FastAPI 后端和 React 前端
- 报价、采购、跟单、质量文档四个 Agent 的基础能力
- ERPNext 客户端和适配器
- OpenMES 客户端和适配器
- 本地 AIP RPC 端点
- 审批、事件时间线、审计记录、幂等键和版本校验
- `docs/field_mapping.md` 字段映射文档

当前 `.env` 使用 `APP_ADAPTER_MODE=real`，ERPNext 和 OpenMES 的凭据已经配置在本机环境中。不要在聊天、提交记录或文档中输出凭据。

## 3. 已经实际验证的结果

### ERPNext

以下接口已经实际返回真实 ERPNext 数据：

- 客户：上汽集团
- 物料：`BD-2401`
- BOM：`BOM-BD-2401-001`，返回 4 个子项
- 库存：`CI-RAW`、`M10-BOLT` 等
- ERP 登录检查成功，用户为 Administrator
- 返回数据的 authority 为 `ERPNext`

### OpenMES

以下部分已经实际返回 OpenMES 数据：

- 质量记录接口可读取
- 生产完工接口可访问，当前返回 0 条
- 返回数据的 authority 为 `OpenMES`

但是 OpenMES 仍未完全接通：

- `/api/mes/work-orders` 当前返回空列表
- `/api/integrations/openmes/work-orders` 当前返回 502
- `/api/integrations/openmes/check` 当前返回 502
- 错误信息是远端返回内容不是有效 JSON

因此不能声称 OpenMES 已经完整接入。必须先解决工单接口和健康检查问题。

### 测试

- `backend/tests` 下真实连接契约测试：7 passed
- 前端构建仍然被 `frontend/tsconfig.tsbuildinfo` 的 `EPERM` 阻塞
- 完整后端测试仍包含旧的 Mock/异步测试问题，不能只根据 7 个连接测试判断项目完成

## 4. 当前仍未完成的核心工作

### 4.1 四个 Agent 主流程仍然是 Mock

前端当前主入口仍然调用：

```text
/api/scenarios/{name}/run
```

该流程位于 `backend/app/runtime/scenarios.py`，仍然使用：

- MockERP
- MockMES
- 固定 fixture
- `synthetic_demo_only`
- 固定演示规则

这条流程只能作为回归测试，不能继续作为真实业务入口。

### 4.2 真实 ERP/MES 尚未形成业务闭环

目前还没有验证完成：

- 真实 ERP 客户/物料/BOM/价格/库存 → 报价 Agent
- 报价审批 → ERP 报价或销售订单草稿
- ERP 物料需求 → 采购 Agent
- ERP 销售订单 → MES 工单关联
- MES 工单/工序/产能 → 跟单 Agent
- MES 质量记录/NCR/SOP/Control Plan → 质量文档 Agent
- 真实质量放行状态 → 发运门禁

### 4.3 真实写入仍未完成

- ERPNext 草稿写入开关仍为关闭
- OpenMES 写操作仍未开放
- 审批目前主要改变本地数据库状态
- 尚未完成真实单据创建后的回读验证

## 5. 接手 AI 的工作规则

### 必须遵守

1. 开始前检查当前工作区：

```powershell
Set-Location E:\competition\汽车零部件工厂智能体开发
git status --short --branch
git log --oneline --decorate -10
```

2. 先修复和验证真实 ERP/MES 连接，再改 Agent 业务流。
3. 真实模式连接失败时必须报错，不能静默回退 Mock。
4. 所有 ERP/MES 字段、接口和状态必须以实际接口响应或用户确认作为依据。
5. 不确定字段、接口、公式或业务规则时，必须停下来询问用户。
6. 任何真实写入都必须经过人工审批、权限校验、幂等控制和回读确认。
7. 每个结果必须标明来源系统、原始记录编号和读取时间。
8. 每完成一个阶段都必须运行实际测试并报告结果。

### 禁止做法

- 不得把 Mock 数据改名后当成真实数据
- 不得把 `DEMO_*` 记录当成工厂真实记录
- 不得根据其他行业或网上样例猜 ERP/MES 字段
- 不得未经审批向 ERP/MES 写入正式业务单据
- 不得把创建草稿说成订单已经执行
- 不得把离线契约测试说成真实业务闭环成功
- 不得为了让页面成功而自动回退到 Mock
- 不得输出或提交 Token、密码、API Secret、Cookie
- 不得删除数据库、旧项目副本或现有代码来掩盖问题

## 6. 第一阶段：修复真实 OpenMES 连接

先不要改四个 Agent 的业务逻辑。

### 目标

让以下接口真实可用：

```text
POST /api/integrations/openmes/check
GET  /api/integrations/openmes/work-orders
GET  /api/mes/work-orders
GET  /api/mes/work-orders/{id}/progress
GET  /api/mes/quality-records
```

### 调查顺序

1. 直接检查 OpenMES 实际 API 的响应状态码、Content-Type 和原始响应结构。
2. 对照 `services/OpenMes` 当前版本的路由和认证方式。
3. 确认工单列表实际返回结构是数组、分页对象还是其他包装格式。
4. 修正 `backend/app/adapters/mes/openmes.py` 的解析逻辑。
5. 修正 `backend/app/adapters/mes/openmes_adapter.py` 的字段映射。
6. 用真实工单编号验证工单详情和进度。
7. 更新 `docs/field_mapping.md`，只写实际验证结果。

### 第一阶段验收标准

- OpenMES 健康检查返回 200
- 工单列表返回真实工单，不能是空的 Mock 结果
- 工单详情和进度可以根据真实编号查询
- 质量记录和工单之间的关联字段已经确认
- 文档中的工单数量与实际接口结果一致

## 7. 第二阶段：建立真实订单垂直闭环

先只实现一条真实正常订单流程，不要同时做所有异常场景。

### 业务流

1. 从真实 ERP 选择客户和物料。
2. 报价 Agent 读取真实价格、BOM、库存和交期相关数据。
3. 生成报价建议，并显示所有证据来源。
4. 人工审批报价。
5. 审批通过后，在 ERP 中创建未提交的报价或销售订单草稿。
6. 回读 ERP，确认草稿编号和 `docstatus=0`。
7. 读取真实 ERP 物料需求。
8. 根据真实订单关联真实 MES 工单。
9. 跟单 Agent 读取 MES 工单、进度和交期。
10. 质量文档 Agent 读取真实质量记录和生产文档。
11. 质量门禁未满足时阻止发运。
12. 只有真实质量状态和人工审批都满足时，才进入发运判断。

### 建议新增的真实业务接口

接口名称可以调整，但必须与 Mock 场景分离：

```text
POST /api/real-orders
GET  /api/real-orders/{id}
POST /api/real-orders/{id}/quotation/analyze
POST /api/real-orders/{id}/quotation/approve
POST /api/real-orders/{id}/erp-draft
GET  /api/real-orders/{id}/mes-status
GET  /api/real-orders/{id}/quality-package
GET  /api/real-orders/{id}/ship-gate
```

不要直接把真实业务逻辑塞进 `run_scenario()`。

## 8. 第三个阶段：接入其他异常业务流

正常订单真实闭环通过后，再实现：

### 缺料流程

- 读取真实 ERP 库存和物料需求
- 计算真实净需求
- 读取真实供应商和采购信息
- 生成采购方案
- 人工选择供应方案
- 创建 ERP 采购订单草稿
- 回读并重新计算 ETA

### 质量冻结流程

- 读取真实 MES 质量问题或 NCR
- 质量文档 Agent 收集证据
- 人工选择处置方案
- 只有真实 MES 质量状态变为放行后，才解除发运门禁

### 加急流程

- 读取真实 MES 生产进度和产能
- 计算可行的交期方案
- 显示成本和风险
- 人工选择方案
- 不得未经确认直接修改 MES 优先级或生产计划

## 9. 前端改造方向

当前前端以“场景与回放”为主，需要逐步改为真实业务入口：

- 客户选择
- 物料选择
- ERP 销售订单选择
- MES 工单选择
- Agent 工作台
- 真实审批箱
- ERP/MES 证据追溯
- 真实发运门禁

“场景回放”只能保留在开发测试入口，不能继续作为默认首页。

页面必须明确显示：

- 来源系统
- 原始记录编号
- 当前数据是否只读
- Agent 建议还是人工决定
- 是否会写入真实系统
- 写入后的回读结果

## 10. 验证命令

后端：

```powershell
Set-Location E:\competition\汽车零部件工厂智能体开发\backend
..\.conda-env\python.exe -m pytest tests -q
..\.conda-env\python.exe -m compileall -q app
```

前端：

```powershell
Set-Location E:\competition\汽车零部件工厂智能体开发\frontend
npm run build
```

每次真实联调至少验证：

```text
GET /api/health
GET /api/integrations/status
ERP 客户/物料/BOM/库存
OpenMES 工单/进度/质量记录
真实订单启动
审批前状态
审批后草稿
真实系统回读
质量门禁
```

如果构建再次出现 `tsconfig.tsbuildinfo` 的 `EPERM`，必须如实记录为阻塞，不能报告为构建成功。

## 11. 安全问题：必须优先处理

当前工作区中存在多个未跟踪的调试、种数和连接脚本。接手后必须检查这些脚本是否包含硬编码凭据，例如：

- `backend/check_openmes.py`
- `backend/check_erpnext.py`
- `backend/get_openmes_token.py`
- `backend/create_erp_api_key*.py`
- `backend/seed_*.py`

处理要求：

1. 不要在输出中打印凭据。
2. 将脚本改为读取环境变量，或删除不再需要的脚本。
3. 对已经写入脚本的凭据执行轮换。
4. 检查 Git 历史，确认凭据没有被提交。

## 12. 每次交接必须报告

完成工作后必须说明：

1. 修改了哪些文件
2. 哪些接口已经连接真实 ERP/MES
3. 哪些部分仍然是 Mock
4. 哪些字段已经实际确认
5. 哪些字段或业务规则仍待用户确认
6. 运行了哪些测试
7. 哪些测试通过
8. 哪些测试失败以及原因
9. 当前是否允许真实系统写入
10. 下一步需要用户提供什么信息

当前首要任务：

> 修复并验证 OpenMES 工单接口，然后从真实 ERP 订单开始实现第一条真实报价到生产跟踪的垂直业务链。
