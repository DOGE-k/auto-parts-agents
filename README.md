# 汽车零部件工厂智能体（真实 ERP/MES 版）

基于**真实 ERPNext + OpenMES** 的四智能体协作平台：报价、采购、跟单、质量文档四个 Agent 在真实业务数据上协同，从报价到发运门禁的完整闭环。所有业务数据来自真实系统并标注来源与记录编号；数据缺失/连接失败**如实报错**，不回退 Mock、不伪造数据；所有写入真实系统的操作必须经过 **人工审批 → 写回 → 回读验证 → 幂等** 门禁。

```
浏览器 (5173, React+Vite)
   │
后端 API (9000, FastAPI) ←— DeepSeek（协同问答）
   │
   ├── ERPNext   http://127.0.0.1:8080  （客户/物料/BOM/价格/库存/销售与采购草稿）
   ├── OpenMES   http://127.0.0.1/      （工单/批次报工/质量 NCR/检验/发运）
   └── 业务库    PostgreSQL 15432（可选，默认 SQLite）
```

> 当前开发状态、真实接口验证记录与遗留问题见 `docs/current_status_and_fix_plan.md`；演示操作剧本见 `docs/demo_script.md`。

> 如果是在另一台 Windows 电脑上复现真实 ERPNext + OpenMES 环境，请先阅读
> [`docs/真实环境复现部署.md`](docs/真实环境复现部署.md)，再运行
> `scripts/setup_real_environment.ps1`。该流程会为每台电脑重新创建本地服务和凭据，
> 不会复制本机 `.env`、Token、API Secret 或数据库；在干净电脑完整重放前，不把它称为已验证的一键部署。

---

## 1. 前置要求

- Docker（含 compose v2）——运行 ERPNext 与 OpenMES
- Python ≥ 3.12、Node ≥ 20——运行本应用（后端/前端/测试）

## 2. 获取代码

本仓库不含 ERPNext/OpenMES 源码（它们是独立上游仓库，放在 `services/` 下，已被 .gitignore 忽略）：

```bash
git clone <本仓库地址> && cd <仓库目录>
git clone https://github.com/Mes-Open/OpenMes.git        services/OpenMes
git clone https://github.com/frappe/frappe_docker.git    services/frappe_docker
```

## 3. 部署 ERPNext 与 OpenMES

### 3.1 一键启动两个系统

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy_services.ps1     # Windows
bash scripts/deploy_services.sh                                          # Linux/macOS
```

脚本会：检查 Docker 与两个上游仓库 → 缺 `.env` 时从模板生成（OpenMES 用它自己的 `.env.example`，ERPNext 用 `scripts/templates/erpnext.env.example`）→ 分别以独立 compose project 启动 `openmes`（postgres/backend/caddy/reverb，端口 80/443）和 `erpnext`（backend/frontend/db/redis/queue/scheduler，端口 8080）。

> ⚠️ 两个 compose 文件里都有叫 `backend` 的服务，**不能**用一个 include 合并文件编排（服务名会静默冲突），必须像本地验证过的这样分两个 project 启动。
> ⚠️ 如果端口 80 / 8080 已被占用，先停掉占用进程或在各自 `.env` 改端口（改了 ERPNext 端口的话，第 4 节的 `ERPNEXT_BASE_URL` 也要同步）。

### 3.2 OpenMES 初始化

- 管理员账号在容器首次启动时按 `services/OpenMes/.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 自动创建（`APP_KEY` 缺省也会自动生成）。
- 就绪检查：`curl -s http://127.0.0.1/api/health` 应返回 `{"status":"ok"}`。
- 登录页 <http://127.0.0.1/>（这个 admin 密码之后也用于页面"审批身份"登录和种子脚本）。

### 3.3 ERPNext 建站（一次性）

```bash
docker compose --project-name erpnext exec backend \
  bench new-site localhost \
  --mariadb-user-host-login-search=% \
  --db-root-password <你在 services/frappe_docker/.env 里设的 DB_PASSWORD> \
  --admin-password <你设的 ERPNext 管理员密码> \
  --install-app erpnext
```

- 站点名**必须叫 `localhost`**（`FRAPPE_SITE_NAME_HEADER=localhost` 按它解析；后端默认连 `http://127.0.0.1:8080`）。
- 就绪检查：`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/api/method/ping` 应为 200。
- 参考文档：<https://github.com/frappe/frappe_docker/blob/main/docs>

## 4. 配置应用

```bash
cp .env.example .env      # 然后按下面逐项填写
```

