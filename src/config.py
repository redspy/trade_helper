"""환경 변수 및 전역 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class PairSeed:
    """DB가 비어 있을 때 시드할 기본 종목 매핑."""

    kr_ticker: str
    kr_name: str
    us_ticker: str
    us_name: str
    rationale: str


DEFAULT_PAIRS: tuple[PairSeed, ...] = (
    PairSeed("005930.KS", "삼성전자", "NVDA", "엔비디아",
             "HBM/파운드리 수요가 엔비디아 AI 가속기 사이클에 직접 연동"),
    PairSeed("000660.KS", "SK하이닉스", "MU", "마이크론",
             "글로벌 메모리(DRAM/NAND) 업황 동조 — 마이크론 실적이 선행 지표"),
    PairSeed("373220.KS", "LG에너지솔루션", "TSLA", "테슬라",
             "EV 배터리 최대 고객사 — 테슬라 판매량/가이던스가 수요 선행"),
    PairSeed("035420.KS", "NAVER", "GOOGL", "알파벳",
             "검색/광고/AI 플랫폼 비즈니스 모델 동조"),
)


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"))
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'trade_dash.db'}"))
    max_news_per_ticker: int = field(
        default_factory=lambda: int(os.environ.get("MAX_NEWS_PER_TICKER", "5")))
    history_days: int = 30          # 분석에 사용할 최근 거래일 수
    fetch_period: str = "6mo"       # SMA60 계산 워밍업을 위해 여유 있게 조회


settings = Settings()
