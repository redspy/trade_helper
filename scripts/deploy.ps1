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
# PM2_HOME을 러너 루트에 고정 — 어떤 계정/세션에서든 같은 데몬을 조회 가능
$env:PM2_HOME = Join-Path $runnerRoot "pm2-home"

# ⚠️ 핵심: GitHub 러너는 잡 종료 시 GITHUB_ACTIONS_RUNNER_TRACKING_ID 환경변수를
# 물려받은 프로세스를 전부 정리한다. 잡 안에서 처음 생성되는 pm2 데몬이 이 변수를
# 상속하면 배포 완료 직후 데몬+대시보드가 함께 죽는다. 데몬을 띄우기 전에 비운다.
# (helper는 데몬이 셋업 때 잡 밖에서 이미 떠 있었기에 이 문제가 없었음)
$env:GITHUB_ACTIONS_RUNNER_TRACKING_ID = ""
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

# ---- 기동 검증: 8501 헬스체크 (최대 60초 대기)
Write-Host "[deploy] Waiting for dashboard health check..."
$healthy = $false
foreach ($i in 1..30) {
  try {
    $resp = Invoke-WebRequest -Uri "http://localhost:8501/_stcore/health" `
              -UseBasicParsing -TimeoutSec 2
    if ($resp.StatusCode -eq 200) { $healthy = $true; break }
  } catch { }
  Start-Sleep -Seconds 2
}

if (-not $healthy) {
  Write-Host "[deploy] Health check FAILED - dumping pm2 status/logs:"
  $ErrorActionPreference = "SilentlyContinue"
  node $pm2 list 2>&1 | ForEach-Object { Write-Host "[pm2] $_" }
  node $pm2 logs trade-dash-web --lines 40 --nostream 2>&1 | ForEach-Object { Write-Host "[pm2-log] $_" }
  $ErrorActionPreference = "Stop"
  throw "[deploy] dashboard did not become healthy on :8501"
}
Write-Host "[deploy] Dashboard healthy on :8501 (trade-dash-web)"
Write-Host "[deploy] done"
