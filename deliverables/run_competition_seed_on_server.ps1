# Run in a local Windows PowerShell window. SSH/SCP prompt for the server password
# interactively; this script does not read, store, print or transmit that password.
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\backend\seed_competition_records.py'
$source = (Resolve-Path -LiteralPath $source).Path
$remote = 'ubuntu@129.204.33.138'
$target = '/home/ubuntu/apps/auto-parts-agents/backend/seed_competition_records.py'
$project = '/home/ubuntu/apps/auto-parts-agents'

Write-Host '上传比赛业务数据脚本到服务器...'
& scp $source ('{0}:{1}' -f $remote, $target)
if ($LASTEXITCODE -ne 0) { throw '上传失败；没有在服务器创建业务数据。' }

Write-Host '先做只读检查...'
& ssh $remote "cd $project && .venv/bin/python backend/seed_competition_records.py --check"
if ($LASTEXITCODE -ne 0) { throw '只读检查失败；没有执行创建步骤。' }

Write-Host '创建缺失的 ERP 订单、MES 工单和 PPAP 任务，并回读...'
& ssh $remote "cd $project && .venv/bin/python backend/seed_competition_records.py --apply && .venv/bin/python backend/seed_competition_records.py --check"
if ($LASTEXITCODE -ne 0) { throw '创建或回读未全部通过；请保存输出，已有记录不会在重跑时被覆盖。' }

Write-Host '服务器已完成三套业务记录的创建和二次只读核验。'