| 配置 | 怎么拿 |
|---|---|
| `ERPNEXT_BASE_URL` / `ERPNEXT_API_KEY` / `ERPNEXT_API_SECRET` | ERPNext 以 Administrator 登录 <http://127.0.0.1:8080> → 用户列表 → 对应用户 → 设置 → **API 访问 → 生成密钥**（Key+Secret 只显示一次） |
| `OPENMES_TOKEN` / `OPENMES_ERP_API_KEY` | 一键脚本：`python backend/get_openmes_token.py`——它读 `services/OpenMes/.env` 的管理员凭据登录、验证 token、缺失时自动创建 ERP 集成 key，并写回根目录 `.env` |
| `REAL_WRITE_API_TOKEN` | **自己设一个随机值**（如 `python -c "import secrets;print(secrets.token_hex(24))"`）。所有写真实系统的接口要求请求头 `X-Real-Write-Token` 与之一致；页面"会话设置 → 本地写入令牌"填同一个值 |
| `DEEPSEEK_API_KEY` | DeepSeek 开放平台申请；不配则协同问答如实返回 503（其余功能不受影响） |
| `APP_ADAPTER_MODE` | 保持 `real`（演示/验收）。`auto` 会在连不上真实系统时回退内置 Mock——仅本地调试用，页面数据不是真的 |
| `DATABASE_URL` | 默认 SQLite 零依赖即可跑；要 PostgreSQL：`docker run -d --name autoparts-db -p 15432:5432 postgres:17-alpine`，`DATABASE_URL` 指向它 |
| 前端 API 地址 | 复制根目录 `.env` 里的 `VITE_API_BASE_URL`（默认 `http://127.0.0.1:9000/api`）到 `frontend/.env.local` |

## 5. 初始化业务数据（种子脚本）

```bash
cd backend
python seed_erpnext.py          # ERPNext 主数据：公司/客户/物料/BOM/价格/仓库/库存
python seed_supplier_data.py    # 供应商关系/交期/MOQ/供应商特定价格
python seed_openmes.py          # OpenMES 工厂基础数据（产线/产品类型/工艺模板等）
python seed_openmes_v2.py       # 工单 + 质量问题（走 OpenMES 用户 API）
python seed_openmes_v3.py       # 接收工单/开工生产（延续 v2）
python seed_inspection_eta.py   # 可选：TEST_ 检验记录与批次耗时补录（演示数据，幂等）
```

- 脚本凭据全部从根目录 `.env` / `services/OpenMes/.env` 读取，不会打印密钥。
- 除 `seed_inspection_eta.py`（显式幂等，可重跑）外，其余为一次性初始化，**重复执行可能产生重复记录**。
- 注意：OpenMES 里 BD-2401 配有 BOM 工艺模板（`TEST_BD2401_QUALITY_FLOW`），新下达工单才有批次步骤可报工；其他产品如需报工，先在 OpenMES 给对应产品类型建工艺模板（主数据工程，系统会如实提示而不是伪造数据）。

在另一台真实环境上导入可复现的 `TEST_` 演示工单和质量问题，使用新增的动态查重脚本（不会使用当前库的固定数字 ID）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_real_environment.ps1 -RunSeeds -RunRealDemoSeed
```

检验/ETA 的旧补录脚本依赖当前库的固定批次和数据库表结构，默认不会执行；详见 [`docs/真实环境复现部署.md`](docs/真实环境复现部署.md)。

## 6. 启动应用

### 图形界面方式（推荐）

**双击仓库根目录的 `启动演示.bat`**：与下面的脚本完全等价——自动拉起容器（含业务库 autoparts-db）→ 起后端/前端 → 逐项预检 → 预检通过后自动打开浏览器；窗口底部会显示预检结果，看完可关闭（服务在后台继续运行）。可右键 →"发送到桌面快捷方式"当日常入口。

配套 **`关闭演示.bat`** 一键全部停止（后端/前端进程 + OpenMES/ERPNext 容器 + 业务库；容器只 stop 不删除，下次启动秒级拉起）。

### 命令行方式

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
```

**这一条命令就是全部启动入口**：Python 解释器自动探测（`.conda-env` → venv → 系统 python，缺依赖会提示 `pip install -e backend`）→ 检测到 ERPNext/OpenMES 容器未响应时**自动调用 deploy_services 脚本拉起**（含就绪等待）→ 后端 9000 + 前端 5173 启动 → 对真实系统逐项预检（连通性、身份解析、写入令牌、DeepSeek、适配器模式），**预检不通过会明确列出，不会静默继续**。幂等：重复运行安全，已启动的部分自动跳过。

手动启动（等价，一般不需要）：

```bash
cd backend  && APP_ADAPTER_MODE=real python -m uvicorn app.main:app --host 127.0.0.1 --port 9000
cd frontend && npm install && npm run dev -- --port 5173 --strictPort
```

## 7. 验证清单

1. `curl http://127.0.0.1:9000/api/health` → `{"status":"ok",...}`
2. `curl http://127.0.0.1:9000/api/integrations/status` → erpnext/openmes 均 `configured:true`、`mode:"real"`
3. 打开 <http://127.0.0.1:5173/> → 顶部显示"ERPNext + OpenMES 真实连接"
4. "会话设置"用 OpenMES admin 登录 + 填写入令牌 → 走一遍报价 → 审批 → ERP 草稿（回读 docstatus=0）
5. 完整演示路径见 `docs/demo_script.md`（问答 / 速率 ETA / 缺料方案 / NCR 处置 / 现场报工 / 现场登记质量问题）

