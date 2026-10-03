<#
.SYNOPSIS
    在一台新的 Windows 电脑上准备本项目的真实 ERPNext + OpenMES 环境。

.DESCRIPTION
    这是“同学电脑真实复现”的引导脚本，不包含任何凭据，也不会把上游
    ERPNext/OpenMES 源码提交到本仓库。它只负责检查工具、获取缺失的上游
    仓库、生成本机模板、调用已有 compose 部署脚本、等待健康状态、完成
    本地业务库迁移，并在人工完成凭据/建站后按明确顺序执行种子脚本。

    真实系统初始化仍有必须由使用者确认的步骤：ERPNext localhost 建站、
    创建专用 API 用户/密钥、OpenMES 管理员配置，以及是否执行会产生重复
    业务记录的非幂等种子。脚本不会读取、打印或提交密钥。

.PARAMETER RunSeeds
    在真实系统连通且凭据已配置后，运行基础种子脚本。seed_openmes.py
    的导入接口支持更新已有业务编号，但质量问题仍使用 POST，重复运行前必须核对。

.PARAMETER RunLegacySeeds
    明确允许执行有当前库前置条件或非幂等风险的旧种子：
    seed_openmes_v2.py、seed_openmes_v3.py。默认不执行。

.PARAMETER RunEtaSeed
    单独允许执行 seed_inspection_eta.py。该脚本依赖固定容器名、固定 TEST_ 批次
    与当前 OpenMES 表结构；默认不执行，不能当作跨机器通用种子。

.PARAMETER RunRealDemoSeed
    执行 backend/seed_real_demo.py，在本机真实 OpenMES 上按 TEST_ 工单号查重并
    导入可复现的演示工单和质量问题。产品类型、产线和问题类型缺失时会停止，
    不猜测创建方式。

.PARAMETER NoPrompt
    不等待人工确认。遇到未完成的建站、凭据或种子条件时立即退出并打印下一步。

.PARAMETER TimeoutSeconds
    等待容器健康的最长秒数，默认 600。
#>

[CmdletBinding()]
param(
    [switch]$RunSeeds,
    [switch]$RunLegacySeeds,
    [switch]$RunEtaSeed,
    [switch]$RunRealDemoSeed,
    [switch]$NoPrompt,
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$openmesDir = Join-Path $root "services\OpenMes"
$frappeDir = Join-Path $root "services\frappe_docker"
$rootEnv = Join-Path $root ".env"
$frontendEnv = Join-Path $root "frontend\.env.local"

function Write-Step([string]$Message) { Write-Host "`n[步骤] $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "[通过] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[待办] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "[失败] $Message" -ForegroundColor Red }

function Require-Command([string]$Name, [string]$InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name 不在 PATH 中。请先安装：$InstallHint"
    }
}

function Read-EnvFile([string]$Path) {
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path -ErrorAction Stop) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Require-EnvValue([hashtable]$Values, [string]$Name, [string]$Where) {
    if (-not $Values.ContainsKey($Name) -or [string]::IsNullOrWhiteSpace([string]$Values[$Name])) {
        Write-Warn "$Where 缺少 $Name。请在本机文件中填写后重新运行。"
        return $false
    }
    return $true
}

function Wait-Http([string]$Name, [string]$Url, [int]$Timeout) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Ok "$Name 已响应：HTTP $($response.StatusCode)"
                return $true
            }
        } catch {
            # 容器启动阶段会短暂连接失败；只在超时后报告原因。
        }
        Start-Sleep -Seconds 3
    }
    Write-Fail "$Name 在 $Timeout 秒内未响应：$Url"
    return $false
}

function Confirm-OrStop([string]$Prompt) {
    if ($NoPrompt) { return $false }
    $answer = Read-Host "$Prompt [y/N]"
    return $answer -match '^(y|yes|是)$'
}

