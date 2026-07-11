"""SQLAlchemy 2.0 ORM 모델 및 저장소 헬퍼.

DATABASE_URL 하나로 SQLite / PostgreSQL을 스위칭한다.
- PostgreSQL: report 컬럼이 JSONB
- SQLite:     JSON 텍스트로 자동 대체 (JSON().with_variant)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd
from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float,
                        ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, select)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, sessionmaker)

from .config import DEFAULT_PAIRS, settings

logger = logging.getLogger(__name__)

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class StockPair(Base):
    __tablename__ = "stock_pairs"
    __table_args__ = (UniqueConstraint("kr_ticker", "us_ticker", name="uq_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kr_ticker: Mapped[str] = mapped_column(String(20))
    kr_name: Mapped[str] = mapped_column(String(100))
    us_ticker: Mapped[str] = mapped_column(String(20))
    us_name: Mapped[str] = mapped_column(String(100))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    reports: Mapped[list["AnalysisReport"]] = relationship(
        back_populates="pair", cascade="all, delete-orphan")


class DailyMarketData(Base):
    __tablename__ = "daily_market_data"
    __table_args__ = (UniqueConstraint("ticker", "trade_date",
                                       name="uq_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[Optional[float]] = mapped_column(Float)
    high: Mapped[Optional[float]] = mapped_column(Float)
    low: Mapped[Optional[float]] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    rsi_14: Mapped[Optional[float]] = mapped_column(Float)
    sma_20: Mapped[Optional[float]] = mapped_column(Float)
    sma_60: Mapped[Optional[float]] = mapped_column(Float)
    macd: Mapped[Optional[float]] = mapped_column(Float)
    macd_signal: Mapped[Optional[float]] = mapped_column(Float)
    macd_hist: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"
    __table_args__ = (UniqueConstraint("pair_id", "report_date",
                                       name="uq_pair_report_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("stock_pairs.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date)
    sentiment_score: Mapped[float] = mapped_column(Float)
    technical_signal: Mapped[str] = mapped_column(String(10))
    final_view: Mapped[str] = mapped_column(String(10))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(JsonType)
    model: Mapped[Optional[str]] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    pair: Mapped[StockPair] = relationship(back_populates="reports")


# ---------------------------------------------------------------- engine

def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True,
                                                          exist_ok=True)
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """테이블 생성 + 매핑 시드."""
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if session.scalar(select(StockPair.id).limit(1)) is None:
            session.add_all([
                StockPair(kr_ticker=p.kr_ticker, kr_name=p.kr_name,
                          us_ticker=p.us_ticker, us_name=p.us_name,
                          rationale=p.rationale)
                for p in DEFAULT_PAIRS
            ])
            session.commit()
            logger.info("기본 종목 매핑 %d건 시드 완료", len(DEFAULT_PAIRS))


# ---------------------------------------------------------------- repository

def get_active_pairs(session: Session) -> Sequence[StockPair]:
    return session.scalars(
        select(StockPair).where(StockPair.is_active.is_(True))).all()


def upsert_market_data(session: Session, ticker: str,
                       df: pd.DataFrame) -> int:
    """(ticker, trade_date) 기준 upsert. df index는 날짜여야 한다."""
    cols = ("open", "high", "low", "close", "volume", "rsi_14", "sma_20",
            "sma_60", "macd", "macd_signal", "macd_hist")
    count = 0
    for idx, row in df.iterrows():
        if pd.isna(row.get("close")):   # 종가 없는 행은 저장하지 않음
            continue
        trade_date = idx.date() if hasattr(idx, "date") else idx
        values = {c: (None if pd.isna(row.get(c)) else
                      (int(row[c]) if c == "volume" else float(row[c])))
                  for c in cols if c in row}
        existing = session.scalar(
            select(DailyMarketData).where(
                DailyMarketData.ticker == ticker,
                DailyMarketData.trade_date == trade_date))
        if existing is not None:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            session.add(DailyMarketData(ticker=ticker, trade_date=trade_date,
                                        **values))
        count += 1
    return count


def save_report(session: Session, pair_id: int, report_date: date,
                result_dict: dict[str, Any], model: str) -> AnalysisReport:
    """(pair_id, report_date) 기준 upsert — 재실행 시 당일 리포트 갱신."""
    existing = session.scalar(
        select(AnalysisReport).where(AnalysisReport.pair_id == pair_id,
                                     AnalysisReport.report_date == report_date))
    fields = dict(
        sentiment_score=result_dict["overall_sentiment_score"],
        technical_signal=result_dict["technical_signal"],
        final_view=result_dict["final_view"],
        confidence=result_dict["confidence"],
        summary=result_dict["summary"],
        report=result_dict,
        model=model,
    )
    if existing is not None:
        for k, v in fields.items():
            setattr(existing, k, v)
        return existing
    report = AnalysisReport(pair_id=pair_id, report_date=report_date, **fields)
    session.add(report)
    return report
