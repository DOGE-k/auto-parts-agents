# 一键启动演示环境：后端 9000 + 前端 5173 + 环境预检
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
# 幂等：端口已被占用时跳过启动；预检失败会明确列出，不静默继续。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

# Python 解释器探测：优先项目自带 .conda-env，其次 venv，最后系统 python。
# （.conda-env 不在 git 里；新机器用系统 Python 时先 `pip install -e backend`。）
$pythonCandidates = @(
    (Join-Path $root ".conda-env\python.exe"),
    (Join-Path $backend ".venv\Scripts\python.exe"),
    (Join-Path $root ".venv\Scripts\python.exe")
)
$python = $null
foreach ($candidate in $pythonCandidates) {
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) { $python = "python" }
Write-Host "[环境] Python: $python"

# 后端依赖预检：uvicorn 缺失时给出明确指引，而不是启动后报一堆 404。
& $python -c "import uvicorn, fastapi" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[失败] 当前 Python 缺少后端依赖（uvicorn/fastapi）。请先执行：" -ForegroundColor Red
    Write-Host "       $python -m pip install -e backend" -ForegroundColor Yellow
    exit 1
}

# 前端依赖预检：否则 npm 进程会立即退出，下面只能等满 30 秒才报端口超时。
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    Write-Host "[失败] 未找到 npm。请先安装 Node.js 20+，再重新运行本脚本。" -ForegroundColor Red
    exit 1
}
$viteCommand = Join-Path $frontend "node_modules\.bin\vite.cmd"
if (-not (Test-Path $viteCommand)) {
    Write-Host "[失败] 前端依赖未安装：$viteCommand" -ForegroundColor Red
    Write-Host "       请先执行：cd `"$frontend`"; npm install" -ForegroundColor Yellow
    exit 1
}
Write-Host "[环境] npm: $($npmCommand.Source)"

# 真实系统容器预检：ERPNext(8080)/OpenMES(80) 未响应时自动调 deploy_services.ps1 拉起。
# 这样本脚本就是唯一入口——电脑重启后也只需这一条命令。
$serviceProbes = @(
    @{ Name = "OpenMES";  Url = "http://127.0.0.1/api/health" },
    @{ Name = "ERPNext";  Url = "http://127.0.0.1:8080/api/method/ping" }
)
$servicesNeeded = $false
foreach ($probe in $serviceProbes) {
    try {
        Invoke-WebRequest -Uri $probe.Url -UseBasicParsing -TimeoutSec 3 | Out-Null
        Write-Host "[通过] $($probe.Name) 容器已在运行" -ForegroundColor DarkGray
    } catch {
        $servicesNeeded = $true
        Write-Host "[检测] $($probe.Name) 未响应，需要拉起容器" -ForegroundColor Yellow
    }
}
if ($servicesNeeded) {
    Write-Host "[启动] 调用 deploy_services.ps1 拉起真实系统容器 ..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "deploy_services.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[失败] 真实系统容器启动失败，见上方输出" -ForegroundColor Red
        exit 1
    }
    foreach ($probe in $serviceProbes) {
        $ready = $false
        for ($i = 0; $i -lt 150; $i++) {
            try {
                Invoke-WebRequest -Uri $probe.Url -UseBasicParsing -TimeoutSec 3 | Out-Null
                $ready = $true
                break
            } catch { Start-Sleep -Seconds 2 }
        }
        if ($ready) {
            Write-Host "[就绪] $($probe.Name)" -ForegroundColor Green
        } else {
            Write-Host "[警告] $($probe.Name) 约 300 秒内未就绪（首次拉取镜像可能较慢），继续启动应用；稍后预检会再次报告" -ForegroundColor Yellow
        }
    }
}

# 业务库容器（PostgreSQL）：后端 DATABASE_URL 指向它，存在但停止时先拉起，
# 否则应用启动校验连不上库直接退出（实测教训：一键关闭后业务库未被拉起）。
$dbExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^autoparts-db$"
if ($dbExists) {
    $dbRunning = docker inspect -f "{{.State.Running}}" autoparts-db
    if ($dbRunning -ne "true") {
        Write-Host "[启动] 业务库容器 autoparts-db（后端启动前必须就绪）..." -ForegroundColor Cyan
        docker start autoparts-db | Out-Null
        for ($i = 0; $i -lt 30; $i++) {
            docker exec autoparts-db pg_isready -U postgres 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { break }
            Start-Sleep -Seconds 1
        }
        Write-Host "[就绪] 业务库 autoparts-db" -ForegroundColor Green
    } else {
        Write-Host "[通过] 业务库 autoparts-db 已在运行" -ForegroundColor DarkGray
    }
}

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-Background([string]$Name, [int]$Port, [scriptblock]$Start) {
    if (Test-PortListening $Port) {
        Write-Host "[跳过] $Name 已在运行（端口 $Port）" -ForegroundColor DarkGray
        return
    }
    & $Start
    Write-Host "[启动] $Name → 端口 $Port" -ForegroundColor Green
}

Write-Host "=== 汽车零部件工厂智能体 · 演示环境启动 ===" -ForegroundColor Cyan

Start-Background "后端 API" 9000 {
    $out = Join-Path $backend "uvicorn-9000.log"
    $err = Join-Path $backend "uvicorn-9000.err.log"
    Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "9000", "--log-level", "warning" `
        -WorkingDirectory $backend -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err
}

