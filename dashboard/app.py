"""Streamlit 대시보드 — 주가 비교 / 지표 / 뉴스 감성 / AI 리포트.

실행: streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import (AnalysisReport, DailyMarketData, SessionLocal,
                          StockPair, init_db)
from src.indicators import overnight_frame

# ---------------------------------------------------------------- palette
# 역할 기반 색 토큰 (validate_palette.js 검증 통과 조합)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
KR_COLOR = "#2a78d6"      # 한국 종목 = blue (엔티티 고정)
US_COLOR = "#1baf7a"      # 미국 종목 = aqua (릴리프: 범례 + 직접 라벨)
POS_COLOR = "#2a78d6"     # 감성 + = blue
NEG_COLOR = "#e34948"     # 감성 - = red
NEUTRAL = "#f0efec"
PAIR_COLOR = "#4a3aa7"    # 페어 수준 통계(상관계수) = violet
VIEW_STYLE = {            # 상태색은 아이콘+라벨과 함께만 사용 (색 단독 의미 금지)
    "매수": ("#0ca30c", "▲"),
    "중립": ("#898781", "―"),
    "관망": ("#ec835a", "◆"),
}
# 초보자용 일상어 번역
VIEW_EXPLAIN = {
    "매수": "새로 사는 것을 고려해볼 만하다는 신호예요. 단, 한 번에 다 사지 말고 나눠서 접근하세요.",
    "중립": "이미 갖고 있다면 그대로 유지, 새로 사기에는 근거가 부족하다는 뜻이에요.",
    "관망": "오늘은 사지도 팔지도 말고 지켜보라는 뜻이에요. 큰 이벤트나 불확실성이 앞에 있어요.",
}

LAYOUT_BASE = dict(
    paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
    font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif",
              color=INK, size=13),
    margin=dict(l=48, r=96, t=36, b=40),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)
AXIS_BASE = dict(gridcolor=GRID, linecolor=BASELINE, tickfont=dict(color=INK_MUTED),
                 zeroline=False)

st.set_page_config(page_title="AI 글로벌 주식 분석", page_icon="📈",
                   layout="wide")


# ---------------------------------------------------------------- data access
@st.cache_data(ttl=300)
def load_pairs() -> pd.DataFrame:
    with SessionLocal() as s:
        rows = s.scalars(select(StockPair).where(
            StockPair.is_active.is_(True))).all()
        return pd.DataFrame([{
            "id": p.id, "kr_ticker": p.kr_ticker, "kr_name": p.kr_name,
            "us_ticker": p.us_ticker, "us_name": p.us_name,
            "rationale": p.rationale,
        } for p in rows])


@st.cache_data(ttl=300)
def load_market(ticker: str, days: int) -> pd.DataFrame:
    """최근 days 거래일 로드 (정확히 N행 — 최신부터 역순 조회 후 뒤집기)."""
    with SessionLocal() as s:
        rows = s.scalars(
            select(DailyMarketData)
            .where(DailyMarketData.ticker == ticker)
            .order_by(DailyMarketData.trade_date.desc())
            .limit(days)).all()
        rows = list(reversed(rows))
        return pd.DataFrame([{
            "date": r.trade_date, "close": r.close, "volume": r.volume,
            "rsi_14": r.rsi_14, "sma_20": r.sma_20, "sma_60": r.sma_60,
            "macd": r.macd, "macd_signal": r.macd_signal,
            "macd_hist": r.macd_hist,
        } for r in rows])


@st.cache_data(ttl=300)
def load_reports(pair_id: int) -> pd.DataFrame:
    with SessionLocal() as s:
        rows = s.scalars(
            select(AnalysisReport)
            .where(AnalysisReport.pair_id == pair_id)
            .order_by(AnalysisReport.report_date.desc())).all()
        return pd.DataFrame([{
            "report_date": r.report_date,
            "sentiment_score": r.sentiment_score,
            "technical_signal": r.technical_signal,
            "final_view": r.final_view, "confidence": r.confidence,
            "summary": r.summary, "report": r.report, "model": r.model,
        } for r in rows])


# ---------------------------------------------------------------- charts
def price_comparison_chart(kr: pd.DataFrame, us: pd.DataFrame,
                           kr_name: str, us_name: str) -> go.Figure:
    """통화/스케일이 다르므로 이중축 대신 기간 시작=100 지수화 (단일 축)."""
    fig = go.Figure()
    for df, name, color in ((kr, kr_name, KR_COLOR), (us, us_name, US_COLOR)):
        if df.empty:
            continue
        indexed = df["close"] / df["close"].iloc[0] * 100
        fig.add_trace(go.Scatter(
            x=df["date"], y=indexed, name=name,
            mode="lines", line=dict(color=color, width=2),
            hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>"))
        # 직접 라벨 (릴리프 규칙 — 저대비 색 보조 인코딩)
        fig.add_annotation(x=df["date"].iloc[-1], y=indexed.iloc[-1],
                           text=f" {name}", showarrow=False,
                           xanchor="left", font=dict(color=color, size=12))
    fig.add_hline(y=100, line=dict(color=BASELINE, width=1, dash="dot"))
    fig.update_layout(**LAYOUT_BASE, height=360,
                      title=dict(text="가격 비교 (기간 시작 = 100)", font=dict(size=15)))
    fig.update_xaxes(**AXIS_BASE)
    fig.update_yaxes(**AXIS_BASE, title=dict(text="지수", font=dict(color=INK_MUTED)))
    return fig


def indicator_chart(df: pd.DataFrame, name: str, color: str) -> go.Figure:
    """가격+SMA(상단) / RSI(중단) / MACD(하단) — 공통 x축 서브플롯.
    골든/데드 크로스를 말이 아니라 눈으로 확인하기 위한 SMA 오버레이."""
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        vertical_spacing=0.07, row_heights=[0.42, 0.29, 0.29],
                        subplot_titles=("종가와 평균 가격선 (20일/60일)",
                                        "과열/침체 온도계 — RSI",
                                        "추세의 힘 — MACD"))
    fig.add_trace(go.Scatter(x=df["date"], y=df["close"], name="종가",
                             line=dict(color=color, width=2),
                             hovertemplate="종가 %{y:,.2f}<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["sma_20"], name="SMA20",
                             line=dict(color=INK_MUTED, width=2, dash="dash"),
                             hovertemplate="SMA20 %{y:,.2f}<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["sma_60"], name="SMA60",
                             line=dict(color=BASELINE, width=2, dash="dot"),
                             hovertemplate="SMA60 %{y:,.2f}<extra></extra>"),
                  row=1, col=1)

    fig.add_trace(go.Scatter(x=df["date"], y=df["rsi_14"], name="RSI",
                             line=dict(color=color, width=2), showlegend=False,
                             hovertemplate="RSI %{y:.1f}<extra></extra>"),
                  row=2, col=1)
    for level in (70, 30):
        fig.add_hline(y=level, row=2, col=1,
                      line=dict(color=BASELINE, width=1, dash="dot"))

    hist_colors = [POS_COLOR if (v or 0) >= 0 else NEG_COLOR
                   for v in df["macd_hist"]]
    fig.add_trace(go.Bar(x=df["date"], y=df["macd_hist"], name="히스토그램",
                         marker=dict(color=hist_colors),
                         hovertemplate="Hist %{y:.3f}<extra></extra>"),
                  row=3, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["macd"], name="MACD",
                             line=dict(color=INK, width=2),
                             hovertemplate="MACD %{y:.3f}<extra></extra>"),
                  row=3, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["macd_signal"], name="시그널",
                             line=dict(color=INK_MUTED, width=2, dash="dot"),
                             hovertemplate="Signal %{y:.3f}<extra></extra>"),
                  row=3, col=1)
    fig.update_layout(**LAYOUT_BASE, height=640, bargap=0.4,
                      title=dict(text=f"{name} 기술적 지표", font=dict(size=15)))
    fig.update_xaxes(**AXIS_BASE)
    fig.update_yaxes(**AXIS_BASE)
    return fig


def _close_series(df: pd.DataFrame) -> pd.Series:
    return pd.Series(df["close"].values, index=pd.to_datetime(df["date"]))


def correlation_chart(kr: pd.DataFrame, us: pd.DataFrame) -> go.Figure | None:
    """60일 롤링 오버나이트 상관계수 — 페어 연동이 살아있는지 검증."""
    frame = overnight_frame(_close_series(kr), _close_series(us))
    if len(frame) < 40:
        return None
    window = min(60, max(20, len(frame) - 10))
    roll = frame["kr_ret"].rolling(window).corr(frame["us_ret"])
    fig = go.Figure(go.Scatter(
        x=frame["date"], y=roll, name=f"{window}일 상관",
        line=dict(color=PAIR_COLOR, width=2),
        hovertemplate="%{x|%Y-%m-%d}<br>상관 %{y:.2f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=BASELINE, width=1))
    for guide in (0.3, -0.3):
        fig.add_hline(y=guide, line=dict(color=GRID, width=1, dash="dot"))
    fig.update_layout(**LAYOUT_BASE, height=300, showlegend=False,
                      title=dict(text=f"미국을 따라가는 정도 (최근 {window}일 상관계수)",
                                 font=dict(size=15)))
    fig.update_xaxes(**AXIS_BASE)
    fig.update_yaxes(**AXIS_BASE, range=[-1, 1])
    return fig


def leadlag_scatter(kr: pd.DataFrame, us: pd.DataFrame,
                    kr_name: str, us_name: str) -> go.Figure | None:
    """미국 직전 세션 수익률(x) → 한국 당일 수익률(y) 산점도 + 회귀선.
    페어 논리의 실증 증거 — 기울기 = 오버나이트 베타."""
    frame = overnight_frame(_close_series(kr), _close_series(us)).tail(60)
    if len(frame) < 20:
        return None
    x, y = frame["us_ret"] * 100, frame["kr_ret"] * 100
    beta, intercept = pd.Series(y).cov(x) / x.var(), 0.0
    intercept = y.mean() - beta * x.mean()
    xs = pd.Series([x.min(), x.max()])
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers", name="일별 관측",
        marker=dict(color=KR_COLOR, size=8, opacity=0.55,
                    line=dict(color=SURFACE, width=1)),
        customdata=frame["date"].dt.strftime("%Y-%m-%d"),
        hovertemplate="%{customdata}<br>미국 %{x:+.2f}% → 한국 %{y:+.2f}%"
                      "<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=xs, y=beta * xs + intercept, mode="lines",
        name=f"회귀선 (β={beta:.2f})", line=dict(color=INK, width=2)))
    fig.add_hline(y=0, line=dict(color=BASELINE, width=1))
    fig.add_vline(x=0, line=dict(color=BASELINE, width=1))
    fig.update_layout(**LAYOUT_BASE, height=300,
                      title=dict(text="미국이 움직인 다음 날, 한국은? (최근 60거래일)",
                                 font=dict(size=15)))
    fig.update_xaxes(**AXIS_BASE,
                     title=dict(text=f"{us_name} 직전 세션 수익률(%)",
                                font=dict(color=INK_MUTED, size=12)))
    fig.update_yaxes(**AXIS_BASE,
                     title=dict(text=f"{kr_name} 당일 수익률(%)",
                                font=dict(color=INK_MUTED, size=12)))
    return fig


def direction_prob_bar(probs: dict) -> go.Figure:
    """상승/보합/하락 확률 — 단일 수평 스택 바."""
    fig = go.Figure()
    for key, label, color in (("up", "상승", POS_COLOR),
                              ("flat", "보합", BASELINE),
                              ("down", "하락", NEG_COLOR)):
        v = float(probs.get(key, 0))
        fig.add_trace(go.Bar(
            x=[v], y=["오늘"], orientation="h", name=label,
            marker=dict(color=color, line=dict(color=SURFACE, width=2)),
            text=f"{label} {v:.0%}", textposition="inside",
            insidetextfont=dict(color="#ffffff", size=13),
            hovertemplate=f"{label} {v:.0%}<extra></extra>"))
    fig.update_layout(**{**LAYOUT_BASE, "margin": dict(l=8, r=8, t=8, b=8)},
                      barmode="stack", height=64, showlegend=False)
    fig.update_xaxes(visible=False, range=[0, 1])
    fig.update_yaxes(visible=False)
    return fig


def compute_hits(reports: pd.DataFrame, kr: pd.DataFrame) -> pd.DataFrame:
    """예상 방향 vs 실제 당일 수익률 대조 (expected_direction 있는 리포트만)."""
    closes = pd.Series(kr["close"].values, index=list(kr["date"]))
    rows = []
    for r in reports.itertuples():
        expected = (r.report or {}).get("expected_direction")
        if not expected or r.report_date not in closes.index:
            continue
        pos = list(closes.index).index(r.report_date)
        if pos == 0:
            continue
        ret = (closes.iloc[pos] / closes.iloc[pos - 1] - 1) * 100
        actual = "상승" if ret > 0.5 else "하락" if ret < -0.5 else "보합"
        rows.append({"기준일": r.report_date, "예상": expected, "실제": actual,
                     "실제수익률(%)": round(ret, 2), "적중": expected == actual})
    return pd.DataFrame(rows)


def sentiment_timeline(reports: pd.DataFrame) -> go.Figure:
    """일자별 뉴스 감성 점수 — 다이버징(blue + / red −), 중립 0 기준선."""
    df = reports.sort_values("report_date")
    colors = [POS_COLOR if v >= 0.05 else NEG_COLOR if v <= -0.05 else BASELINE
              for v in df["sentiment_score"]]
    fig = go.Figure(go.Bar(
        x=df["report_date"], y=df["sentiment_score"],
        marker=dict(color=colors), name="감성 점수",
        hovertemplate="%{x|%Y-%m-%d}<br>감성 %{y:.2f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=BASELINE, width=1))
    fig.update_layout(**LAYOUT_BASE, height=280, bargap=0.5, showlegend=False,
                      title=dict(text="뉴스 분위기 흐름 (-1 나쁨 ~ +1 좋음)",
                                 font=dict(size=15)))
    fig.update_xaxes(**AXIS_BASE)
    fig.update_yaxes(**AXIS_BASE, range=[-1.05, 1.05])
    return fig


def confidence_text(conf: float) -> str:
    if conf >= 0.7:
        return f"{conf:.0%} — 여러 근거가 같은 방향을 가리키는, 비교적 뚜렷한 신호예요."
    if conf >= 0.5:
        return f"{conf:.0%} — 참고할 만하지만 확신은 약한 수준이에요."
    return (f"{conf:.0%} — 동전 던지기보다 조금 나은 정도예요. "
            "이런 날은 거래하지 않는 것도 훌륭한 전략입니다.")


def veteran_tips(latest, rsi: float | None) -> list[str]:
    """상황별 투자 원칙 (룰 기반) — 최대 3개."""
    tips: list[str] = []
    if latest.confidence < 0.5:
        tips.append("확신이 낮은 날 억지로 매매하면 수수료와 실수만 쌓여요. "
                    "**거래하지 않는 것도 투자입니다.**")
    if abs(latest.sentiment_score) >= 0.4:
        tips.append("뉴스가 뜨거울 때 따라 사는 '추격 매수'는 초보자가 돈을 잃는 "
                    "가장 흔한 경로예요. 뉴스는 이미 가격에 반영됐을 가능성이 큽니다.")
    if rsi is not None:
        if rsi >= 70:
            tips.append("지금은 단기간에 많이 오른 '과열' 구간이에요. "
                        "새로 사기보다는 조정을 기다리는 편이 유리한 경우가 많아요.")
        elif rsi <= 30:
            tips.append("많이 떨어진 '침체' 구간이지만, 떨어지는 칼날을 잡지 마세요. "
                        "하락이 멈춘 것을 확인한 뒤 나눠 사는 게 안전해요.")
    tips.append("어떤 신호가 와도 **한 종목에 전 재산을 넣지 마세요.** "
                "이 리포트는 참고 자료일 뿐, 손실은 본인 책임입니다.")
    return tips[:3]


def view_badge(view: str, confidence: float) -> str:
    color, icon = VIEW_STYLE.get(view, (INK_MUTED, "•"))
    return (f'<span style="background:{color};color:#fff;padding:4px 14px;'
            f'border-radius:14px;font-weight:700;font-size:1.05rem;">'
            f'{icon} {view}</span>'
            f'<span style="color:{INK_MUTED};margin-left:10px;">'
            f'확신도 {confidence:.0%}</span>')


# ---------------------------------------------------------------- page
def main() -> None:
    init_db()
    st.title("📈 AI 기반 글로벌 주식 분석 대시보드")

    pairs = load_pairs()
    if pairs.empty:
        st.warning("등록된 종목 매핑이 없습니다. 배치를 먼저 실행하세요: "
                   "`python -m src.main`")
        return

    with st.sidebar:
        st.header("설정")
        label_map = {f"{r.kr_name} ↔ {r.us_name}": r for r in
                     pairs.itertuples()}
        selected = st.selectbox("분석 페어", list(label_map))
        days = st.select_slider("조회 기간(거래일)", options=[10, 20, 30], value=30)
        pair = label_map[selected]
        st.caption(f"연동 근거 — {pair.rationale}")

    kr = load_market(pair.kr_ticker, days)
    us = load_market(pair.us_ticker, days)
    reports = load_reports(pair.id)

    if kr.empty and us.empty:
        st.info("시세 데이터가 없습니다. 야간 배치 실행 후 다시 확인하세요.")
        return

    # 연동 검증용 장기 데이터 (표시 기간과 무관하게 6개월)
    kr_long = load_market(pair.kr_ticker, 140)
    us_long = load_market(pair.us_ticker, 140)
    hits = compute_hits(reports, kr_long) if not reports.empty else pd.DataFrame()

    # ---- 오늘의 한눈 요약 (초보자 우선: 결론부터)
    latest = reports.iloc[0] if not reports.empty else None
    if latest is not None:
        body_latest = latest.report
        with st.container(border=True):
            st.markdown(f"### 오늘의 결론 — {pair.kr_name}")
            st.markdown(view_badge(latest.final_view, latest.confidence),
                        unsafe_allow_html=True)
            st.markdown(f"**{VIEW_EXPLAIN.get(latest.final_view, '')}**")
            note = body_latest.get("beginner_note")
            if note:
                st.info(f"💬 **오늘의 한마디** — {note}")
            st.caption(f"AI 확신도: {confidence_text(latest.confidence)}")

            # 손실 감각: 하루 변동폭 + 6개월 가격 위치
            if not kr.empty and len(kr) >= 21:
                vol = float(kr["close"].pct_change().tail(20).std() * 100)
                col_v, col_p = st.columns(2)
                with col_v:
                    st.markdown(
                        f"**하루 변동폭** — 이 종목은 최근 한 달간 하루 평균 "
                        f"약 **±{vol:.1f}%** 움직였어요. 100만원을 투자하면 "
                        f"하루에 ±{vol:.1f}만원 정도 오르내릴 수 있다는 뜻이에요.")
                with col_p:
                    lo = float(kr_long["close"].min())
                    hi = float(kr_long["close"].max())
                    cur = float(kr["close"].iloc[-1])
                    pos = (cur - lo) / (hi - lo) if hi > lo else 0.5
                    st.markdown(
                        f"**지금 가격 위치** — 최근 6개월 최저 {lo:,.0f} ~ "
                        f"최고 {hi:,.0f} 범위에서 현재 **{pos:.0%} 지점**이에요. "
                        f"{'높은 편이니 신중하게.' if pos >= 0.7 else '낮은 편이에요.' if pos <= 0.3 else '중간쯤이에요.'}")
                    st.progress(min(max(pos, 0.0), 1.0))

            rsi_now = (float(kr["rsi_14"].iloc[-1])
                       if not kr.empty and pd.notna(kr["rsi_14"].iloc[-1]) else None)
            for tip in veteran_tips(latest, rsi_now):
                st.markdown(f"- {tip}")

    # ---- KPI 타일
    c1, c2, c3, c4, c5 = st.columns(5)
    if not kr.empty and len(kr) >= 2:
        chg = (kr["close"].iloc[-1] / kr["close"].iloc[-2] - 1) * 100
        c1.metric(f"{pair.kr_name} 종가", f"{kr['close'].iloc[-1]:,.0f}",
                  f"{chg:+.2f}%")
    if not us.empty and len(us) >= 2:
        chg = (us["close"].iloc[-1] / us["close"].iloc[-2] - 1) * 100
        c2.metric(f"{pair.us_name} 종가", f"${us['close'].iloc[-1]:,.2f}",
                  f"{chg:+.2f}%")
    if latest is not None:
        senti = latest.sentiment_score
        mood = "긍정적" if senti >= 0.2 else "부정적" if senti <= -0.2 else "중립적"
        c3.metric("뉴스 분위기", mood, f"{senti:+.2f}",
                  help="최근 뉴스가 이 종목에 얼마나 우호적인지 AI가 매긴 점수예요. "
                       "-1(매우 나쁨) ~ +1(매우 좋음). 단, 뉴스가 좋다고 주가가 "
                       "꼭 오르는 건 아니에요 — 이미 가격에 반영됐을 수 있거든요.")
        stats_kpi = (latest.report or {}).get("pair_stats") or {}
        corr = stats_kpi.get("corr_60d")
        if corr is not None:
            link = "강함" if abs(corr) >= 0.5 else "보통" if abs(corr) >= 0.3 else "약함"
            c4.metric("미국 연동 정도", link, f"상관 {corr:+.2f}",
                      delta_color="off",
                      help="이 한국 종목이 짝지어진 미국 종목을 얼마나 따라 "
                           "움직이는지예요 (최근 60일 기준, 1에 가까울수록 강함). "
                           "'약함'이면 미국이 올라도 한국이 따라 오르지 않을 수 "
                           "있다는 뜻이에요.")
    if not hits.empty:
        rate = hits["적중"].mean()
        c5.metric("AI 적중률", f"{rate:.0%}", f"{len(hits)}회 평가",
                  delta_color="off",
                  help="AI가 예측한 방향(상승/보합/하락)이 실제와 맞은 비율이에요. "
                       "이 숫자가 낮다면 AI 의견을 더 보수적으로 참고하세요.")
    else:
        c5.metric("AI 적중률", "—", "평가 이력 없음", delta_color="off",
                  help="AI 예측이 실제와 맞았는지 매일 기록해 누적하는 지표예요. "
                       "이력이 쌓이면 표시됩니다.")

    st.divider()

    # ---- 가격 비교 차트
    st.plotly_chart(price_comparison_chart(kr, us, pair.kr_name, pair.us_name),
                    use_container_width=True)
    st.caption("💡 읽는 법: 두 종목의 출발점을 100으로 맞춰 '누가 더 올랐나'를 "
               "비교해요. 원화/달러처럼 단위가 달라도 공정하게 비교할 수 있어요. "
               "선이 100 위면 기간 시작보다 오른 것, 아래면 내린 거예요.")

    # ---- 페어 연동 검증 (컨설팅 반영: 상관이 죽은 페어는 논리 자체를 의심)
    st.subheader("🔗 정말 미국을 따라 움직일까? — 연동 검증")
    col_l, col_r = st.columns(2)
    corr_fig = correlation_chart(kr_long, us_long)
    scatter_fig = leadlag_scatter(kr_long, us_long, pair.kr_name, pair.us_name)
    with col_l:
        if corr_fig is not None:
            st.plotly_chart(corr_fig, use_container_width=True)
        else:
            st.info("상관 계산에 필요한 표본이 부족합니다.")
    with col_r:
        if scatter_fig is not None:
            st.plotly_chart(scatter_fig, use_container_width=True)
    st.caption("💡 읽는 법 — 왼쪽: '한국 종목이 미국 종목을 얼마나 따라 움직이나'를 "
               "숫자로 나타낸 선이에요. 1에 가까울수록 잘 따라가고, 0이면 따로 놀아요. "
               "점선(±0.3) 안쪽이면 '연동이 약하다'는 뜻 — 미국이 올랐다고 한국 종목을 "
               "사는 근거로 삼기 어려워요. / 오른쪽: 점 하나가 하루예요. 점들이 "
               "오른쪽 위로 모이면 '미국이 오른 다음 날 한국도 올랐다'가 실제로 "
               "있었다는 증거예요.")

    # ---- 기술적 지표 (탭)
    tab_kr, tab_us = st.tabs([f"🇰🇷 {pair.kr_name}", f"🇺🇸 {pair.us_name}"])
    with tab_kr:
        if not kr.empty:
            st.plotly_chart(indicator_chart(kr, pair.kr_name, KR_COLOR),
                            use_container_width=True)
    with tab_us:
        if not us.empty:
            st.plotly_chart(indicator_chart(us, pair.us_name, US_COLOR),
                            use_container_width=True)
    with st.expander("💡 위 차트 읽는 법 (처음이라면 꼭 읽어보세요)"):
        st.markdown("""
