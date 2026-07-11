# 📈 trade_dash — AI 기반 글로벌 주식 분석 시스템

매일 새벽 1시(KST), 한국 종목과 연동된 미국 선행 종목의 시세·기술적 지표·뉴스를
수집하고, Claude LLM으로 감성 분석 + 최종 뷰(매수/중립/관망) 리포트를 생성하여
Streamlit 대시보드로 시각화하는 배치 파이프라인입니다.

## 아키텍처

```
GitHub Actions (01:00 KST cron)
        │
        ▼
┌─ src/main.py ──────────────────────────────────────────┐
│ 1. collector.py   yfinance 시세 30일 + 뉴스 헤드라인    │
│ 2. indicators.py  RSI(14) / SMA(20,60) / MACD 계산      │
│ 3. analyzer.py    Claude messages.parse() → Pydantic    │
│                   구조화 리포트 (감성 -1~+1, 최종 뷰)   │
│ 4. database.py    SQLite ⇄ PostgreSQL(JSONB) 저장       │
└─────────────────────────────────────────────────────────┘
        │
        ▼
dashboard/app.py (Streamlit + Plotly)
  가격 비교(지수화) · RSI/MACD · 감성 타임라인 · AI 리포트
```

## 프로젝트 구조

```
trade_dash/
├── .github/workflows/nightly_batch.yml   # 매일 01:00 KST 배치
├── db/schema.sql                         # PostgreSQL 스키마 (참조용)
├── src/
│   ├── config.py        # .env 로드, 기본 종목 매핑 시드
│   ├── models.py        # Pydantic 모델 (LLM Structured Output 스키마)
│   ├── indicators.py    # RSI / SMA / MACD (순수 pandas)
│   ├── collector.py     # 시세·뉴스 수집 (tenacity 재시도)
│   ├── analyzer.py      # Anthropic messages.parse() 호출
│   ├── database.py      # SQLAlchemy 2.0 ORM + 저장소
│   └── main.py          # 배치 엔트리포인트
├── dashboard/app.py     # Streamlit 대시보드
├── requirements.txt
└── .env.example
```

## 빠른 시작

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # ANTHROPIC_API_KEY 입력

python -m src.main                  # 배치 1회 실행 (수집 + 분석 + 저장)
streamlit run dashboard/app.py      # 대시보드
```

## 설정

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_BACKEND` | `auto` | `claude_cli`(헤드리스 CLI) / `api`(SDK) / `auto` |
| `ANTHROPIC_API_KEY` | — | `api` 백엔드에서만 필요 |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | `api` 백엔드 모델 |
| `CLAUDE_CLI_MODEL` | (CLI 기본) | `claude_cli` 백엔드 모델 (예: `sonnet`, `opus`) |
| `DATABASE_URL` | `sqlite:///data/trade_dash.db` | `postgresql+psycopg://...`로 PG 전환 |
| `MAX_NEWS_PER_TICKER` | `5` | 티커당 뉴스 헤드라인 수 |

### LLM 백엔드 두 가지

- **`claude_cli`** — Claude Code 헤드리스 모드(`claude -p --output-format json`)로
  분석. **API 키 불필요** — 로컬에 로그인된 Claude 구독을 그대로 사용합니다.
  CLI에는 스키마 강제 옵션이 없으므로 프롬프트에 JSON 스키마를 포함하고,
  응답을 Pydantic으로 검증해 실패 시 오류를 되먹여 최대 3회 재시도합니다.
- **`api`** — Anthropic SDK `messages.parse()`로 스키마를 API 레벨에서 강제.
  `auto`(기본)는 `ANTHROPIC_API_KEY` 존재 여부로 자동 선택합니다.

```bash
# 로컬에서 API 키 없이 실행 (Claude Code 로그인 상태면 됨)
LLM_BACKEND=claude_cli CLAUDE_CLI_MODEL=sonnet python -m src.main
```

종목 매핑은 `stock_pairs` 테이블에서 관리합니다. 최초 실행 시
`src/config.py`의 `DEFAULT_PAIRS`(삼성전자↔NVDA, SK하이닉스↔MU,
LG엔솔↔TSLA, NAVER↔GOOGL)가 시드됩니다.

## CI/CD (helper 패턴 — GitHub Secrets 불사용)

시크릿은 GitHub에 저장하지 않습니다. **서버(self-hosted runner) 루트의
`.env`에만 존재**하며, 배포/배치 워크플로가 실행 시 워크스페이스로 복사합니다.

| 워크플로 | 트리거 | 러너 | 역할 |
|---|---|---|---|
| `ci.yml` | PR | GitHub-hosted | 문법 체크 + 스모크 테스트 (키 불필요) |
| `deploy.yml` | main push | **self-hosted** | `.env` 복사 → venv → pm2로 대시보드 재시작 |
| `nightly_batch.yml` | 01:00 KST cron | **self-hosted** | 배치 실행 → DB 백업 → 실패 시 Telegram 알림 |

서버 `.env`에 넣을 것: `ANTHROPIC_API_KEY`(또는 `CLAUDE_CODE_OAUTH_TOKEN`),
**워크스페이스 밖 절대경로의 `DATABASE_URL`**, 선택적으로 `TELEGRAM_*`.
상세 절차는 `docs/ops-plan.md` 참고.

크론 `0 16 * * *`(UTC) = 매일 01:00 KST. `workflow_dispatch`로 수동 실행 가능.

## 설계 노트

- **오버나이트 전이 구조**: 분석의 단위는 "미국 직전 세션 마감 → 오늘 한국 세션".
  60일 롤링 오버나이트 상관계수와 베타(`indicators.pair_stats`)를 계산해 프롬프트에
  주입하고, |상관| < 0.3이면 연동 논리를 과신하지 않도록 지시합니다.
- **매크로 컨텍스트**: 원/달러 환율, SOX, VIX를 함께 수집해 판단에 반영합니다.
- **한국어 뉴스**: Google News RSS(API 키 불필요, 최근 7일)에서 종목명 검색으로
  한국어 헤드라인을 수집해 yfinance 영문 뉴스와 병합합니다 — 한국 종목뿐 아니라
  미국 종목의 한국어 보도(예: "엔비디아")도 포함됩니다.
- **방향 확률 + 무효화 조건**: 리포트는 상승/보합/하락 확률(합=1), 강세/약세
  시나리오, 뷰 무효화 트리거를 필수로 포함하며, 대시보드가 예상 방향 vs 실제
  수익률을 대조해 적중률을 누적 표시합니다.

- **Structured Output**: `client.messages.parse(output_format=AnalysisResult)`로
  Pydantic 스키마를 강제 — JSON 파싱/검증 코드가 필요 없고, 스키마 불일치 시
  API 레벨에서 거부됩니다.
- **재시도**: yfinance는 tenacity 지수 백오프(3회), Anthropic SDK는 내장
  재시도(`max_retries=3`, 429/5xx 자동 백오프)를 사용합니다.
- **지표 계산**: ta-lib 네이티브 의존성을 피하기 위해 pandas로 직접 구현
  (Wilder RSI, EMA 기반 MACD).
- **JSONB**: 리포트 전문은 `analysis_reports.report`에 저장 — PostgreSQL은
  JSONB, SQLite는 JSON 텍스트로 자동 대체됩니다.

> ⚠️ 본 시스템의 리포트는 LLM이 생성한 데이터 기반 시나리오이며,
> 투자 자문이 아닙니다.
