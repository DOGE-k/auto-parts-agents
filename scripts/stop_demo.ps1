# 一键关闭演示环境：后端 9000 + 前端 5173 + OpenMES/ERPNext 容器 + 业务库
# 用法：powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
# 幂等：已停止的部分显示"跳过"；容器用 stop（不删除），下次 start_demo 秒级拉起。

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$openmesDir = Join-Path $root "services\OpenMes"
$frappeDir = Join-Path $root "services\frappe_docker"

function Stop-PortListener([string]$Name, [int]$Port) {
    $pids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $pids) {
        Write-Host "[跳过] $Name 未在运行（端口 $Port）" -ForegroundColor DarkGray
        return
    }
    foreach ($procId in $pids) {
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "[停止] $Name（进程 $($proc.ProcessName) PID $procId，端口 $Port）" -ForegroundColor Green
        } catch {
            Write-Host "[警告] $Name 进程 $procId 停止失败：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

Write-Host "=== 汽车零部件工厂智能体 · 一键关闭 ===" -ForegroundColor Cyan

# 1) 应用层：后端 / 前端
Stop-PortListener "后端 API" 9000
Stop-PortListener "前端页面" 5173

# 2) 容器层：OpenMES + ERPNext（stop 保留容器与数据，便于下次快速拉起）
if (Test-Path (Join-Path $openmesDir "docker-compose.yml")) {
    Write-Host "[停止] OpenMES 容器组 ..." -ForegroundColor Green
    docker compose --project-name openmes --env-file (Join-Path $openmesDir ".env") `
        -f (Join-Path $openmesDir "docker-compose.yml") stop
} else {
    Write-Host "[跳过] services/OpenMes 不存在，跳过容器组" -ForegroundColor DarkGray
}

if (Test-Path (Join-Path $frappeDir "compose.yaml")) {
    Write-Host "[停止] ERPNext 容器组 ..." -ForegroundColor Green
    docker compose --project-name erpnext `
        -f (Join-Path $frappeDir "compose.yaml") `
        -f (Join-Path $frappeDir "overrides\compose.mariadb.yaml") `
        -f (Join-Path $frappeDir "overrides\compose.redis.yaml") `
        -f (Join-Path $frappeDir "overrides\compose.noproxy.yaml") `
        --env-file (Join-Path $frappeDir ".env") stop
} else {
    Write-Host "[跳过] services/frappe_docker 不存在，跳过容器组" -ForegroundColor DarkGray
}

# 3) 业务库（存在才停）
$businessDb = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^autoparts-db$"
if ($businessDb) {
    Write-Host "[停止] 业务库容器 autoparts-db ..." -ForegroundColor Green
    docker stop autoparts-db | Out-Null
    Write-Host "[停止] 业务库 autoparts-db ✓" -ForegroundColor Green
} else {
    Write-Host "[跳过] 业务库容器 autoparts-db 不存在（SQLite 模式）" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "已全部关闭。下次双击 启动演示.bat 一键恢复（容器为 stop 保留，秒级拉起）。" -ForegroundColor Cyan