- **평균 가격선(20일/60일)** — 최근 20일/60일 동안의 평균 가격이에요.
  지금 가격이 평균선 **위**에 있으면 상승 흐름, **아래**면 하락 흐름으로 봐요.
  짧은 선(20일)이 긴 선(60일)을 아래에서 위로 뚫으면 '골든크로스'라고 부르는
  상승 전환 신호, 반대는 '데드크로스'예요.
- **과열/침체 온도계(RSI)** — 최근 상승/하락의 강도를 0~100으로 나타내요.
  **70 위**면 단기간에 너무 올라 쉬어갈 수 있는 '과열', **30 아래**면 너무 떨어진
  '침체' 상태예요. 과열에서 사면 상투를 잡기 쉬워요.
- **추세의 힘(MACD)** — 막대가 **0 위에서 커지면** 상승 힘이 세지는 중,
  **0 아래로 내려가면** 하락 힘이 세지는 중이에요. 막대의 방향이 바뀌는 순간이
  추세 전환의 힌트가 돼요.
- ⚠️ 어떤 지표도 혼자서는 못 믿어요. 여러 신호가 **같은 방향**을 가리킬 때만
  의미가 커집니다.
""")

    # ---- 감성 타임라인 + AI 리포트
    if reports.empty:
        st.info("AI 분석 리포트가 아직 없습니다.")
        return

    st.plotly_chart(sentiment_timeline(reports), use_container_width=True)
    st.caption("💡 읽는 법: 매일의 뉴스 분위기를 점수로 기록한 그래프예요. "
               "파란 막대(위)는 좋은 뉴스가 많았던 날, 빨간 막대(아래)는 나쁜 뉴스가 "
               "많았던 날이에요. 분위기가 좋다고 바로 사기보다, 좋은 분위기가 "
               "'이어지는지'를 보는 게 중요해요.")

    st.subheader(f"🤖 AI 장전 브리핑 — {latest.report_date}")
    st.markdown(f"**{latest.summary}**")
    body = latest.report

    # 방향 확률 (신 스키마 리포트만)
    probs = body.get("direction_probabilities")
    if probs:
        st.plotly_chart(direction_prob_bar(probs), use_container_width=True,
                        config={"displayModeBar": False})
        stats = body.get("pair_stats") or {}
        if stats.get("corr_60d") is not None:
            st.caption(f"분석 시점 연동 통계 — 60일 상관 {stats['corr_60d']:+.2f}, "
                       f"오버나이트 베타 {stats.get('beta_overnight', 0):+.2f} "
                       f"(관측치 {stats.get('n_obs')}건)")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### 미국 종목 흐름")
        st.write(body.get("us_market_summary", ""))
        st.markdown("##### 한국 종목 영향 추론")
        st.write(body.get("expected_impact_on_kr", ""))
    with col_b:
        st.markdown("##### 종합 판단 근거")
        st.write(body.get("rationale", ""))

    if body.get("bull_case") or body.get("bear_case"):
        col_bull, col_bear = st.columns(2)
        with col_bull:
            st.success(f"**강세 시나리오** — {body.get('bull_case', '')}")
        with col_bear:
            st.error(f"**약세 시나리오** — {body.get('bear_case', '')}")
    if body.get("invalidation_trigger"):
        st.warning(f"**뷰 무효화 조건** — {body['invalidation_trigger']}")

    if not hits.empty:
        with st.expander("뷰 적중 이력"):
            st.dataframe(hits, use_container_width=True, hide_index=True)

    with st.expander("헤드라인별 감성 상세"):
        news = pd.DataFrame(body.get("news_sentiments", []))
        if not news.empty:
            st.dataframe(news, use_container_width=True, hide_index=True)

    # 접근성 릴리프: 차트와 동일 데이터의 테이블 뷰
    with st.expander("리포트 이력 (테이블 뷰)"):
        st.dataframe(
            reports.drop(columns=["report"]).rename(columns={
                "report_date": "기준일", "sentiment_score": "감성",
                "technical_signal": "기술신호", "final_view": "최종뷰",
                "confidence": "확신도", "summary": "요약", "model": "모델",
            }),
            use_container_width=True, hide_index=True)

    # ---- 용어 사전 (초보자용)
    with st.expander("📚 용어 사전 — 어려운 말 쉽게 풀기"):
        st.markdown("""
