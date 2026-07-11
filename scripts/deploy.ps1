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

# ---- pm2 준비: helper 방식 — 글로벌 pm2 대신 로컬 설치본을 node로 직접 실행
#   (글로벌 npm .ps1 shim은 깨지기 쉽고, PS5.1 stderr 문제도 있음)
# PM2_HOME을 러너 루트에 고정 — 어떤 계정/세션에서든 같은 데몬을 조회 가능
$env:PM2_HOME = Join-Path $runnerRoot "pm2-home"

# ⚠️ GitHub 러너는 잡 종료 시 GITHUB_ACTIONS_RUNNER_TRACKING_ID 환경변수를
# 물려받은 프로세스를 정리한다. 잡 안에서 데몬이 처음 생성되는 경우를 대비해 비운다.
# (정상 운용 시 데몬은 서버 부트스트랩으로 잡 밖에서 이미 떠 있음 — ops-plan §6.5)
$env:GITHUB_ACTIONS_RUNNER_TRACKING_ID = ""
$pm2Root = Join-Path $runnerRoot "pm2-local"
$pm2 = Join-Path $pm2Root "node_modules\pm2\bin\pm2"
if (-not (Test-Path $pm2)) {
  Write-Host "[deploy] Installing local pm2 (first run)..."
  Invoke-Native { npm install --prefix $pm2Root pm2 --no-fund --no-audit }
}

# ---- Step 1: 앱 정지 (app 디렉토리 파일 잠금 해제 — helper의 stop-first 방식)
$ErrorActionPreference = "SilentlyContinue"
node $pm2 stop trade-dash-web 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
Write-Host "[deploy] app stopped (or was not running)"

# ---- Step 2: 워크스페이스 → app 디렉토리 동기화 (helper의 robocopy 방식)
# ⚠️ 앱을 워크스페이스에서 직접 실행하면 Windows 파일 잠금 때문에 다음 checkout이
#    실패한다. 반드시 별도 app 디렉토리로 복사해 거기서 실행한다.
$appDir = Join-Path $runnerRoot "trade-dash-app"
if (-not (Test-Path $appDir)) {
  New-Item -ItemType Directory -Path $appDir | Out-Null
  Write-Host "[deploy] app directory created"
}
robocopy $env:GITHUB_WORKSPACE $appDir /E /XD .git .venv data __pycache__ /XF "*.db" /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
  throw "[deploy] robocopy failed (exit code $LASTEXITCODE)"
}
Write-Host "[deploy] source synced to $appDir"

# .env 복사 (시크릿은 서버에만 상주)
Copy-Item $envSource (Join-Path $appDir ".env") -Force
Write-Host "[deploy] .env copied"

Set-Location $appDir

# ---- Step 3: venv 준비 (app 디렉토리 안, 최초 1회 생성 후 재사용)
if (-not (Test-Path ".venv")) {
  Invoke-Native { python -m venv .venv }
  Write-Host "[deploy] venv created"
}
Invoke-Native { & ".\.venv\Scripts\python.exe" -m pip install -q -r requirements.txt }
Write-Host "[deploy] dependencies installed"

# ---- Step 4: 앱 시작 (helper의 delete → start → save 방식)
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
