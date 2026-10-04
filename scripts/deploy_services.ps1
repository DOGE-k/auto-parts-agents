# 一键部署外部真实系统：OpenMES + ERPNext（各自独立 compose project）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\deploy_services.ps1
# 幂等：已存在的配置文件不覆盖；重复运行安全。
#
# 说明：两个系统的 compose 里都有叫 "backend" 的服务，不能用一个 include
# 文件合并（服务名会静默冲突），所以按本地验证过的拓扑分别以独立 project
# 启动——与 docs/current_status_and_fix_plan.md 记录的真实环境一致。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$openmesDir = Join-Path $root "services\OpenMes"
$frappeDir = Join-Path $root "services\frappe_docker"

function Write-Step([string]$Msg) { Write-Host "[部署] $Msg" -ForegroundColor Green }
function Write-Hint([string]$Msg) { Write-Host "[待办] $Msg" -ForegroundColor Yellow }

# ── 前置检查 ──────────────────────────────────────────────────────────────
try { docker version --format "ok" | Out-Null } catch {
    Write-Host "[失败] Docker 不可用，请先安装并启动 Docker Desktop" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $openmesDir)) {
    Write-Host "[失败] 缺少 services/OpenMes。请先克隆上游仓库：" -ForegroundColor Red
    Write-Host "       git clone https://github.com/Mes-Open/OpenMes.git services/OpenMes" -ForegroundColor Yellow
    $missing = $true
}
if (-not (Test-Path $frappeDir)) {
    Write-Host "[失败] 缺少 services/frappe_docker。请先克隆上游仓库：" -ForegroundColor Red
    Write-Host "       git clone https://github.com/frappe/frappe_docker.git services/frappe_docker" -ForegroundColor Yellow
    $missing = $true
}
if ($missing) { exit 1 }

# ── OpenMES 配置（.env 不存在时从上游模板复制） ───────────────────────────
$freshInstall = $false
$openmesEnv = Join-Path $openmesDir ".env"
if (-not (Test-Path $openmesEnv)) {
    Copy-Item (Join-Path $openmesDir ".env.example") $openmesEnv
    $freshInstall = $true
    Write-Hint "已从 .env.example 生成 services/OpenMes/.env——请打开它设置 ADMIN_PASSWORD 等项（OpenMES 管理员密码，质量页登录/种子脚本都用它）。"
}

# ── ERPNext 配置（.env 不存在时从本仓库模板复制） ─────────────────────────
$frappeEnv = Join-Path $frappeDir ".env"
if (-not (Test-Path $frappeEnv)) {
    Copy-Item (Join-Path $PSScriptRoot "templates\erpnext.env.example") $frappeEnv
    $freshInstall = $true
    Write-Hint "已生成 services/frappe_docker/.env——请打开它设置 DB_PASSWORD（MariaDB root 密码，建站时要用）。"
}

# ── 启动 OpenMES（postgres / backend / caddy / reverb） ──────────────────
Write-Step "启动 OpenMES（project: openmes，端口 80/443）..."
docker compose --project-name openmes --env-file $openmesEnv -f (Join-Path $openmesDir "docker-compose.yml") up -d
if ($LASTEXITCODE -ne 0) { Write-Host "[失败] OpenMES 启动失败" -ForegroundColor Red; exit 1 }

# ── 启动 ERPNext（backend/frontend/db/redis/queue/scheduler/configurator） ─
Write-Step "启动 ERPNext（project: erpnext，端口 8080，首次会拉取镜像）..."
docker compose --project-name erpnext `
    -f (Join-Path $frappeDir "compose.yaml") `
    -f (Join-Path $frappeDir "overrides\compose.mariadb.yaml") `
    -f (Join-Path $frappeDir "overrides\compose.redis.yaml") `
    -f (Join-Path $frappeDir "overrides\compose.noproxy.yaml") `
    --env-file $frappeEnv up -d
if ($LASTEXITCODE -ne 0) { Write-Host "[失败] ERPNext 启动失败" -ForegroundColor Red; exit 1 }

# 首次部署待办只在真的首次（本次新生成了 .env）时提示；日常重启不刷屏。
if ($freshInstall) {
    Write-Host ""
    Write-Host "两个系统已启动。首次部署还需手工初始化（详见 README.md 第 3 节）：" -ForegroundColor Cyan
    Write-Hint "OpenMES  等容器健康后，管理员账号按 services/OpenMes/.env 的 ADMIN_USERNAME/ADMIN_PASSWORD 自动创建；登录页 http://127.0.0.1/"
    Write-Hint "ERPNext  建站（一次性，站点名必须叫 localhost）："
    Write-Host '        docker compose --project-name erpnext exec backend bench new-site localhost --mariadb-user-host-login-scope=% --db-root-password <你的DB_PASSWORD> --admin-password <你设的ERPNext管理员密码> --install-app erpnext' -ForegroundColor Yellow
    Write-Hint "之后回到 README.md 第 4 节：配置应用 .env（get_openmes_token.py 一键取 token）→ 种子数据 → start_app.ps1。"
}
