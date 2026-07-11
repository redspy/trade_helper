"""analyzer.py — LLM 분석 레이어 (이중 백엔드).

백엔드 선택 (환경 변수 LLM_BACKEND):
  - "claude_cli" : Claude Code 헤드리스 모드(`claude -p`). API 키 불필요 —
                   로그인된 Claude 구독으로 실행. (로컬 기본)
  - "api"        : Anthropic SDK + client.messages.parse() 구조화 출력.
  - "auto"(기본) : ANTHROPIC_API_KEY가 있으면 api, 없으면 claude_cli.

두 백엔드 모두 Pydantic AnalysisResult로 검증된 결과를 반환한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess

import pandas as pd
from pydantic import ValidationError

from .collector import TickerData
from .config import settings
from .database import StockPair
from .models import AnalysisResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
당신은 한국 기관투자자 대상 장전(pre-market) 브리핑을 작성하는 크로스보더 애널리스트입니다.
미국 직전 세션의 마감 데이터가 '오늘' 한국 세션에 미칠 영향을 분석합니다.

## 시장 시차 구조 (모든 분석의 전제)
- 미국 직전 세션은 한국 시간 오늘 새벽에 마감했고, 그 결과는 아직 한국 가격에 미반영.
- 한국 데이터의 마지막 종가는 미국 직전 세션 '이전'에 형성된 가격.
- 따라서 분석 대상은 "미국 마감분 + 신규 뉴스가 오늘 한국 개장/장중에 어떻게 반영될 것인가".
- 이미 한국 직전 종가에 반영된 재료와 오늘 새로 반영될 재료를 반드시 구분할 것.

## 분석 원칙
1. 수치 인용 강제: 가격, RSI, SMA, MACD, 오버나이트 상관계수/베타, 환율을 근거로 인용할 것.
2. 페어 연동 통계를 존중할 것: 60일 오버나이트 상관계수의 절대값이 0.3 미만이면
   "미국 종목이 올랐으니 한국 종목도 오른다"는 논리를 과신하지 말고 그 사실을 명시할 것.
   베타를 사용해 기대 갭의 크기를 정량적으로 서술할 것 (예: 미국 +2% × 베타 0.5 → 약 +1%).
3. 뉴스 신선도: 발행 3일이 지난 헤드라인은 이미 가격에 반영된 것으로 간주하고
   감성 가중치를 낮출 것. 종합 감성 점수는 신선도·관련도 가중 평균으로 산출할 것.
4. 기술적 신호 판정 기준:
   - RSI 70 이상 과매수 / 30 이하 과매도
   - 종가와 SMA20/SMA60의 위치 관계 (골든/데드 크로스 여부)
   - MACD 히스토그램의 방향 전환
5. 매크로 컨텍스트: 원/달러 환율 방향(원화 약세=외국인 수급 부담), SOX(반도체 섹터 공통
   동인), VIX(변동성 국면에서는 개별 재료보다 위험회피가 지배)를 판단에 반영할 것.

## 방향 확률과 확신도
6. 오늘 한국 종목의 상승(+0.5% 초과)/보합/하락(-0.5% 미만) 확률을 합계 1.0으로 배분할 것.
   확률은 베타 기반 기대 갭, 기술적 위치, 감성을 종합해 산출하고 근거와 일관되어야 함.
7. 확신도 캘리브레이션 (반드시 이 기준을 따를 것):
   - 0.80 이상: 연동 통계·뉴스 감성·기술적 신호 3가지가 모두 같은 방향
   - 0.60~0.79: 2가지가 일치하고 1가지가 중립 또는 약한 반대
   - 0.60 미만: 근거가 명확히 상충

## 최종 뷰 — '관망'을 기본값으로 쓰지 말 것
8. 세 가지 뷰의 정의:
   - 매수: 상승 확률이 하락 확률의 1.5배 이상이고 기술적 신호가 이를 지지
   - 중립: 방향 근거가 균형 — 기존 포지션 유지, 신규 진입 근거 부족
   - 관망: 임박한 이벤트(실적 발표, FOMC 등)나 급변동 국면으로 진입 자체가 부적절
   반드시 세 뷰를 각각 검토한 뒤 가장 부합하는 것을 선택하고,
   '관망'을 선택할 때는 어떤 이벤트/불확실성 때문인지 rationale에 명시할 것.
9. 강세/약세 시나리오와 뷰 무효화 조건(구체적 가격 레벨 또는 이벤트)을 반드시 제시할 것.
10. 모든 서술은 한국어. 투자 확정 조언이 아닌 데이터 기반 시나리오로 서술할 것.

## 독자 눈높이 — 주식 초보 개인투자자
11. 전문 용어(RSI, MACD, 베타, 갭 등)를 쓸 때는 처음 등장 시 괄호로 한 줄 쉬운 설명을
    붙일 것. 예: "RSI(최근 상승/하락 강도를 0~100으로 나타낸 온도계 같은 지표)".
    문장은 짧게, 한 문장에 하나의 논점만.
12. summary와 beginner_note는 전문용어 없이 완전한 일상어로 쓸 것.
    beginner_note는 "오늘 ~하는 게 좋아요"처럼 실행 가능한 행동 가이드로 쓰고,
    확신이 낮은 날에는 "거래하지 않고 지켜보는 것"을 당당히 권할 것.
"""


