# 汽车零部件四智能体动态协作平台

当前实现按《汽车零部件四智能体动态协作平台开发实施文档》推进。首阶段目标是本地 Mock MVP，先实现报价、采购、跟单、质量文档四个独立 Agent 的协作基础。

## 当前状态

- 已建立领域对象、双层状态枚举与操作状态迁移守卫。
- 本地演示数据库使用 SQLite；这是经确认的演示版调整，正式部署仍按实施文档使用 PostgreSQL。
- 正常订单与缺料场景使用单独标注的合成数据和固定演示计算规则。
- 梧桐注册、ERPNext、OpenMES 和真实 LLM 不在本地 Mock 首阶段接入范围内。

## 当前功能

- 六个前端页面：场景选择、协同驾驶舱、Agent 工作台、方案对比、人工审批箱、审计追溯。
- 两条场景：正常订单从报价审批走到签收归档；缺料链展示动态 Leader/Partner、供应方案人工选择、PO 草稿审批和 ETA 更新。
- 后端包含双层状态、Agent 能力白名单、ToolRegistry、Inbox/Outbox、事件审计、版本绑定审批和请求幂等键。
- SQLite 文件仅保存本地演示数据，路径默认为 `backend/data/demo.db`；项目内 `.conda-env` 不会提交到 Git。

## 运行环境

推荐 Python 3.12。创建虚拟环境并安装后端依赖：

```powershell
conda activate .\.conda-env
cd backend
python -m pip install -e .
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

另开 PowerShell 窗口启动前端：

```powershell
cd frontend
npm install
npm run dev
```

前端地址为 `http://127.0.0.1:5173`，API 地址为 `http://127.0.0.1:8000`。`.env.example` 可作为本机配置参考，真实系统凭据保持空白。

当前电脑通过 Anaconda 在项目内创建 Python 3.12 环境。项目没有 Docker 依赖。梧桐 AIP、ERPNext 与 OpenMES 真实连接仍未实现。

新环境初始化命令（在项目根目录运行）：

```powershell
conda create --prefix .\.conda-env python=3.12 -y
conda activate .\.conda-env
```

## 明确待补信息

演示规则在 `backend/app/tools/demo_rules.py` 中注明版本和计算口径，演示数据在 `scenarios/` 下标记为合成数据。OpenMES 真实接入还需要实施文档第 9.2 节列出的项目、版本和接口资料。