try {
    Write-Step "检查本机工具"
    Require-Command "docker" "Docker Desktop（并启动它）"
    Require-Command "git" "Git for Windows"
    Require-Command "python" "Python 3.12 或 3.13"
    Require-Command "node" "Node.js 20 或更高版本"
    Require-Command "npm" "随 Node.js 安装的 npm"
    docker version --format "{{.Server.Version}}" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop 未运行，或当前用户无权访问 Docker Engine。" }
    docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose v2 不可用。" }

    $pyVersion = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($pyVersion -notmatch '^3\.(12|13)$') { throw "当前 Python 是 $pyVersion；项目要求 Python 3.12 或 3.13。" }
    $nodeMajor = (& node --version).Trim() -replace '^v','' -split '\.' | Select-Object -First 1
    if ([int]$nodeMajor -lt 20) { throw "当前 Node.js 版本过低（$(& node --version)）；项目要求 20+。" }
    Write-Ok "Docker、Git、Python $pyVersion、Node.js $(& node --version) 和 npm 已就绪"

    Write-Step "获取上游 ERPNext/OpenMES 源码（不写入本仓库 Git）"
    if (-not (Test-Path (Join-Path $openmesDir ".git"))) {
        if (Test-Path $openmesDir) { throw "services/OpenMes 已存在但不是 Git 工作区，请先移走后再运行。" }
        git clone --depth 1 https://github.com/Mes-Open/OpenMes.git $openmesDir
    } else { Write-Ok "services/OpenMes 已存在，跳过克隆" }
    if (-not (Test-Path (Join-Path $frappeDir ".git"))) {
        if (Test-Path $frappeDir) { throw "services/frappe_docker 已存在但不是 Git 工作区，请先移走后再运行。" }
        git clone --depth 1 https://github.com/frappe/frappe_docker.git $frappeDir
    } else { Write-Ok "services/frappe_docker 已存在，跳过克隆" }

    Write-Step "生成本机配置模板（不写真实密钥）"
    if (-not (Test-Path $rootEnv)) {
        Copy-Item (Join-Path $root ".env.example") $rootEnv
        Write-Warn "已生成根目录 .env；它只包含模板，ERPNext/OpenMES/LLM 凭据仍为空。"
    } else { Write-Ok "根目录 .env 已存在，不覆盖已有本机配置" }
    if (-not (Test-Path (Join-Path $openmesDir ".env"))) {
        $openmesExample = Join-Path $openmesDir ".env.example"
        if (-not (Test-Path $openmesExample)) { throw "OpenMES 上游未提供 .env.example，无法安全生成配置。" }
        Copy-Item $openmesExample (Join-Path $openmesDir ".env")
        Write-Warn "已生成 services/OpenMes/.env；请按上游模板设置管理员账号/密码。"
    } else { Write-Ok "services/OpenMes/.env 已存在，不覆盖已有本机配置" }
    if (-not (Test-Path (Join-Path $frappeDir ".env"))) {
        $erpTemplate = Join-Path $PSScriptRoot "templates\erpnext.env.example"
        if (-not (Test-Path $erpTemplate)) { throw "缺少本仓库的 ERPNext env 模板：$erpTemplate" }
        Copy-Item $erpTemplate (Join-Path $frappeDir ".env")
        Write-Warn "已生成 services/frappe_docker/.env；请至少设置 DB_PASSWORD。"
    } else { Write-Ok "services/frappe_docker/.env 已存在，不覆盖已有本机配置" }
    if (-not (Test-Path $frontendEnv)) {
        Set-Content -LiteralPath $frontendEnv -Encoding UTF8 -Value "VITE_API_BASE_URL=http://127.0.0.1:9000/api"
        Write-Ok "已生成 frontend/.env.local（只包含本地后端地址）"
    }

    $openmesValues = Read-EnvFile (Join-Path $openmesDir ".env")
    if (-not (Require-EnvValue $openmesValues "ADMIN_PASSWORD" "services/OpenMes/.env")) {
        if ($NoPrompt) { throw "OpenMES 管理员密码尚未配置。" }
        Write-Host "请编辑 services/OpenMes/.env 设置管理员密码；不要把该文件提交到 GitHub。" -ForegroundColor Yellow
        Read-Host "完成后按 Enter 继续"
        $openmesValues = Read-EnvFile (Join-Path $openmesDir ".env")
        if (-not (Require-EnvValue $openmesValues "ADMIN_PASSWORD" "services/OpenMes/.env")) { throw "OpenMES 管理员密码仍为空。" }
    }
    $erpValues = Read-EnvFile (Join-Path $frappeDir ".env")
    if (-not (Require-EnvValue $erpValues "DB_PASSWORD" "services/frappe_docker/.env")) {
        if ($NoPrompt) { throw "ERPNext DB_PASSWORD 尚未配置。" }
        Write-Host "请编辑 services/frappe_docker/.env 设置 DB_PASSWORD；不要把该文件提交到 GitHub。" -ForegroundColor Yellow
        Read-Host "完成后按 Enter 继续"
        $erpValues = Read-EnvFile (Join-Path $frappeDir ".env")
        if (-not (Require-EnvValue $erpValues "DB_PASSWORD" "services/frappe_docker/.env")) { throw "ERPNext DB_PASSWORD 仍为空。" }
    }

    Write-Step "启动 OpenMES 与 ERPNext compose 项目"
    & (Join-Path $PSScriptRoot "deploy_services.ps1")
    if ($LASTEXITCODE -ne 0) { throw "现有 deploy_services.ps1 启动外部服务失败。" }
    $openmesReady = Wait-Http "OpenMES" "http://127.0.0.1/api/health" $TimeoutSeconds
    $erpReady = Wait-Http "ERPNext" "http://127.0.0.1:8080/api/method/ping" $TimeoutSeconds
    if (-not ($openmesReady -and $erpReady)) { throw "外部服务尚未全部健康；不要继续执行种子。" }

    Write-Step "初始化本项目 SQLite/Alembic 业务库"
    $backend = Join-Path $root "backend"
    $pythonExe = "python"
    if (Test-Path (Join-Path $root ".conda-env\python.exe")) { $pythonExe = Join-Path $root ".conda-env\python.exe" }
    & $pythonExe -m pip install -e (Join-Path $root "acps-sdk-src\acps-sdk")
    if ($LASTEXITCODE -ne 0) { throw "ACPS SDK 依赖安装失败。" }
    & $pythonExe -m pip install -e $backend
    if ($LASTEXITCODE -ne 0) { throw "后端依赖安装失败。" }
    Push-Location $backend
    try { & $pythonExe -m alembic upgrade head } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Alembic 迁移失败。" }
    Write-Ok "本地业务库已执行 alembic upgrade head（不会覆盖已有库）"

    Write-Step "检查 ERPNext localhost 建站与应用凭据"
    $siteCheck = & docker compose --project-name erpnext -f (Join-Path $frappeDir "compose.yaml") -f (Join-Path $frappeDir "overrides\compose.mariadb.yaml") -f (Join-Path $frappeDir "overrides\compose.redis.yaml") -f (Join-Path $frappeDir "overrides\compose.noproxy.yaml") --env-file (Join-Path $frappeDir ".env") exec -T backend bench --site localhost list-apps 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "ERPNext 的 localhost 站点尚未确认。请执行以下一次性建站命令（密码只从本机 .env 读取，不要粘贴给别人）："
        Write-Host 'docker compose --project-name erpnext exec backend bench new-site localhost --mariadb-user-host-login-search=% --db-root-password <services/frappe_docker/.env 中 DB_PASSWORD> --admin-password <你设置的 ERPNext 管理员密码> --install-app erpnext' -ForegroundColor Yellow
        if ($NoPrompt -or -not (Confirm-OrStop "已完成建站并安装 erpnext 吗？")) { throw "请先完成 ERPNext localhost 建站，再重新运行脚本。" }
        $siteCheck = & docker compose --project-name erpnext -f (Join-Path $frappeDir "compose.yaml") -f (Join-Path $frappeDir "overrides\compose.mariadb.yaml") -f (Join-Path $frappeDir "overrides\compose.redis.yaml") -f (Join-Path $frappeDir "overrides\compose.noproxy.yaml") --env-file (Join-Path $frappeDir ".env") exec -T backend bench --site localhost list-apps 2>&1
        if ($LASTEXITCODE -ne 0) { throw "仍无法确认 ERPNext localhost 站点，请检查容器日志。" }
    }
    Write-Ok "ERPNext localhost 站点已可用"

    $projectValues = Read-EnvFile $rootEnv
    $missingProject = @("ERPNEXT_API_KEY", "ERPNEXT_API_SECRET", "REAL_WRITE_API_TOKEN") | Where-Object { -not (Require-EnvValue $projectValues $_ ".env") }
    if ($missingProject.Count -gt 0) {
        Write-Warn "根目录 .env 尚缺少真实应用凭据：$($missingProject -join ', ')。请用各自系统的专用账号填写，脚本不会替你生成或复制。"
        if ($NoPrompt) { throw "真实应用凭据尚未配置。" }
        Read-Host "完成 .env 后按 Enter 继续"
    }
    $projectValues = Read-EnvFile $rootEnv
    if (-not (Require-EnvValue $projectValues "ERPNEXT_API_KEY" ".env") -or -not (Require-EnvValue $projectValues "ERPNEXT_API_SECRET" ".env")) {
        throw "ERPNext API Key/Secret 仍为空；不能执行真实种子。"
    }

    if ($RunSeeds) {
        Write-Step "按固定顺序执行种子脚本"
        Write-Host "基础顺序：seed_erpnext.py → seed_openmes.py → seed_supplier_data.py" -ForegroundColor Cyan
        Write-Host "注意：seed_openmes.py 的产品/工单导入支持 update_or_create，但质量问题使用 POST，重复运行前应人工核对。" -ForegroundColor Yellow
        if (-not $NoPrompt -and -not (Confirm-OrStop "确认现在执行上述种子吗？")) { throw "用户取消种子执行。" }
        $safeSeeds = @("seed_erpnext.py", "seed_openmes.py", "seed_supplier_data.py")
        foreach ($seed in $safeSeeds) {
            Write-Host "[种子] $seed" -ForegroundColor Cyan
            & $pythonExe (Join-Path $backend $seed)
            if ($LASTEXITCODE -ne 0) { throw "$seed 执行失败。" }
        }
        if ($RunRealDemoSeed) {
            Write-Host "[种子] seed_real_demo.py（动态查重的 TEST_ 演示工单/质量问题）" -ForegroundColor Cyan
            & $pythonExe (Join-Path $backend "seed_real_demo.py")
            if ($LASTEXITCODE -ne 0) { throw "seed_real_demo.py 执行失败。" }
        } else {
            Write-Warn "未执行 seed_real_demo.py。需要导入可复现 TEST_ 演示工单和质量问题时，请加 -RunRealDemoSeed。"
        }
        if ($RunLegacySeeds) {
            Write-Warn "即将执行旧/高风险种子：seed_openmes_v2.py、seed_openmes_v3.py。它们会 POST 工单或质量问题，重复运行可能重复记录。"
            Write-Warn "这些脚本不能直接视为跨机器可复现导入；只有核对当前 OpenMES 版本、工单和 Issue Type 后才可执行。"
            if (-not $NoPrompt) {
                $legacyAnswer = Read-Host "已备份并核对当前 OpenMES 数据，仍要执行这两个旧种子吗？输入 RUN 才继续"
                if ($legacyAnswer -ne "RUN") { throw "用户取消旧/高风险种子执行。" }
            }
            foreach ($seed in @("seed_openmes_v2.py", "seed_openmes_v3.py")) {
                Write-Host "[旧/高风险种子] $seed" -ForegroundColor Yellow
                & $pythonExe (Join-Path $backend $seed)
                if ($LASTEXITCODE -ne 0) { throw "$seed 执行失败。" }
            }
        } else {
            Write-Warn "已跳过旧/高风险种子 v2、v3。若确需尝试，请先核对数据并加 -RunLegacySeeds；它们不是跨机器无条件可复现步骤。"
        }
        if ($RunEtaSeed) {
            Write-Warn "即将执行 seed_inspection_eta.py。它固定依赖容器 openmes-postgres、TEST_WO_PAGE_00023/TEST_LOT_PAGE_9 和当前 OpenMES 表结构；缺任一前置条件都会失败。"
            if (-not (Test-Path (Join-Path $openmesDir ".env"))) { throw "缺少 OpenMES .env，不能执行 ETA 种子。" }
            if (-not $NoPrompt) {
                $etaAnswer = Read-Host "已确认 TEST_LOT_PAGE_9 与 openmes-postgres 存在，仍要执行 ETA 种子吗？输入 RUN 才继续"
                if ($etaAnswer -ne "RUN") { throw "用户取消 ETA 种子执行。" }
            }
            & $pythonExe (Join-Path $backend "seed_inspection_eta.py")
            if ($LASTEXITCODE -ne 0) { throw "seed_inspection_eta.py 执行失败。" }
        } else {
            Write-Warn "已跳过 seed_inspection_eta.py。它依赖当前库固定 TEST_ 批次，不能当作同学电脑的通用导入步骤；核对前置条件后可加 -RunEtaSeed。"
        }
        Write-Ok "已完成本轮种子步骤；请在页面逐项验收真实数据与审批门禁。"
    } else {
        Write-Warn "未执行种子。需要导入演示数据时，重新运行并加 -RunSeeds；非幂等种子还需显式加 -AllowNonIdempotentSeeds。"
    }

    Write-Host "`n[完成] 真实服务与应用准备流程结束。应用入口仍由 scripts/start_demo.ps1 启动：http://127.0.0.1:5173/" -ForegroundColor Green
    Write-Host "[安全] 不要把 .env、services/*/.env、Token、API Secret、Cookie 或证书提交到 GitHub。" -ForegroundColor Yellow
    exit 0
} catch {
    Write-Fail $_.Exception.Message
    exit 1
}