| 용어 | 쉬운 설명 |
|---|---|
| **관망** | 사지도 팔지도 말고 지켜보기 |
| **중립** | 갖고 있으면 유지, 새로 사기엔 근거 부족 |
| **RSI** | 최근 얼마나 과하게 올랐나/떨어졌나를 0~100으로 나타낸 온도계. 70↑ 과열, 30↓ 침체 |
| **평균 가격선(SMA)** | 최근 N일 평균 가격을 이은 선. 현재가와 비교해 흐름을 판단 |
| **MACD** | 상승/하락 추세의 '힘'이 세지는지 약해지는지 보는 지표 |
| **상관계수** | 두 종목이 같이 움직이는 정도. 1=완전히 같이, 0=따로, -1=반대로 |
| **베타** | 미국 종목이 1% 움직일 때 한국 종목이 평균 몇 % 움직이는지 |
| **갭** | 어제 종가와 오늘 시작 가격의 차이. 밤사이 미국 시장 영향이 여기에 반영돼요 |
| **확신도** | AI가 자기 판단을 얼마나 자신하는지. 50%면 반반이라는 뜻 |
| **뷰 무효화 조건** | "이 가격을 벗어나면 내 판단이 틀린 것" — 미리 정해두는 손절/재검토 기준 |
""")

    st.caption("본 리포트는 AI가 생성한 데이터 기반 시나리오이며 투자 자문이 "
               "아닙니다. 투자 판단과 손실 책임은 본인에게 있습니다.")


if __name__ == "__main__":
    main()