def _format_history(df: pd.DataFrame, days: int = 10) -> str:
    """최근 N거래일 가격/지표를 LLM 입력용 마크다운 테이블로 변환."""
    cols = ["close", "volume", "rsi_14", "sma_20", "sma_60", "macd",
            "macd_signal", "macd_hist"]
    view = df.tail(days)[cols].copy()
    view.index = [idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                  for idx in view.index]
    return view.round(3).to_markdown()


def _format_news(data: TickerData) -> str:
    if not data.news:
        return "(수집된 뉴스 없음)"
    lines = []
    for n in data.news:
        stamp = n.published_at.strftime("%Y-%m-%d") if n.published_at else "날짜미상"
        src = f" ({n.publisher})" if n.publisher else ""
        lines.append(f"- [{stamp}]{src} {n.title}")
    return "\n".join(lines)


_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def _format_pair_stats(stats: dict) -> str:
    corr, beta = stats.get("corr_60d"), stats.get("beta_overnight")
    if corr is None:
        return f"- 표본 부족 (관측치 {stats.get('n_obs', 0)}건) — 연동 통계 신뢰 불가"
    strength = ("강한" if abs(corr) >= 0.5 else
                "보통" if abs(corr) >= 0.3 else "약한(주의: 연동 논리 과신 금지)")
    lines = [f"- 60일 오버나이트 상관계수: {corr:+.3f} ({strength} 연동, "
             f"관측치 {stats['n_obs']}건)"]
    if beta is not None:
        lines.append(f"- 오버나이트 베타: {beta:+.3f} "
                     f"(미국 +1% 마감 시 한국 {beta:+.2f}% 갭 경향)")
    return "\n".join(lines)


def _format_context(context: dict[str, TickerData]) -> str:
    from .collector import CONTEXT_TICKERS
    if not context:
        return "(매크로 데이터 수집 실패 — 개별 종목 데이터만으로 판단)"
    lines = []
    for ticker, data in context.items():
        c = data.history["close"].dropna()
        if len(c) < 6:
            continue
        d1 = (c.iloc[-1] / c.iloc[-2] - 1) * 100
        d5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100
        lines.append(f"- {CONTEXT_TICKERS.get(ticker, ticker)} ({ticker}): "
                     f"{c.iloc[-1]:,.2f} (1일 {d1:+.2f}%, 5일 {d5:+.2f}%)")
    return "\n".join(lines) or "(매크로 데이터 없음)"


def build_user_prompt(pair: StockPair, kr: TickerData, us: TickerData,
                      stats: dict, context: dict[str, TickerData],
                      report_date) -> str:
    us_chg = (us.latest["close"] / us.history["close"].iloc[-2] - 1) * 100 \
        if len(us.history) >= 2 else 0.0
    weekday = _WEEKDAY_KO[report_date.weekday()]
    return f"""\
## 브리핑 기준일
오늘은 {report_date} ({weekday}요일)입니다. 한국 휴장일이면 다음 거래일 기준으로 서술하세요.

## 분석 대상 페어
- 한국 종목: {pair.kr_name} ({pair.kr_ticker})
- 미국 선행 종목: {pair.us_name} ({pair.us_ticker})
- 연동 근거(정성): {pair.rationale or "N/A"}

## 페어 연동 통계 (한국 수익률 t ↔ 미국 직전 세션 수익률, 정량 검증)
{_format_pair_stats(stats)}

## 매크로 컨텍스트
{_format_context(context)}

## 미국 종목 {pair.us_name} — 최근 10거래일 시세 및 지표
(직전 세션 등락률: {us_chg:+.2f}% — 이 마감분이 오늘 한국 세션에 반영될 차례)
{_format_history(us.history)}

## 미국 종목 관련 최신 뉴스 헤드라인
{_format_news(us)}

## 한국 종목 {pair.kr_name} — 최근 10거래일 시세 및 지표
(마지막 종가는 미국 직전 세션 '이전' 가격 — 미국 마감분 미반영 상태)
{_format_history(kr.history)}

## 한국 종목 관련 최신 뉴스 헤드라인
{_format_news(kr)}

위 데이터를 바탕으로 오늘 {pair.kr_name}에 대한 장전 브리핑을 작성하세요.
"""


# ================================================================ API 백엔드

