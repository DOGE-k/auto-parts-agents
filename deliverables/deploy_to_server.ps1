# 通过 SSH 密钥把服务器更新到本机当前提交（由 DSH 代理执行，也可人工运行）
# 前置：本机公钥已加入服务器 ubuntu 的 ~/.ssh/authorized_keys
param(
    [string]$Server = "ubuntu@129.204.33.138",
    [string]$KeyFile = "$env:USERPROFILE\.ssh\dsh_wutong",
    [string]$RemoteRoot = "/home/ubuntu/apps/auto-parts-agents",
    [string]$BundleLocal = "$PSScriptRoot\..\backend\data\wutong-update-20261017.bundle",
    [string]$BundleRemote = "/home/ubuntu/wutong-update-20261017.bundle"
)
$ErrorActionPreference = 'Stop'

function Invoke-Remote {
    param([string]$Command, [switch]$AllowFail)
    $out = & ssh -i $KeyFile -o BatchMode=yes -o ConnectTimeout=15 $Server $Command 2>&1
    $code = $LASTEXITCODE
    $text = ($out | Out-String)
    if ($code -ne 0 -and -not $AllowFail) {
        throw ("远程命令失败 (exit=" + $code + "): " + $Command + "`n" + $text)
    }
    return @{ Code = $code; Text = $text }
}

Write-Host "=== 0. 连通性 ==="
$r = Invoke-Remote "hostname; whoami; cd $RemoteRoot && git rev-parse HEAD"
Write-Host $r.Text.Trim()

Write-Host "=== 1. 上传离线包 ==="
$localHash = (Get-FileHash $BundleLocal -Algorithm SHA256).Hash.ToLower()
& scp -i $KeyFile -o BatchMode=yes $BundleLocal ($Server + ":" + $BundleRemote) | Out-Null
if ($LASTEXITCODE -ne 0) { throw "scp 上传失败" }
$remoteHash = (Invoke-Remote "sha256sum $BundleRemote | cut -d' ' -f1").Text.Trim()
Write-Host ("本地 " + $localHash)
Write-Host ("远端 " + $remoteHash)
if ($localHash -ne $remoteHash) { throw "SHA256 不一致，上传的是旧包，停止" }

Write-Host "=== 2. 备份本地改动（含未跟踪）==="
$r = Invoke-Remote "cd $RemoteRoot && git stash push -u -m server-local-$(Get-Date -Format yyyyMMddHHmmss) && git status --porcelain | wc -l"
Write-Host $r.Text.Trim()

Write-Host "=== 3. 合并 ==="
$r = Invoke-Remote "cd $RemoteRoot && git fetch $BundleRemote main && git merge --ff-only FETCH_HEAD && git rev-parse HEAD"
Write-Host $r.Text.Trim()

Write-Host "=== 4. 校验新代码 ==="
$r = Invoke-Remote "cd $RemoteRoot && grep -c '\\[密钥\\]' scripts/start_wutong.sh; grep -c '_failure_text' backend/app/aip/aip_agent_service.py; grep -c 'deepseek_key_status' backend/app/aip/wutong_runtime.py"
Write-Host $r.Text.Trim()

Write-Host "=== 5. 恢复新提交未覆盖的本地改动 ==="
$r = Invoke-Remote "cd $RemoteRoot && git checkout 'stash@{0}' -- backend/seed_erpnext.py backend/seed_openmes.py scripts/stop_wutong.sh && git status --short"
Write-Host $r.Text.Trim()

Write-Host "=== 6. 装依赖 ==="
$r = Invoke-Remote "cd $RemoteRoot && .venv/bin/python -m pip install -q -e './backend[wutong]' && echo PIP_OK"
Write-Host $r.Text.Trim()

Write-Host "=== 7. 重启接入进程 ==="
Invoke-Remote "cd $RemoteRoot && bash scripts/stop_wutong.sh" | Out-Null
$r = Invoke-Remote "cd $RemoteRoot && unset DEEPSEEK_API_KEY && bash scripts/start_wutong.sh"
Write-Host $r.Text.Trim()

Write-Host "=== 8. 启动日志中的密钥判定 ==="
$r = Invoke-Remote "cd $RemoteRoot && grep -m2 '密钥' backend/data/wutong-runtime/logs/leader.log"
Write-Host $r.Text.Trim()

Write-Host "=== 完成 ==="
