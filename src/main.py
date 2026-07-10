"""main.py — 야간 배치 파이프라인 엔트리포인트.

흐름:
  1. DB 초기화(테이블 생성 + 매핑 시드)
  2. 활성 페어별로 한국/미국 시세·지표·뉴스 수집 → daily_market_data upsert
  3. LLM 분석 → analysis_reports 저장
  4. 페어 단위 예외 격리 — 한 페어 실패가 전체 배치를 죽이지 않음

실행: python -m src.main
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .analyzer import get_analyzer
from .collector import MarketDataCollector
from .indicators import pair_stats
from .database import (SessionLocal, get_active_pairs, init_db, save_report,
                       upsert_market_data)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")

KST = ZoneInfo("Asia/Seoul")


def run() -> int:
    """배치 실행. 실패한 페어 수를 종료 코드로 반환한다."""
    report_date = datetime.now(KST).date()
    logger.info("===== 야간 배치 시작 (기준일 %s KST) =====", report_date)

    init_db()
    collector = MarketDataCollector()
    analyzer = get_analyzer()

    ok, failed = 0, 0
    with SessionLocal() as session:
        pairs = get_active_pairs(session)
        logger.info("활성 페어 %d건", len(pairs))

        # 매크로 컨텍스트(환율/SOX/VIX)는 1회만 수집해 전 페어가 공유
        context = collector.collect_context()
        for ticker, data in context.items():
            upsert_market_data(session, ticker, data.history)
        session.commit()

        for pair in pairs:
            label = f"{pair.kr_name}({pair.kr_ticker}) <-> " \
                    f"{pair.us_name}({pair.us_ticker})"
            try:
                # [Data Pipeline] 수집 + 지표 계산 (한국어 뉴스는 종목명으로 검색)
                kr = collector.collect(pair.kr_ticker, kr_query=pair.kr_name)
                us = collector.collect(pair.us_ticker, kr_query=pair.us_name)
                upsert_market_data(session, pair.kr_ticker, kr.history)
                upsert_market_data(session, pair.us_ticker, us.history)
                session.commit()

                # 페어 연동 통계 (60일 오버나이트 상관/베타)
                stats = pair_stats(kr.history["close"], us.history["close"])

                # [AI Analysis Layer] 구조화 리포트 생성
                result = analyzer.analyze(pair, kr, us, stats, context,
                                          report_date)

                # [Storage] — 분석 시점의 연동 통계도 함께 보존
                payload = result.model_dump(mode="json")
                payload["pair_stats"] = stats
                save_report(session, pair.id, report_date, payload,
                            analyzer.model or "claude-cli-default")
                session.commit()
                ok += 1
                logger.info("✅ %s → %s (감성 %.2f)", label,
                            result.final_view, result.overall_sentiment_score)
            except Exception:
                session.rollback()
                failed += 1
                logger.exception("❌ %s 처리 실패 — 다음 페어로 진행", label)

    logger.info("===== 배치 종료: 성공 %d / 실패 %d =====", ok, failed)
    return failed


if __name__ == "__main__":
    sys.exit(min(run(), 1))