class StockAnalyzer:
    """Anthropic SDK — messages.parse()로 스키마를 API 레벨에서 강제."""

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # 선택 의존성 — CLI 백엔드만 쓸 때 임포트 비용 회피

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다 (.env 확인)")
        self.model = model or settings.anthropic_model
        # SDK가 429/5xx/네트워크 오류를 지수 백오프로 자동 재시도
        self.client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, max_retries=3)

    def analyze(self, pair: StockPair, kr: TickerData, us: TickerData,
                stats: dict, context: dict[str, TickerData],
                report_date) -> AnalysisResult:
        import anthropic

        prompt = build_user_prompt(pair, kr, us, stats, context, report_date)
        logger.info("[%s <-> %s] LLM 분석 요청 (api, model=%s)",
                    pair.kr_ticker, pair.us_ticker, self.model)
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_format=AnalysisResult,
            )
        except anthropic.RateLimitError:
            logger.error("레이트 리밋 초과 — SDK 재시도까지 모두 실패")
            raise
        except anthropic.APIStatusError as e:
            logger.error("API 오류 status=%s type=%s", e.status_code, e.type)
            raise
        except anthropic.APIConnectionError:
            logger.error("네트워크 오류 — Anthropic API에 연결할 수 없습니다")
            raise

        result = response.parsed_output
        if result is None:
            raise ValueError(
                f"구조화 출력 파싱 실패 (stop_reason={response.stop_reason})")
        _log_result(pair, result)
        return result


# ============================================================ CLI 백엔드

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class ClaudeCliAnalyzer:
    """Claude Code 헤드리스 모드(`claude -p --output-format json`).

    API 키 없이 로그인된 Claude 구독으로 실행된다. CLI에는 스키마 강제
    옵션이 없으므로: 프롬프트에 JSON 스키마를 포함 → 응답에서 JSON 추출
    → Pydantic 검증 → 실패 시 오류 메시지를 되먹여 최대 3회 재시도.
    """

    MAX_ATTEMPTS = 3
    TIMEOUT_SEC = 600

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("CLAUDE_CLI_MODEL", "")

    def _schema_instruction(self) -> str:
        schema = json.dumps(AnalysisResult.model_json_schema(),
                            ensure_ascii=False, indent=2)
        return (
            "\n\n## 출력 형식 (절대 준수)\n"
            "아래 JSON 스키마에 정확히 부합하는 JSON 객체 **하나만** 출력하세요.\n"
            "코드 펜스, 설명 문장, 마크다운 없이 순수 JSON만 출력합니다.\n"
            f"```json-schema\n{schema}\n```"
        )

    def _invoke(self, prompt: str) -> str:
        cmd = ["claude", "-p", "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, timeout=self.TIMEOUT_SEC)
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 종료 코드 {proc.returncode}: {proc.stderr[:500]}")
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            raise RuntimeError(f"claude CLI 오류 응답: {envelope}")
        return envelope.get("result", "")

    @staticmethod
    def _extract_json(text: str) -> dict:
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
        match = _JSON_BLOCK.search(cleaned)
        if not match:
            raise ValueError(f"응답에서 JSON을 찾지 못함: {text[:200]!r}")
        return json.loads(match.group(0))

    def analyze(self, pair: StockPair, kr: TickerData, us: TickerData,
                stats: dict, context: dict[str, TickerData],
                report_date) -> AnalysisResult:
        base_prompt = (SYSTEM_PROMPT + "\n---\n"
                       + build_user_prompt(pair, kr, us, stats, context,
                                           report_date)
                       + self._schema_instruction())
        logger.info("[%s <-> %s] LLM 분석 요청 (claude_cli%s)",
                    pair.kr_ticker, pair.us_ticker,
                    f", model={self.model}" if self.model else "")
        prompt = base_prompt
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                raw = self._invoke(prompt)
                result = AnalysisResult.model_validate(self._extract_json(raw))
                _log_result(pair, result)
                return result
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                last_error = e
                logger.warning("CLI 응답 검증 실패 (%d/%d): %s",
                               attempt, self.MAX_ATTEMPTS, e)
                prompt = (base_prompt
                          + "\n\n## 이전 시도 오류 (수정 필수)\n"
                          + f"직전 출력이 스키마 검증에 실패했습니다: {e}\n"
                          + "스키마에 정확히 맞는 순수 JSON만 다시 출력하세요.")
            except subprocess.TimeoutExpired as e:
                last_error = e
                logger.warning("CLI 타임아웃 (%d/%d)", attempt, self.MAX_ATTEMPTS)
        raise RuntimeError(f"claude CLI 분석 {self.MAX_ATTEMPTS}회 모두 실패"
                           ) from last_error


# ============================================================ 팩토리

def _log_result(pair: StockPair, result: AnalysisResult) -> None:
    logger.info("[%s] 분석 완료: view=%s sentiment=%.2f confidence=%.2f",
                pair.kr_ticker, result.final_view,
                result.overall_sentiment_score, result.confidence)


def get_analyzer() -> StockAnalyzer | ClaudeCliAnalyzer:
    """LLM_BACKEND 환경 변수로 백엔드 선택 (auto | api | claude_cli)."""
    backend = os.environ.get("LLM_BACKEND", "auto").lower()
    if backend == "api" or (backend == "auto" and settings.anthropic_api_key):
        return StockAnalyzer()
    logger.info("Claude Code 헤드리스 CLI 백엔드 사용 (API 키 불필요)")
    return ClaudeCliAnalyzer()
