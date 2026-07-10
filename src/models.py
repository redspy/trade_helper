"""Pydantic 도메인 모델 — LLM 구조화 출력 스키마 포함."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class NewsHeadline(BaseModel):
    """수집된 뉴스 헤드라인 (LLM 입력용)."""

    title: str
    publisher: Optional[str] = None
    published_at: Optional[datetime] = None
    url: Optional[str] = None


class NewsSentiment(BaseModel):
    """개별 헤드라인에 대한 감성 판정 (LLM 출력)."""

    headline: str = Field(description="평가한 뉴스 헤드라인 원문")
    score: float = Field(ge=-1.0, le=1.0,
                         description="감성 점수: -1(매우 부정) ~ +1(매우 긍정)")
    reason: str = Field(description="점수 근거 한 문장")


class DirectionProbabilities(BaseModel):
    """오늘 한국 종목의 방향 확률 (합계 = 1.0)."""

    up: float = Field(ge=0.0, le=1.0, description="상승(+0.5% 초과) 확률")
    flat: float = Field(ge=0.0, le=1.0, description="보합(-0.5% ~ +0.5%) 확률")
    down: float = Field(ge=0.0, le=1.0, description="하락(-0.5% 미만) 확률")


class AnalysisResult(BaseModel):
    """LLM이 반환하는 최종 분석 리포트 (Structured Output 스키마)."""

    us_market_summary: str = Field(
        description="미국 선행 종목의 직전 세션 흐름 요약 (가격 변동 + 지표 + 뉴스, 2~3문장)")
    expected_impact_on_kr: str = Field(
        description="위 흐름이 오늘 한국 종목에 미칠 영향 추론 — 이미 반영된 재료와 "
                    "미반영 재료를 구분하여 서술 (2~3문장)")
    news_sentiments: list[NewsSentiment] = Field(
        description="수집된 각 뉴스 헤드라인의 감성 평가")
    overall_sentiment_score: float = Field(
        ge=-1.0, le=1.0,
        description="뉴스 전체를 종합한 감성 점수 (-1 ~ +1)")
    technical_signal: Literal["bullish", "neutral", "bearish"] = Field(
        description="RSI/이동평균/MACD를 종합한 기술적 신호")
    expected_direction: Literal["상승", "보합", "하락"] = Field(
        description="오늘 한국 종목의 예상 방향 (확률이 가장 높은 시나리오)")
    direction_probabilities: DirectionProbabilities = Field(
        description="상승/보합/하락 확률 — 합이 1.0이 되어야 함")
    final_view: Literal["매수", "중립", "관망"] = Field(
        description="수치 + 감성 + 기술적 지표를 종합한 최종 뷰")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="확신도 — 캘리브레이션 기준: 3개 근거(연동통계·감성·기술) 모두 "
                    "일치 시 0.8+, 2개 일치 0.6~0.8, 상충 시 0.6 미만")
    bull_case: str = Field(
        description="강세 시나리오 — 어떤 조건이면 상승하는지 1~2문장")
    bear_case: str = Field(
        description="약세 시나리오 — 어떤 조건이면 하락하는지 1~2문장")
    invalidation_trigger: str = Field(
        description="이 뷰가 무효화되는 구체적 조건 (가격 레벨/이벤트) 1문장")
    rationale: str = Field(
        description="최종 뷰 도출 근거 — 데이터를 인용한 3~5문장")
    summary: str = Field(description="대시보드 카드용 한 줄 요약 (40자 이내)")
