# 汽车零部件四智能体动态协作平台

本项目的目标是报价、采购、跟单、质量文档四个独立智能体，通过事件和受控能力协作。梧桐平台注册安排在本地智能体功能和业务接口验证之后。

## 当前开发状态

当前代码包含两类内容，使用时要区分：

- `scenarios/` 和 `/api/scenarios/.../run` 是早期开发用的合成场景回放，仍通过 MockERP/MockMES 执行。它们是回归测试工具，不是工厂真实业务功能。
- `backend/app/adapters/` 与 `backend/app/integrations/` 新增真实 API 连接层：DeepSeek Chat Completions、ERPNext/Frappe REST、OpenMES 读取接口。OpenMES 工单读取已经可通过本平台 API 调用。
- ERPNext 只提供通用 REST 传输能力；具体 DocType/字段映射需在实际实例中检查后再配置。
- ERPNext 草稿写入默认关闭；即使打开配置，没有可信的人工审批校验器仍会拒绝写入。OpenMES 写操作也保持关闭。
- 四个 Agent 的真实业务流程尚未接到真实 ERPNext/OpenMES 数据上。因此当前版本不是已完成的生产系统，也不能用来处理真实订单。

## 本机运行

在项目根目录使用 Anaconda 创建 Python 环境：

```powershell
conda create --prefix .\.conda-env python=3.12 -y
conda activate .\.conda-env
python -m pip install -e .\backend
Copy-Item .env.example .env
```

编辑根目录 `.env`，只在本机填写 API 地址与密钥；不要把密钥发到聊天或提交到 GitHub。然后在项目根目录打开一个 Anaconda Prompt：

```powershell
conda activate .\.conda-env
Set-Location .\backend
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

另开一个 Anaconda Prompt 启动前端：

```powershell
conda activate .\.conda-env
Set-Location .\frontend
npm install
npm run dev
```

打开前端 `http://127.0.0.1:5173`。后端 API 文档在 `http://127.0.0.1:8000/docs`。

## 真实连接配置与检查

根目录 `.env.example` 提供配置名，真实凭据留空。配置后可在 Swagger 页面调用：

- `GET /api/integrations/status`：查看配置是否完整，只返回状态，不返回密钥。
- `POST /api/integrations/deepseek/check`：实际请求一次模型，可能产生少量 API 费用。
- `POST /api/integrations/erpnext/check`：使用专门的 ERPNext API 用户验证 Frappe token。
- `POST /api/integrations/openmes/check`：检查 OpenMES 服务；有用户 Token 时也验证当前用户。
- `GET /api/integrations/openmes/work-orders`：使用 OpenMES 只读用户 Token 查询工单。
- `GET /api/integrations/openmes/work-orders/{work_order_id}`：查询工单详情。

OpenMES ERP 集成 API 的读取需要先在 OpenMES 启用 ERP Integration 模块，再使用单独创建且只授予读取 scope 的 API Key。它和普通用户 Bearer Token 不是同一类凭据。ERPNext REST 请求以 API 用户角色为权限边界。

`ERPNEXT_BASE_URL` 与 `OPENMES_BASE_URL` 填系统站点根地址，不要把 API 路径或密钥拼到 URL 中。

## 验证

后端连接器的离线契约测试：

```powershell
Set-Location .\backend
$env:PYTHONPATH = "."
..\.conda-env\python.exe -m unittest discover -s tests -v
```

这些测试验证请求协议、认证头、字段限定、秘密不回显和写入闸门，不会访问 DeepSeek 或你的 ERPNext/OpenMES 实例。

## 完成真实四智能体所需的下一步资料

需要先有可访问的 ERPNext 与 OpenMES 测试实例。之后按实测结果完成：

1. ERPNext 版本、站点地址、只读 API 用户，以及目标 DocType/字段和业务状态的实际映射。
2. OpenMES 版本、站点地址、只读用户 Token；如需读取 ERP 集成数据，再提供启用模块后的只读 scope API Key。
3. 本机 `.env` 中的 DeepSeek API Key。请勿在聊天里粘贴密钥。
4. 在确认操作人员身份认证和人工审批来源前，不开启任何真实系统写入。

梧桐 AIC/ACS 注册、四个独立 ACP Partner 服务和跨 Agent 真实动态协作仍在后续实现范围内；当前没有注册或联调成功的声明。