## 8. 测试与 CI

```bash
cd backend
python -m pip install -e .
python -m pytest tests -q                                      # 当前基线 214 passed（2026-10-02 复核）
python -m compileall -q app
cd ../frontend
npm ci
npm run test -- --run
npx tsc --noEmit --incremental false --project tsconfig.json
npm run build
```

2026-10-02 复核：后端 `tests` 为 214 passed，前端 Vitest 为 26 passed，源代码类型检查通过；`npm run build` 可能因 Windows 文件锁无法写入 `frontend/tsconfig.tsbuildinfo` 而返回 EPERM。遇到该错误时先释放项目相关进程或文件锁，再重跑，不要把构建失败写成成功。

推送后 GitHub Actions（`.github/workflows/ci.yml`）自动跑同样的检查——测试自带假适配器，**不需要**真实 ERP/MES 即可运行。

## 9. 常见问题排障

| 现象 | 原因与处理 |
|---|---|
| 首次拉取 ERPNext 镜像很久 | `frappe/erpnext:v16.36.0` 首次下载和解压可能需要较长时间；只要进度或日志仍在变化就等待，不要反复按 `Ctrl+C`。 |
| 拉取结束后出现 `unexpected EOF` | 下载连接中断。运行 `docker pull frappe/erpnext:v16.36.0` 重试，完成后重新运行 `scripts\setup_real_environment.ps1`。 |
| Docker Desktop 提示 `containerd`、`SIGBUS`、`bus error` 或 `core dumped` | Docker 引擎在处理镜像层时崩溃。退出并重新打开 Docker Desktop，确认 `docker version` 同时有 `Client` 和 `Server` 后再重试；不要直接点击“恢复出厂设置”，以免删除本机容器、镜像和卷。 |
| 脚本提示缺少 `ADMIN_PASSWORD` | 编辑 `services\OpenMes\.env`，填写 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`；保存后回到等待中的脚本窗口按 Enter。若脚本已回到 `PS ...>`，重新运行入口命令。 |
| 脚本提示缺少 `DB_PASSWORD` | 编辑 `services\frappe_docker\.env`，填写 `DB_PASSWORD`；保存后回到等待中的脚本窗口按 Enter。若脚本已回到 `PS ...>`，重新运行入口命令。 |
| 页面报"连不上 ERP/MES"或接口 502 | 确认容器健康：`docker ps` 看 `openmes-*` / `erpnext-*`；`APP_ADAPTER_MODE=real` 下连接失败**必然明确报错**（设计如此，不回退假数据） |
| OpenMES 接口 401 | `OPENMES_TOKEN` 失效——重跑 `python backend/get_openmes_token.py` 换新 token 后重启后端 |
| ERPNext 接口 500、数据库日志出现 `Access denied for user '_xxxx'@'IP'` | 旧版本建站把站点库用户授权绑死在容器 IP 上，容器重启换 IP 后认证失败。修复（保留密码）：进 `erpnext-db-1` 执行 `RENAME USER '_xxxx'@'<旧IP>' TO '_xxxx'@'%'; FLUSH PRIVILEGES;`（按 README 3.3 节带 `--mariadb-user-host-login-search=%` 新建的站点不会有此问题） |
| 写接口 403 | 请求头 `X-Real-Write-Token` 与 `.env` 的 `REAL_WRITE_API_TOKEN` 不一致；页面"会话设置"里重新保存令牌 |
| 登录会话 15 分钟断 | OpenMES Sanctum 会话 TTL（安全设计），重新登录即可 |
| 报价/采购提示"EVALUATION_BLOCKED / 数据缺失" | 真实主数据未配置（如无 BOM、无价格表）——系统**故意**不编造，按提示补录 ERP 数据 |
| 新工单无法报工 | 该产品类型在 OpenMES 无 BOM 工艺模板 → 无批次步骤；补模板后对新订单重新"下达工单" |
| 后端改了代码不生效 | uvicorn 无 --reload，需重启进程 |
| 前端连的后端不是 9000 | 检查 `frontend/.env.local` 的 `VITE_API_BASE_URL`（`npm run dev` 会热加载 .env） |
| `npm run build` 偶发 EPERM | Windows 文件锁，重试即可 |

## 10. 诚实声明（部署文档的边界）

- 本指南的容器编排、配置模板与初始化命令与**本地已验证环境逐项一致**（compose 结构、端口、站点名、种子脚本均来自真实运行记录），但"全新机器从零到演示"的端到端流程**尚未在另一台干净机器上完整重放过**；如遇问题按第 9 节排障，或对照 `docs/current_status_and_fix_plan.md` 的环境记录。
- ERPNext/OpenMES 上游版本更新可能改变其部署细节，以各自上游文档为准。
- 项目已知边界（不伪造能力）：订单级质量放行、SN 级追溯（OpenMES 无对应 API，页面如实标注 NOT_SUPPORTED/数据缺失）。
