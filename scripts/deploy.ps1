# trade_dash 배포 스크립트 (Windows self-hosted runner)
# helper/scripts/deploy.ps1 패턴 준용:
#   Invoke-Native(PS5.1 NativeCommandError 차단) + 로컬 pm2를 node로 직접 실행
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Stop"

# Windows 기본 로케일(cp949)에서 UTF-8 파일(한글 주석 등)을 안전하게 읽도록 강제
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Native {
  param([scriptblock]$Command)
  # Redirect stderr to stdout to prevent NativeCommandError in PS 5.1
  $ErrorActionPreference = "SilentlyContinue"
  & $Command 2>&1 | Write-Host
  $exitCode = $LASTEXITCODE
  $ErrorActionPreference = "Stop"
  if ($exitCode -ne 0) {
    throw "Command failed (exit code $exitCode): $Command"
  }
}

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
  Invoke-Native { python -m venv .venv }
  Write-Host "[deploy] venv created"
}

Invoke-Native { & ".\.venv\Scripts\python.exe" -m pip install -q -r requirements.txt }
Write-Host "[deploy] dependencies installed"

# ---- pm2: helper 방식 — 글로벌 pm2 대신 로컬 설치본을 node로 직접 실행
#   (글로벌 npm .ps1 shim은 깨지기 쉽고, PS5.1 stderr 문제도 있음)
$pm2Root = Join-Path $runnerRoot "pm2-local"
$pm2 = Join-Path $pm2Root "node_modules\pm2\bin\pm2"
if (-not (Test-Path $pm2)) {
  Write-Host "[deploy] Installing local pm2 (first run)..."
  Invoke-Native { npm install --prefix $pm2Root pm2 --no-fund --no-audit }
}

# 기존 프로세스 제거 후 새로 시작 (helper의 delete → start 방식)
$ErrorActionPreference = "SilentlyContinue"
node $pm2 delete trade-dash-web 2>&1 | Out-Null
node $pm2 start ecosystem.config.js 2>&1 | ForEach-Object { Write-Host "[pm2] $_" }
$startOk = ($LASTEXITCODE -eq 0)
node $pm2 save 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

if (-not $startOk) { throw "[deploy] pm2 start failed" }
Write-Host "[deploy] Dashboard started (trade-dash-web)"
Write-Host "[deploy] done"
