"""Fondamentali aziendali via yfinance + cross-check minimi."""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict

import yfinance as yf

from ..utils.logger import get_logger

log = get_logger()

_cache: dict[str, tuple[float, dict]] = {}
_TTL = 24 * 3600


@dataclass
class Fundamentals:
    ticker: str
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    peg_ratio: float | None = None
    price_to_book: float | None = None
    return_on_equity: float | None = None
    debt_to_equity: float | None = None
    profit_margin: float | None = None
    operating_margin: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    dividend_yield: float | None = None
    free_cashflow: float | None = None
    beta: float | None = None
    trailing_eps: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _safe(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "Infinity", "-Infinity"):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def get_fundamentals(ticker: str) -> Fundamentals:
    now = time.time()
    cached = _cache.get(ticker)
    if cached and (now - cached[0]) < _TTL:
        return Fundamentals(**cached[1])

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        log.warning(f"yfinance info failed for {ticker}: {e}")
        info = {}

    f = Fundamentals(
        ticker=ticker,
        market_cap=_safe(info, "marketCap"),
        pe_ratio=_safe(info, "trailingPE"),
        forward_pe=_safe(info, "forwardPE"),
        peg_ratio=_safe(info, "pegRatio", "trailingPegRatio"),
        price_to_book=_safe(info, "priceToBook"),
        return_on_equity=_safe(info, "returnOnEquity"),
        debt_to_equity=_safe(info, "debtToEquity"),
        profit_margin=_safe(info, "profitMargins"),
        operating_margin=_safe(info, "operatingMargins"),
        revenue_growth=_safe(info, "revenueGrowth"),
        earnings_growth=_safe(info, "earningsGrowth"),
        dividend_yield=_safe(info, "dividendYield"),
        free_cashflow=_safe(info, "freeCashflow"),
        beta=_safe(info, "beta"),
        trailing_eps=_safe(info, "trailingEps"),
    )
    _cache[ticker] = (now, f.to_dict())
    return f
