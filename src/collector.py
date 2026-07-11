"""collector.py — 주가/기술적 지표 수집 및 뉴스 헤드라인 수집.

yfinance 호출은 일시적 네트워크 오류가 잦으므로 tenacity로
지수 백오프 재시도를 건다.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import pandas as pd
import yfinance as yf
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from .config import settings
from .indicators import enrich_with_indicators
from .models import NewsHeadline

logger = logging.getLogger(__name__)

# 매크로 컨텍스트 티커 — 한국 개장 갭의 공통 동인
CONTEXT_TICKERS: dict[str, str] = {
    "USDKRW=X": "원/달러 환율",
    "^SOX": "필라델피아 반도체지수",
    "^VIX": "VIX 변동성지수",
}

# 한국어 뉴스 소스 — Google News RSS (API 키 불필요, 최근 7일 검색)
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

_RETRY = dict(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)


@dataclass
class TickerData:
    """단일 티커의 수집 결과."""

    ticker: str
    history: pd.DataFrame                       # 최근 N거래일 + 지표
    news: list[NewsHeadline] = field(default_factory=list)

    @property
    def latest(self) -> pd.Series:
        return self.history.iloc[-1]


class MarketDataCollector:
    """가격 이력(+지표)과 뉴스 헤드라인을 수집한다."""

    def __init__(self, history_days: int | None = None) -> None:
        self.history_days = history_days or settings.history_days

    @retry(**_RETRY)
    def _download_history(self, ticker: str) -> pd.DataFrame:
        df = yf.Ticker(ticker).history(period=settings.fetch_period,
                                       interval="1d", auto_adjust=True)
        if df.empty:
            raise ValueError(f"{ticker}: 가격 데이터가 비어 있습니다")
        return df

    def fetch_price_history(self, ticker: str) -> pd.DataFrame:
        """6개월치 일봉을 받아 지표를 계산해 전체 반환.
        (롤링 상관/베타 계산과 DB 축적을 위해 자르지 않는다 —
        프롬프트용 최근 구간은 사용처에서 tail로 추출)"""
        raw = self._download_history(ticker)
        df = raw.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })[["open", "high", "low", "close", "volume"]]
        # 휴장/집계 경계에서 종가 없는 빈 행이 섞여 오는 경우 제거
        df = df.dropna(subset=["close"])
        return enrich_with_indicators(df)

    @retry(**_RETRY)
    def _download_news(self, ticker: str) -> list[dict]:
        return yf.Ticker(ticker).news or []

    def fetch_news(self, ticker: str) -> list[NewsHeadline]:
        """yfinance 뉴스 피드에서 최근 헤드라인을 최대 N건 수집.
        뉴스 실패는 치명적이지 않으므로 빈 리스트로 폴백한다."""
        try:
            items = self._download_news(ticker)
        except Exception:
            logger.warning("%s: 뉴스 수집 실패 — 뉴스 없이 진행", ticker,
                           exc_info=True)
            return []

        headlines: list[NewsHeadline] = []
        for item in items[: settings.max_news_per_ticker]:
            # yfinance 0.2.5x부터 {'content': {...}} 중첩 구조
            content = item.get("content", item)
            title = content.get("title")
            if not title:
                continue
            published_at = None
            raw_date = content.get("pubDate") or content.get("providerPublishTime")
            try:
                if isinstance(raw_date, (int, float)):
                    published_at = datetime.fromtimestamp(raw_date, tz=timezone.utc)
                elif isinstance(raw_date, str):
                    published_at = datetime.fromisoformat(
                        raw_date.replace("Z", "+00:00"))
            except (ValueError, OSError):
                pass
            provider = content.get("provider") or {}
            url = ((content.get("canonicalUrl") or {}).get("url")
                   or content.get("link"))
            headlines.append(NewsHeadline(
                title=title,
                publisher=(provider.get("displayName")
                           if isinstance(provider, dict) else None),
                published_at=published_at,
                url=url,
            ))
        return headlines

    # ------------------------------------------------------ 한국어 뉴스

    @retry(**_RETRY)
    def _download_kr_rss(self, query: str) -> str:
        resp = httpx.get(GOOGLE_NEWS_RSS,
                         params={"q": f"{query} when:7d", "hl": "ko",
                                 "gl": "KR", "ceid": "KR:ko"},
                         timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    def fetch_kr_news(self, query: str) -> list[NewsHeadline]:
        """Google News RSS에서 한국어 헤드라인 수집 (실패 시 빈 리스트 폴백)."""
        try:
            xml_text = self._download_kr_rss(query)
            root = ET.fromstring(xml_text)
        except Exception:
            logger.warning("'%s': 한국어 뉴스 수집 실패 — 제외하고 진행",
                           query, exc_info=True)
            return []

        headlines: list[NewsHeadline] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            source = (item.findtext("source") or "").strip() or None
            # Google News 제목은 "제목 - 언론사" 형식 — 꼬리 중복 제거
            if source and title.endswith(f" - {source}"):
                title = title[: -len(f" - {source}")].strip()
            published_at = None
            raw_date = item.findtext("pubDate")
            if raw_date:
                try:
                    published_at = parsedate_to_datetime(raw_date)
                except (ValueError, TypeError):
                    pass
            headlines.append(NewsHeadline(
                title=title, publisher=source, published_at=published_at,
                url=item.findtext("link")))
            if len(headlines) >= settings.max_news_per_ticker:
                break
        return headlines

    # ------------------------------------------------------ 통합 수집

    def collect(self, ticker: str, kr_query: str | None = None) -> TickerData:
        """시세 + 뉴스 수집. kr_query가 주어지면 한국어 뉴스(Google News RSS)를
        우선 배치하고 yfinance 영문 뉴스와 제목 기준으로 중복 제거해 병합."""
        logger.info("%s: 시세/뉴스 수집 시작", ticker)
        history = self.fetch_price_history(ticker)
        news = self.fetch_news(ticker)
        kr_count = 0
        if kr_query:
            kr_news = self.fetch_kr_news(kr_query)
            kr_count = len(kr_news)
            seen = {n.title for n in kr_news}
            news = kr_news + [n for n in news if n.title not in seen]
            news = news[: settings.max_news_per_ticker * 2]
        logger.info("%s: 거래일 %d건, 뉴스 %d건 수집 (한국어 %d건)",
                    ticker, len(history), len(news), kr_count)
        return TickerData(ticker=ticker, history=history, news=news)

    def collect_context(self) -> dict[str, TickerData]:
        """매크로 컨텍스트(환율/SOX/VIX) 수집 — 실패한 티커는 건너뛴다."""
        out: dict[str, TickerData] = {}
        for ticker, name in CONTEXT_TICKERS.items():
            try:
                out[ticker] = TickerData(ticker=ticker,
                                         history=self.fetch_price_history(ticker))
                logger.info("%s(%s): 컨텍스트 수집 완료", name, ticker)
            except Exception:
                logger.warning("%s(%s): 컨텍스트 수집 실패 — 제외하고 진행",
                               name, ticker, exc_info=True)
        return out
