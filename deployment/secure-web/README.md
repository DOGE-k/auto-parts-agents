# 演示部署恢复

用户已明确本项目用于比赛演示：保留网页登录和聊天历史隔离，撤回本轮额外的 HTTPS、Unix socket、管理端口回环和配额限制。此前的模板仅作为变更历史保留，不再是默认部署方案；`scripts/deploy_secure_web.sh` 已停用，不会再改变服务。

恢复后的目标：

| 服务 | 入口及行为 |
|---|---|
| 项目网页 | 原服务器 `IP:5173`，Vite 开发服务 |
| 网页后端 | `127.0.0.1:9000`，通过前端同源 `/api` 代理访问 |
| 网页身份 | 保留项目登录；保留个人聊天归属；原审批与写入角色门禁不变 |
| ERPNext | 原来的公开 `8080` 映射 |
| OpenMES | 原 Caddyfile、原 `80/443` 映射；后端地址恢复 `http://localhost` |
| 梧桐 AIP | 既有 `9443–9447`、AIC、证书、mTLS 和 AMP 修复均保持 |

## 受控恢复步骤

只在 Linux 项目服务器执行。先由执行者保存当前状态，确认已部署保留登录/历史的新代码；以下工具不修改任何账号密码或私钥，不恢复旧数据库。

```bash
python scripts/restore_demo_deployment.py prepare \
  --backup /absolute/private/original-backup \
  --state /absolute/private/new-restore-state

# 审阅 prepare 结果后，再执行相同路径：
python scripts/restore_demo_deployment.py apply \
  --backup /absolute/private/original-backup \
  --state /absolute/private/new-restore-state

python scripts/restore_demo_deployment.py verify \
  --backup /absolute/private/original-backup \
  --state /absolute/private/new-restore-state
```

`prepare` 只核对原始备份并把当前配置复制到新的私有目录。`apply` 仅恢复选定监听参数、原 Caddyfile 与启停脚本，停用本轮 systemd 服务并归档其状态目录，重建 Caddy 和 ERP 前端容器，启动原演示服务，并因 MES 地址恢复而重启五个 AIP 进程。

保留的登录在 HTTP 入口下需要 `PROJECT_AUTH_COOKIE_SECURE=false`；`PROJECT_AUTH_REQUIRE_LOGIN=true` 保持。前端 API 继续使用 `/api`，不能恢复成浏览器电脑自己的 `127.0.0.1:9000`。

## 恢复边界与核验

- 不覆盖整份 `.env`，只替换 `OPENMES_BASE_URL`、`PROJECT_AUTH_REQUIRE_LOGIN`、`PROJECT_AUTH_COOKIE_SECURE` 和 ERP 的 `HTTP_PUBLISH_PORT`；原凭据、用户、密码和业务数据保持。
- 不删除 Caddy 数据卷中的已签发证书，不修改运行中的 AIC、RPC 地址或证书配置；本轮 SSH 限制已单独撤回，这个脚本不管理 SSH。
- 不降级数据库或删除 `owner_subject` 列，不清空聊天；旧无归属历史不擅自认领。
- 恢复脚本会检查原/现配置关系、监听归属前提、HTTP 健康、匿名历史仍为 401、数据库行数未减少和五个 mTLS 健康入口。执行后还需验证实际登录、跨账号历史与真实平台调用。
- 此恢复是用户明确要求的比赛演示设置，不把这些端口或部署方式称为赛事硬性要求。域名备案问题也不因恢复 IP 测试方式而被此脚本认定合规。
- 若执行失败，诊断保存在私有恢复目录，不能为了恢复页面而回滚数据库或覆盖凭据。先检查失败步骤再继续，不提供未经核验的自动反向切换。
