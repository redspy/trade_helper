# trade_dash 서버 운용 계획 (CI/CD)

> 기준: `~/Source/helper`의 검증된 운용 패턴(self-hosted runner + pm2 + 서버 상주
> `.env`)을 그대로 준용하되, trade_dash의 특성(상시 웹 + 야간 배치 이원 구조,
> Python/Streamlit)에 맞게 조정한다.

## 0. helper 패턴 분석 요약

| 항목 | helper 방식 | trade_dash 적용 |
|---|---|---|
| CI | PR → GitHub-hosted, 문법 체크 | 동일 + 지표/DB 스모크 테스트 |
| CD | main push → **self-hosted runner**(Windows, PowerShell) | 동일 러너에 repo 추가 |
| 시크릿 | GitHub Secrets가 아닌 **러너 루트 `.env` 상주** → 배포 시 복사 | 동일 |
| 프로세스 관리 | pm2 (stop → 파일 갱신 → 의존성 → restart → save) | 동일 (Streamlit을 pm2로) |
| 경로 검증 | `run.cmd` 존재 확인으로 러너 루트 검증 | 동일 |

## 1. 실행 토폴로지

trade_dash는 helper와 달리 **프로세스가 두 개**다:

```
서버 (helper와 동일 머신 가능)
├── [상시] trade-dash-web  : Streamlit 대시보드 (pm2 관리, :8501)
└── [일 1회] nightly batch : python -m src.main
                             (GitHub Actions cron 0 16 * * * → self-hosted 러너에서 실행)
```

- **대시보드**: pm2가 부팅 시 자동 시작·크래시 재시작 담당.
  `pm2 start --name trade-dash-web "….venv/Scripts/streamlit run dashboard/app.py --server.headless true --server.port 8501"`
- **배치**: 별도 상주 프로세스 없이 GitHub Actions cron이 self-hosted 러너에서
  `python -m src.main`을 실행. 실행 이력·로그가 GitHub에 남아 관측성이 좋고,
  helper와 운영 방식이 일관된다.
  - 대안(옵션 B): 서버 crontab/작업 스케줄러 — GitHub 장애와 무관하게 동작하지만
    이력 관리가 분산됨. **중복 실행 방지를 위해 A/B 중 하나만 켠다.**

## 2. 워크플로 구성

### ci.yml (신규 — helper ci.yml 준용)
```yaml
on: pull_request → ubuntu-latest
  - pip install -r requirements.txt
  - python -m py_compile src/*.py dashboard/app.py
  - 스모크 테스트: 지표 계산(베타 회수), DB upsert 멱등성  # 기존 검증 스크립트를 tests/로 이관
```

### deploy.yml (신규 — helper deploy.yml 준용)
```yaml
on: push(main) → runs-on: self-hosted
  - checkout
  - scripts/deploy.ps1   # 서버가 Windows(helper 러너와 동일 머신)인 경우
```

deploy 스크립트 절차 (helper deploy.ps1과 동일 구조):
1. 러너 루트 검증 (`run.cmd` 존재 확인)
2. 러너 루트의 `.env` → 워크스페이스 복사
3. `python -m venv .venv` (없으면) + `pip install -r requirements.txt`
4. `pm2 restart trade-dash-web` (없으면 start) + `pm2 save`

### nightly_batch.yml (수정)
- `runs-on: ubuntu-latest` → **`self-hosted`**
- **SQLite 커밋백 스텝 제거** — DB가 서버 로컬 소유가 되므로 불필요
  (커밋백은 GitHub-hosted 러너 + 원격 대시보드 전제였음)
- 실패 시 Telegram 알림 스텝 추가 (helper의 기존 봇 토큰 재활용, curl 1줄)

## 3. LLM 인증 — 서버에는 브라우저 로그인이 없다

서버 `.env`에 둘 중 하나를 배치 (`LLM_BACKEND=auto`가 자동 선택):

| 방식 | 서버 .env | 비고 |
|---|---|---|
| **A. API 키 (권장)** | `ANTHROPIC_API_KEY=sk-ant-…` | `messages.parse()` 스키마 강제 — 무인 운영에 가장 안정적 |
| B. Claude 구독 유지 | `claude` CLI 설치 + `claude setup-token`으로 발급한 장기 토큰(`CLAUDE_CODE_OAUTH_TOKEN`) | 구독 요금제 활용, 토큰 만료 시 갱신 필요 |

## 4. 데이터 전략

- **1단계 (현행 유지)**: 서버 로컬 SQLite (`data/trade_dash.db`).
  단일 서버·일 1회 쓰기라 충분하다.
