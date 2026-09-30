# 一键启动演示环境：后端 9000 + 前端 5173 + 环境预检
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
# 幂等：端口已被占用时跳过启动；预检失败会明确列出，不静默继续。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $root ".conda-env\python.exe"

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
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "npm run dev -- --host 127.0.0.1 --port 5173" `
        -WorkingDirectory $frontend -WindowStyle Hidden
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

Write-Host "`n=== 演示入口 ===" -ForegroundColor Cyan
Write-Host "  页面：  http://127.0.0.1:5173/  （真实业务页为默认）"
Write-Host "  剧本：  docs/demo_script.md（四条故事线 + 逐句台词）"
if ($failed.Count -gt 0) {
    Write-Host "`n预检未全部通过：$($failed -join '、')。请先解决再演示。" -ForegroundColor Red
    exit 1
}
Write-Host "`n环境就绪，可以开始演示。" -ForegroundColor Green
