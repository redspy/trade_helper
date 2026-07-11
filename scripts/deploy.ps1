# trade_dash 배포 스크립트 (Windows self-hosted runner)
# helper/scripts/deploy.ps1 패턴 준용:
#   러너 루트 검증 → .env 복사 → venv/의존성 → pm2 재시작
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Stop"

# GITHUB_WORKSPACE = {runner_root}\_work\{repo}\{repo}
$runnerRoot = (Resolve-Path "$env:GITHUB_WORKSPACE\..\..\..\").Path
$envSource  = Join-Path $runnerRoot ".env"

Write-Host "[deploy] Runner root : $runnerRoot"
Write-Host "[deploy] Workspace   : $env:GITHUB_WORKSPACE"

if (-not (Test-Path (Join-Path $runnerRoot "run.cmd"))) {
  throw "[deploy] ERROR: run.cmd not found - check runner root path: $runnerRoot"
}
if (-not (Test-Path $envSource)) {
  throw "[deploy] ERROR: .env not found at: $envSource"
}

# .env 복사 (시크릿은 서버에만 상주)
Copy-Item $envSource (Join-Path $env:GITHUB_WORKSPACE ".env") -Force
Write-Host "[deploy] .env copied"

Set-Location $env:GITHUB_WORKSPACE

# venv 준비 (최초 1회 생성, 이후 재사용)
if (-not (Test-Path ".venv")) {
  python -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw "[deploy] venv creation failed" }
  Write-Host "[deploy] venv created"
}

& ".\.venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "[deploy] pip install failed" }
Write-Host "[deploy] dependencies installed"

# pm2로 대시보드 재시작 (없으면 신규 시작) — pm2는 전역 설치 전제 (helper 서버와 공유)
pm2 describe trade-dash-web *> $null
if ($LASTEXITCODE -eq 0) {
  pm2 restart ecosystem.config.js --update-env
  Write-Host "[deploy] dashboard restarted"
} else {
  pm2 start ecosystem.config.js
  Write-Host "[deploy] dashboard started"
}
pm2 save
Write-Host "[deploy] done"
