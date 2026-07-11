#!/bin/bash
# trade_dash 배포 스크립트 (Linux self-hosted runner용 — Windows는 deploy.ps1)
set -e

# GITHUB_WORKSPACE = {runner_root}/_work/{repo}/{repo}
RUNNER_ROOT="$(cd "$GITHUB_WORKSPACE/../../.." && pwd)"
ENV_SOURCE="$RUNNER_ROOT/.env"

echo "[deploy] Runner root: $RUNNER_ROOT"

if [ ! -f "$RUNNER_ROOT/run.cmd" ] && [ ! -f "$RUNNER_ROOT/run.sh" ]; then
  echo "[deploy] ERROR: run.cmd/run.sh 를 찾을 수 없습니다 — 경로 확인: $RUNNER_ROOT"
  exit 1
fi
if [ ! -f "$ENV_SOURCE" ]; then
  echo "[deploy] ERROR: .env 파일이 없습니다: $ENV_SOURCE"
  exit 1
fi

cp "$ENV_SOURCE" "$GITHUB_WORKSPACE/.env"
echo "[deploy] .env 복사 완료"

cd "$GITHUB_WORKSPACE"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "[deploy] venv 생성"
fi
.venv/bin/pip install -q -r requirements.txt
echo "[deploy] 의존성 설치 완료"

if pm2 describe trade-dash-web > /dev/null 2>&1; then
  pm2 restart ecosystem.config.js --update-env
  echo "[deploy] 대시보드 재시작 완료"
else
  pm2 start ecosystem.config.js
  echo "[deploy] 대시보드 시작 완료"
fi
pm2 save
echo "[deploy] 배포 완료"