- **백업**: 배치 성공 후 `data/backup/trade_dash_YYYYMMDD.db` 로컬 복사
  (7일 보관) — 배치 workflow 마지막 스텝.
- **전환 트리거**: 페어 수 확대·다중 쓰기 프로세스·원격 대시보드 필요 시
  `.env`의 `DATABASE_URL`만 PostgreSQL로 교체 (코드 변경 불필요).

## 5. 서버 디렉토리 레이아웃 (helper 준용)

```
{runner_root}/                  # helper와 동일 루트 공유 가능 (러너는 repo별 _work 분리)
├── run.cmd                     # GitHub Actions runner
├── .env                        # trade_dash 시크릿 (서버에만 존재, 절대 커밋 금지)
├── trade-dash-data/            # ⚠️ DB는 워크스페이스 밖에 (checkout 리셋 대비)
│   ├── trade_dash.db           #    .env: DATABASE_URL=sqlite:///{절대경로}
│   └── backup/                 #    일자별 백업 (scripts/backup_db.py, 7일 보관)
├── trade-dash-app/             # ⚠️ 실행 디렉토리 (robocopy 배포 대상, venv 포함)
│   └── .venv/                  #    앱은 반드시 여기서 실행 — 워크스페이스에서 직접
│                               #    실행하면 파일 잠금으로 다음 checkout이 실패함
└── _work/trade_helper/trade_helper  # checkout 전용 (실행 금지)
```

> **왜 DB를 밖에 두나**: self-hosted 러너에서 `actions/checkout`은 매 실행마다
> 워크스페이스를 clean/reset 한다. DB가 안에 있으면 배치가 쌓은 데이터가
> 다음 배포/배치 때 저장소 버전으로 되돌아간다.

## 6. 롤아웃 순서

1. **GitHub 저장소 푸시** (선행 조건 — gh 로그인 후 `gh repo create` + push)
2. 서버의 기존 러너에 trade_dash repo 등록 (또는 신규 러너 설치),
   러너 루트에 `.env` 배치 (ANTHROPIC_API_KEY 또는 OAuth 토큰 포함)
3. `ci.yml` + `deploy.yml` + `scripts/deploy.ps1` 추가 → main 푸시로 대시보드
   상시 서비스 확인 (pm2 list, :8501 헬스체크)
4. `nightly_batch.yml`을 self-hosted로 전환, 커밋백 제거, 새벽 실행 1회 관찰
5. Telegram 실패 알림 + 일자별 DB 백업 추가

## 6.5 pm2 데몬 부트스트랩 (서버 1회 필수) — ⚠️ 잡 안에서 낳은 데몬은 죽는다

서비스형 GitHub 러너는 잡 종료 시 Job Object로 자식 프로세스를 강제 정리하므로
(환경변수 우회 불가), **배포 잡 안에서 처음 생성된 pm2 데몬은 잡이 끝나면
대시보드와 함께 종료된다.** helper가 살아있는 이유는 데몬이 셋업 때 사용자
세션(잡 밖)에서 태어났기 때문 — trade_dash도 동일하게 1회 부트스트랩한다:

```powershell
# 서버의 일반 PowerShell 창에서 1회
$env:PM2_HOME = "D:\runners\trading-runner\pm2-home"
$pm2 = "D:\runners\trading-runner\pm2-local\node_modules\pm2\bin\pm2"
node $pm2 resurrect        # 마지막 pm2 save 상태(trade-dash-web) 복원
node $pm2 list             # online 확인
netstat -ano | findstr :8501

# (선택) 재부팅 대비 — 로그온 시 자동 복원
schtasks /Create /TN "trade-dash-pm2-resurrect" /SC ONLOGON /F `
  /TR "cmd /c set PM2_HOME=D:\runners\trading-runner\pm2-home&& node D:\runners\trading-runner\pm2-local\node_modules\pm2\bin\pm2 resurrect"
```

이후의 배포 잡은 살아있는 데몬에 delete/start RPC만 보내므로(앱의 부모 = 잡 밖
데몬) 잡이 끝나도 프로세스가 유지된다.

## 7. 운영 체크리스트

- [ ] pm2 startup 등록 (서버 재부팅 시 자동 기동)
- [ ] 배치 소요시간 모니터 (현재 페어당 2~3분 × 4페어 ≈ 10분; timeout 30분)
- [ ] Actions 이력에서 주 1회 적중률·실패율 확인
- [ ] `.env`는 서버에만 — 저장소·문서·로그에 노출 금지 (helper 공통 원칙 5 준용)
