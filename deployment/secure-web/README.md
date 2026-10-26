# 正式网页安全部署

此目录是现有 Linux 演示服务器的受控迁移方案。复用 OpenMES 的 Caddy 容器提供正式域名 HTTPS，不另起代理抢占 80/443。执行前先审阅生成配置和实际服务依赖。

## 最终拓扑

| 服务 | 最终入口 | 访问范围 |
|---|---|---|
| 项目网页 | 正式域名 HTTPS 443，80仅重定向 | 公网；业务内容和API需项目登录 |
| 浏览器后端 | `/run/auto-parts-api/backend.sock` | 私有 Unix socket，无9000 TCP监听 |
| ERPNext | `127.0.0.1:8080` | 服务器内部或SSH隧道 |
| OpenMES | `127.0.0.1:18081` → Caddy容器8081 | 服务器内部或SSH隧道 |
| 梧桐AIP | 现有9443–9447 | 保留既有mTLS和身份授权 |
| Vite开发服务 | 停止5173监听 | 正式页面使用构建产物 |

后端由 systemd `auto-parts-api.service` 管理，单worker、无reload、故障重启并开机启动。Caddy保留原有数据、配置和MES数据卷，并使用unless-stopped重启策略。

## 执行前条件

- SSH维护通道可用；确认域名A/AAAA实际指向此机，80/443能够完成证书验证。
- 先完成数据库、ERP、MES和实际环境配置的私有备份；备份不得提交仓库或混入参赛材料。
- 新代码和前端依赖已同步，Linux项目环境为项目根目录`.venv`，项目账号已准备好。
- Docker Compose须至少2.24.4；此版本要求用于`ports: !override`，防止旧公网映射被合并保留。
- 先单独收紧ERPNext的发布地址为`127.0.0.1:8080`。此脚本不重建ERP，只验收结果。
- 本脚本不修改云安全组。22端口来源范围、IPv4/IPv6规则和全部绑定安全组仍需在控制台核对。

## 生成和审阅

在项目根目录执行，域名和备份目录使用实际核对值：

```bash
APP_DOMAIN=your-domain.example bash scripts/deploy_secure_web.sh prepare
```

默认状态目录为`~/.local/state/auto-parts-secure-web`，权限700。生成Caddyfile、OpenMES安全override、systemd单元和非秘密backend.env。prepare只生成和解析配置，不迁移、不重启服务。

预检对合并后的端口和挂载做结构断言，但不打印Compose展开后的凭据。它也核查当前Caddy运行UID；当前官方容器为root，可访问750目录。如果以后改为非root容器，必须先设计明确的组映射，不能放宽socket目录到全用户可访问。

## 迁移和启动

OpenMES迁到18081后，**五个AIP进程也必须使用新地址**。浏览器服务的backend.env覆盖只影响该systemd服务，不能替代根目录`.env`或AIP进程环境中的OPENMES_BASE_URL更新。先备份真实配置，只修改这一连接URL并按现有方式重启AIP进程，保持AIC、RPC端口、证书、私钥及注册身份原样。迁移窗口要安排这一步与Caddy切换紧邻，避免AIP短暂连到旧80入口。

```bash
APP_DOMAIN=your-domain.example SECURE_WEB_BACKUP_DIR=/absolute/private/verified-backup bash scripts/deploy_secure_web.sh apply
```

apply依次构建同源前端、执行Alembic迁移、安装并重启单worker UDS服务、核对私有健康、停止经进程工作目录和命令核实的旧5173/9000监听、仅重建Caddy、检查HTTPS及匿名访问门禁。不会覆盖服务器未跟踪的`scripts/start_app.sh`，不会自动修改AIP或ERP配置。

前端OpenMES维护入口设为`http://127.0.0.1:18081`，需要维护人员先建立SSH隧道；正常项目用户不应把它当公网入口。OpenMES自身的页面登录、WebSocket及必要的APP_URL设置还需维护通道实测。

## 私有socket与代理头

socket目录mode750，服务用户和组为部署账号，Caddy容器root持有只读挂载。目录保留策略避免systemd重启时删除目录造成Docker挂载失效。容器实际能访问socket，并由HTTPS API返回401证明已连接到后端。

Caddy明确覆盖客户端提交的X-Forwarded-For/Proto/Host。Uvicorn的`--forwarded-allow-ips=*`只用于该私有UDS：可连接者限Caddy和受信本机用户。**不得将这个设置复制到公网TCP监听。** 若代理前再加CDN/负载均衡，须重新核实真实来源IP链，不能直接信任任意转发头。

## 验证

```bash
APP_DOMAIN=your-domain.example bash scripts/deploy_secure_web.sh verify
curl --unix-socket /run/auto-parts-api/backend.sock http://localhost/api/health
```

脚本验证ERP/MES本机健康、HTTPS页面200、匿名会话接口401、管理服务只绑定回环、5173/9000无监听，以及systemd已启用并运行。不会进行真实账号登录、模型调用或ERP/MES写入。

另须从独立外网验证8080/18081/5173/9000无法直连；用合法账号验证登录、Secure Cookie、历史隔离和SSE；验证9443–9447无有效证书请求被拒绝、合法梧桐协作仍成功。重启持久化应检查systemd/Docker重启策略，整机重启只有用户同意后才做。

正式网页从旧5173迁移后，需核对平台公开ACS/webAppUrl是否仍指旧入口，按平台流程更新网页元数据；不能因此随意更换AIC或证书。材料中的截图脱敏与实际运行配置分别处理。

## 安全回退

```bash
APP_DOMAIN=your-domain.example bash scripts/deploy_secure_web.sh safe-stop
```

此回退让正式HTTPS显示503维护页并停止浏览器后端；ERP和MES保持回环访问，AIP保持既有进程与接入配置。不会恢复旧公网MES路由、旧5173/9000监听或ERP全公网映射。不得直接把旧Caddyfile、旧`.env`和旧启动脚本整体恢复上线。

需要恢复代码或数据库时，先停相关写入、评估备份时间与新产生数据，再人工选择版本；不自动降级数据库，不自动覆盖线上数据。修复后重新prepare、审阅、apply、验收。

`scripts/deploy_services.sh`会在状态目录存在安全override时自动携带；检测到正式部署状态但override缺失时拒绝启动，避免重新公开MES。以其他账号或sudo运行时须显式设置`SECURE_WEB_STATE`指向原部署状态目录。ERPNext新部署模板默认回环发布。

后续直接维护OpenMES Caddy时必须携带安全override：

```bash
docker compose -p openmes   -f services/OpenMes/docker-compose.yml   -f "$HOME/.local/state/auto-parts-secure-web/openmes.override.yml"   up -d --no-deps caddy
```

只运行上游base compose仍可能恢复不安全映射，禁止用其替代安全入口。服务器旧start_app.sh应另加正式服务保护：已安装正式服务时不再启动Vite开发服务；原始版本保留在私有备份。
