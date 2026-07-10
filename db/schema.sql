-- =============================================================
-- AI 기반 글로벌 주식 분석 시스템 - PostgreSQL Schema
-- (SQLite 사용 시 SQLAlchemy가 동일 구조를 자동 생성하며,
--  JSONB는 JSON 텍스트 컬럼으로 대체됩니다)
-- =============================================================

-- 1) 한국 종목 <-> 미국 선행 종목 매핑
CREATE TABLE IF NOT EXISTS stock_pairs (
    id          SERIAL PRIMARY KEY,
    kr_ticker   VARCHAR(20)  NOT NULL,              -- 예: 005930.KS
    kr_name     VARCHAR(100) NOT NULL,              -- 예: 삼성전자
    us_ticker   VARCHAR(20)  NOT NULL,              -- 예: NVDA
    us_name     VARCHAR(100) NOT NULL,              -- 예: 엔비디아
    rationale   TEXT,                               -- 매핑 근거 (공급망/수요 연동 등)
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_pair UNIQUE (kr_ticker, us_ticker)
);

-- 2) 일별 시세 + 기술적 지표 (한국/미국 티커 공용)
CREATE TABLE IF NOT EXISTS daily_market_data (
    id           BIGSERIAL PRIMARY KEY,
    ticker       VARCHAR(20)   NOT NULL,
    trade_date   DATE          NOT NULL,
    open         NUMERIC(18,4),
    high         NUMERIC(18,4),
    low          NUMERIC(18,4),
    close        NUMERIC(18,4) NOT NULL,
    volume       BIGINT,
    rsi_14       NUMERIC(8,4),
    sma_20       NUMERIC(18,4),
    sma_60       NUMERIC(18,4),
    macd         NUMERIC(18,6),
    macd_signal  NUMERIC(18,6),
    macd_hist    NUMERIC(18,6),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_ticker_date UNIQUE (ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_market_ticker_date
    ON daily_market_data (ticker, trade_date DESC);

-- 3) AI 분석 리포트 (구조화 결과 전문은 JSONB로 보존)
CREATE TABLE IF NOT EXISTS analysis_reports (
    id               BIGSERIAL PRIMARY KEY,
    pair_id          INTEGER      NOT NULL REFERENCES stock_pairs(id) ON DELETE CASCADE,
    report_date      DATE         NOT NULL,          -- 분석 기준일 (KST)
    sentiment_score  NUMERIC(4,3) NOT NULL,          -- -1.000 ~ +1.000
    technical_signal VARCHAR(10)  NOT NULL,          -- bullish / neutral / bearish
    final_view       VARCHAR(10)  NOT NULL,          -- 매수 / 중립 / 관망
    confidence       NUMERIC(4,3) NOT NULL,          -- 0.000 ~ 1.000
    summary          TEXT,                           -- 한 줄 요약
    report           JSONB        NOT NULL,          -- AnalysisResult 전체 (뉴스별 감성 포함)
    model            VARCHAR(60),                    -- 사용한 LLM 모델 ID
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_pair_report_date UNIQUE (pair_id, report_date)
);

CREATE INDEX IF NOT EXISTS ix_reports_pair_date
    ON analysis_reports (pair_id, report_date DESC);
