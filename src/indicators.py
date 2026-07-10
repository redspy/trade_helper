"""기술적 지표 계산 — 순수 pandas 구현.

ta-lib은 네이티브 빌드가 필요해 CI에서 깨지기 쉬우므로,
RSI / SMA / MACD를 표준 정의대로 직접 계산한다.
"""
from __future__ import annotations

import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (EWM alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    out = 100 - 100 / (1 + rs)
    # 하락이 전혀 없는 구간은 RSI=100 (워밍업 NaN은 그대로 유지)
    return out.mask(avg_loss.eq(0) & avg_gain.notna(), 100.0)


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD(12, 26, 9) — columns: macd / macd_signal / macd_hist."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": macd_line - signal_line,
    })


def overnight_frame(kr_close: pd.Series, us_close: pd.Series) -> pd.DataFrame:
    """한국 수익률(t)과 '직전' 미국 세션 수익률을 날짜로 정렬한 프레임.

    미국 세션은 한국 거래일 t의 새벽(KST)에 마감하므로, 한국 t일 수익률의
    선행 변수는 t보다 엄격히 이전 날짜의 미국 수익률이다 (merge_asof backward,
    allow_exact_matches=False).
    columns: date / kr_ret / us_ret
    """
    def _ret(s: pd.Series) -> pd.DataFrame:
        s = s.copy()
        s.index = pd.to_datetime(s.index)
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        out = s.sort_index().pct_change().dropna().rename("ret").reset_index()
        out.columns = ["date", "ret"]
        return out

    kr = _ret(kr_close).rename(columns={"ret": "kr_ret"})
    us = _ret(us_close).rename(columns={"ret": "us_ret"})
    merged = pd.merge_asof(kr, us, on="date", direction="backward",
                           allow_exact_matches=False)
    return merged.dropna()


def pair_stats(kr_close: pd.Series, us_close: pd.Series,
               window: int = 60) -> dict:
    """오버나이트 연동 통계 — 60일 상관계수 / 베타 / 관측치 수."""
    frame = overnight_frame(kr_close, us_close).tail(window)
    if len(frame) < 20:
        return {"corr_60d": None, "beta_overnight": None, "n_obs": len(frame)}
    var = frame["us_ret"].var()
    return {
        "corr_60d": round(float(frame["kr_ret"].corr(frame["us_ret"])), 3),
        "beta_overnight": (round(float(frame["kr_ret"].cov(frame["us_ret"]) / var), 3)
                           if var else None),
        "n_obs": int(len(frame)),
    }


def enrich_with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV DataFrame(index=date, columns=open/high/low/close/volume)에
    rsi_14 / sma_20 / sma_60 / macd 계열 컬럼을 추가해 반환."""
    out = df.copy()
    out["rsi_14"] = rsi(out["close"], 14)
    out["sma_20"] = sma(out["close"], 20)
    out["sma_60"] = sma(out["close"], 60)
    out = pd.concat([out, macd(out["close"])], axis=1)
    return out
