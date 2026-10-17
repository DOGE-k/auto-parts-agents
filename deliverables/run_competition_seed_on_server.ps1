# Run in a local Windows PowerShell window. SSH/SCP prompt for the server password
# interactively; this script does not read, store, print or transmit that password.
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\backend\seed_competition_records.py'
$source = (Resolve-Path -LiteralPath $source).Path
$remote = 'ubuntu@129.204.33.138'
$target = '/home/ubuntu/apps/auto-parts-agents/backend/seed_competition_records.py'
$project = '/home/ubuntu/apps/auto-parts-agents'

Write-Host 'Uploading competition records script to server...'
& scp $source ('{0}:{1}' -f $remote, $target)
if ($LASTEXITCODE -ne 0) { throw 'Upload failed. No business records were created.' }

Write-Host 'Running read-only preflight...'
& ssh $remote "cd $project && .venv/bin/python backend/seed_competition_records.py --check"
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed. No creation step was run.' }

Write-Host 'Creating missing ERP, MES and PPAP records; then reading back...'
& ssh $remote "cd $project && .venv/bin/python backend/seed_competition_records.py --apply && .venv/bin/python backend/seed_competition_records.py --check"
if ($LASTEXITCODE -ne 0) { throw 'Creation or verification failed. Existing records will not be overwritten on retry.' }

Write-Host 'Server-side creation and second read-only verification completed.'
