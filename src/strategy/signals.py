"""Orchestrazione completa per produrre un segnale per ticker.

Flow:
  prezzi -> quality check
  fondamentali -> quality check
  news -> sentiment
  prezzi -> technical + ml_forecast
  regime -> aggregator -> decision
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis import fundamental as fa_mod
from ..analysis import ml_forecast as ml_mod
from ..analysis import sentiment as sa_mod
from ..analysis import technical as ta_mod
from ..analysis.regime import MarketRegime
from ..data.fetcher import get_price_history
from ..data.fundamentals import get_fundamentals
from ..data.news import get_news
from ..data.quality import validate_price_history
from ..data.universe import get_asset
from ..utils.logger import get_logger
from .aggregator import AggregatedScore, aggregate
from .decision import Decision, decide

log = get_logger()


@dataclass
class TickerSignal:
    ticker: str
    quality_ok: bool
    quality_issues: list[str] = field(default_factory=list)
    short_medium: AggregatedScore | None = None
    long_term: AggregatedScore | None = None
    decision_short: Decision | None = None
    decision_long: Decision | None = None
    last_price: float | None = None
    raw_components: dict = field(default_factory=dict)


def analyze_ticker(ticker: str, regime: MarketRegime | None = None) -> TickerSignal:
    asset = get_asset(ticker)
    name = asset.name if asset else None

    df = get_price_history(ticker, period="2y")
    q = validate_price_history(ticker, df)
    sig = TickerSignal(ticker=ticker, quality_ok=q.ok, quality_issues=q.issues)
    if not q.ok:
        log.warning(f"{ticker} quality FAIL: {q.issues}")
        return sig

    sig.last_price = float(df["Close"].iloc[-1])

    tech = ta_mod.compute(df)
    fundamentals_data = get_fundamentals(ticker)
    fund = fa_mod.compute(fundamentals_data)
    fc = ml_mod.compute(df, horizon_days=10)
    news_items = get_news(ticker, name=name, limit=20)
    sent = sa_mod.compute(news_items)

    sig.raw_components = {
        "technical": {"score": tech.score, "confidence": tech.confidence,
                      "details": tech.details, "indicators": tech.indicators},
        "fundamental": {"score": fund.score, "confidence": fund.confidence,
                        "details": fund.details, "raw": fundamentals_data.to_dict()},
        "ml_forecast": {"score": fc.score, "confidence": fc.confidence,
                        "details": fc.details},
        "sentiment": {"score": sent.score, "confidence": sent.confidence,
                      "details": sent.details},
    }

    for horizon in ("short_medium", "long_term"):
        agg = aggregate(horizon, tech, fund, fc, sent, regime=regime)
        dec = decide(agg)
        if horizon == "short_medium":
            sig.short_medium = agg
            sig.decision_short = dec
        else:
            sig.long_term = agg
            sig.decision_long = dec

    return sig
