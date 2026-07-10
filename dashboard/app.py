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
                        subplot_titles=("종가 + SMA20/60", "RSI (14)",
                                        "MACD (12, 26, 9)"))
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
                      title=dict(text=f"롤링 오버나이트 상관계수 ({window}일)",
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
                      title=dict(text="오버나이트 전이 (최근 60거래일)",
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
                      title=dict(text="뉴스 감성 타임라인 (-1 ~ +1)",
                                 font=dict(size=15)))
    fig.update_xaxes(**AXIS_BASE)
    fig.update_yaxes(**AXIS_BASE, range=[-1.05, 1.05])
    return fig


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

    # ---- KPI 타일 + 최신 AI 뷰
    latest = reports.iloc[0] if not reports.empty else None
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
        c3.metric("뉴스 감성", f"{latest.sentiment_score:+.2f}",
                  latest.technical_signal)
        with c4:
            st.caption(f"AI 최종 뷰 · {latest.report_date}")
            st.markdown(view_badge(latest.final_view, latest.confidence),
                        unsafe_allow_html=True)
    if not hits.empty:
        rate = hits["적중"].mean()
        c5.metric("뷰 적중률", f"{rate:.0%}", f"{len(hits)}회 평가")
    else:
        c5.metric("뷰 적중률", "—", "평가 이력 없음")

    st.divider()

    # ---- 가격 비교 차트
    st.plotly_chart(price_comparison_chart(kr, us, pair.kr_name, pair.us_name),
                    use_container_width=True)

    # ---- 페어 연동 검증 (컨설팅 반영: 상관이 죽은 페어는 논리 자체를 의심)
    st.subheader("🔗 페어 연동 검증")
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
    st.caption("점선(±0.3) 아래의 상관은 '약한 연동' — 이 구간에서는 미국 종목 "
               "재료를 근거로 한 방향 베팅의 신뢰도가 낮습니다.")

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

    # ---- 감성 타임라인 + AI 리포트
    if reports.empty:
        st.info("AI 분석 리포트가 아직 없습니다.")
        return

    st.plotly_chart(sentiment_timeline(reports), use_container_width=True)

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

    st.caption("본 리포트는 LLM이 생성한 데이터 기반 시나리오이며 "
               "투자 자문이 아닙니다.")


if __name__ == "__main__":
    main()