Start-Background "前端页面" 5173 {
    $out = Join-Path $frontend "vite-5173.log"
    $err = Join-Path $frontend "vite-5173.err.log"
    Remove-Item $out, $err -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $npmCommand.Source `
        -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173" `
        -WorkingDirectory $frontend -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err
}

# 等待端口就绪（最多 30 秒）
foreach ($probe in @(@{ Name = "后端"; Port = 9000 }, @{ Name = "前端"; Port = 5173 })) {
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-PortListening $probe.Port) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host "[失败] $($probe.Name) 30 秒内未监听端口 $($probe.Port)，请查看日志" -ForegroundColor Red
        if ($probe.Name -eq "前端") {
            Write-Host "       前端标准输出：$frontend\vite-5173.log" -ForegroundColor Yellow
            Write-Host "       前端错误输出：$frontend\vite-5173.err.log" -ForegroundColor Yellow
            if (Test-Path (Join-Path $frontend "vite-5173.err.log")) {
                Get-Content (Join-Path $frontend "vite-5173.err.log") -Tail 30
            }
        }
        exit 1
    }
    Write-Host "[就绪] $($probe.Name) 端口 $($probe.Port)" -ForegroundColor Green
}

# 环境预检：全部走真实接口，失败如实报告
Write-Host "`n=== 环境预检（真实系统连通性） ===" -ForegroundColor Cyan
$checks = @(
    @{ Name = "后端健康";        Url = "http://127.0.0.1:9000/api/health" },
    @{ Name = "真实业务表面";    Url = "http://127.0.0.1:9000/api/runtime/surface" },
    @{ Name = "ERPNext 集成";    Url = "http://127.0.0.1:9000/api/integrations/status" },
    @{ Name = "审批身份解析";    Url = "http://127.0.0.1:9000/api/real-orders/identity/me" }
)
$failed = @()
foreach ($check in $checks) {
    try {
        $resp = Invoke-RestMethod -Uri $check.Url -Method Get -TimeoutSec 10
        Write-Host "[通过] $($check.Name)" -ForegroundColor Green
    } catch {
        Write-Host "[失败] $($check.Name): $($_.Exception.Message)" -ForegroundColor Red
        $failed += $check.Name
    }
}

# OpenMES / ERPNext 连通性（通过后端真实探测端点）
try {
    $openmes = Invoke-RestMethod -Uri "http://127.0.0.1:9000/api/integrations/openmes/check" -Method Post -TimeoutSec 15
    Write-Host "[通过] OpenMES 连通（health=$($openmes.status)）" -ForegroundColor Green
} catch {
    Write-Host "[失败] OpenMES 连通：$($_.Exception.Message)" -ForegroundColor Red
    $failed += "OpenMES"
}

$envFile = Join-Path $root ".env"
$envContent = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
if ($envContent -match "DEEPSEEK_API_KEY=\S") {
    Write-Host "[通过] DeepSeek API Key 已配置（智能协同问答可用）" -ForegroundColor Green
} else {
    Write-Host "[警告] DEEPSEEK_API_KEY 为空：智能协同问答会如实返回 503" -ForegroundColor Yellow
}
if ($envContent -match "REAL_WRITE_API_TOKEN=\S") {
    Write-Host "[通过] 真实写入门禁令牌已配置（NCR 处置/关闭可写回）" -ForegroundColor Green
} else {
    Write-Host "[警告] REAL_WRITE_API_TOKEN 未配置：写回接口会返回 503" -ForegroundColor Yellow
}
if ($envContent -match "APP_ADAPTER_MODE=real") {
    Write-Host "[通过] 适配器模式 real（连不上真实系统会明确报错，不回退 Mock）" -ForegroundColor Green
} else {
    Write-Host "[警告] APP_ADAPTER_MODE 不是 real：连不上 ERP/MES 时会静默回退 Mock，页面显示假数据。演示请设为 real" -ForegroundColor Yellow
}

Write-Host "`n=== 演示入口 ===" -ForegroundColor Cyan
Write-Host "  页面：  http://127.0.0.1:5173/  （真实业务页为默认）"
Write-Host "  剧本：  docs/demo_script.md（四条故事线 + 逐句台词）"
if ($failed.Count -gt 0) {
    Write-Host "`n预检未全部通过：$($failed -join '、')。请先解决再演示。" -ForegroundColor Red
    exit 1
}
Write-Host "`n环境就绪，可以开始演示。" -ForegroundColor Green
