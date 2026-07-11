"""CI 스모크 테스트 — 네트워크/LLM 없이 핵심 로직만 검증한다.

1. 오버나이트 정렬·통계: 합성 데이터에 심어둔 베타를 회수하는지
2. DB 업서트 멱등성: 같은 데이터를 두 번 넣어도 중복이 없는지
"""
from __future__ import annotations

import importlib
from datetime import date

import numpy as np
import pandas as pd


def test_indicators_enrich_no_nan_in_tail():
    from src.indicators import enrich_with_indicators

    np.random.seed(1)
    idx = pd.date_range("2026-01-01", periods=120, freq="B")
    close = pd.Series(100 + np.random.randn(120).cumsum(), index=idx)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": 1_000_000})
    out = enrich_with_indicators(df).tail(30)
    cols = ["rsi_14", "sma_20", "sma_60", "macd", "macd_signal", "macd_hist"]
    assert out[cols].notna().all().all()
    assert out["rsi_14"].between(0, 100).all()


def test_pair_stats_recovers_planted_beta():
    from src.indicators import pair_stats

    np.random.seed(0)
    us_idx = pd.date_range("2026-01-01", periods=120, freq="B")
    kr_idx = us_idx + pd.Timedelta(days=1)
    us = pd.Series(100 + np.random.randn(120).cumsum(), index=us_idx)
    us_ret = us.pct_change().fillna(0)
    # KR(t) = 0.6 × US(t-1) + 노이즈 → 베타 0.6 회수 기대
    kr = pd.Series((1 + 0.6 * us_ret.values
                    + np.random.randn(120) * 0.002).cumprod() * 70000,
                   index=kr_idx)
    stats = pair_stats(kr, us)
    assert stats["n_obs"] >= 20
    assert 0.4 < stats["beta_overnight"] < 0.8
    assert stats["corr_60d"] > 0.5


def test_db_upsert_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    import src.config, src.database  # noqa: E401
    importlib.reload(src.config)
    db = importlib.reload(src.database)

    db.init_db()
    idx = pd.date_range("2026-07-01", periods=3, freq="B")
    df = pd.DataFrame({
        "open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3],
        "close": [1.0, 2.0, 3.0], "volume": [10, 20, 30],
        "rsi_14": [50, 51, 52], "sma_20": [1, 1, 1], "sma_60": [1, 1, 1],
        "macd": [0, 0, 0], "macd_signal": [0, 0, 0], "macd_hist": [0, 0, 0],
    }, index=idx)
    fake_report = {
        "overall_sentiment_score": 0.4, "technical_signal": "bullish",
        "final_view": "매수", "confidence": 0.7, "summary": "테스트",
    }

    from sqlalchemy import func, select
    with db.SessionLocal() as s:
        pairs = db.get_active_pairs(s)
        assert len(pairs) > 0  # 기본 매핑 시드 확인
        for _ in range(2):     # 두 번 실행 → 멱등이어야 함
            db.upsert_market_data(s, "TEST.KS", df)
            db.save_report(s, pairs[0].id, date(2026, 7, 9), fake_report, "m")
            s.commit()
        assert s.scalar(select(func.count()).select_from(db.DailyMarketData)) == 3
        assert s.scalar(select(func.count()).select_from(db.AnalysisReport)) == 1


def test_upsert_skips_nan_close(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nan.db'}")
    import src.config, src.database  # noqa: E401
    importlib.reload(src.config)
    db = importlib.reload(src.database)

    db.init_db()
    idx = pd.date_range("2026-07-01", periods=2, freq="B")
    df = pd.DataFrame({"close": [1.0, float("nan")]}, index=idx)
    from sqlalchemy import func, select
    with db.SessionLocal() as s:
        db.upsert_market_data(s, "NAN.KS", df)
        s.commit()
        assert s.scalar(select(func.count()).select_from(db.DailyMarketData)) == 1
