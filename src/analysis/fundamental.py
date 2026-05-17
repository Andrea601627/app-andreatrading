"""Analisi fondamentale -> score [-100, +100].

Logica basata su value + quality + growth + safety.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..data.fundamentals import Fundamentals


@dataclass
class FundamentalResult:
    score: float
    details: dict = field(default_factory=dict)
    confidence: float = 1.0


def _clip(x: float, lo: float = -100, hi: float = 100) -> float:
    return float(max(lo, min(hi, x)))


def _score_pe(pe: float | None) -> float:
    if pe is None or pe <= 0:
        return 0.0
    # P/E < 12 ottimo, 12-18 buono, 18-25 neutrale, >30 caro
    if pe < 8: return 60
    if pe < 12: return 40
    if pe < 18: return 15
    if pe < 25: return -5
    if pe < 35: return -30
    return -55


def _score_pb(pb: float | None) -> float:
    if pb is None or pb <= 0:
        return 0.0
    if pb < 1: return 40
    if pb < 2: return 20
    if pb < 3: return 0
    if pb < 5: return -20
    return -40


def _score_roe(roe: float | None) -> float:
    if roe is None:
        return 0.0
    # roe è in formato decimale (es 0.15 = 15%)
    if roe > 0.20: return 60
    if roe > 0.12: return 35
    if roe > 0.06: return 10
    if roe > 0: return -5
    return -50


def _score_debt(de: float | None) -> float:
    if de is None:
        return 0.0
    # yfinance ritorna debt/equity in %, es 80 = 0.8x
    de_ratio = de / 100 if de > 5 else de
    if de_ratio < 0.3: return 35
    if de_ratio < 0.6: return 15
    if de_ratio < 1.0: return 0
    if de_ratio < 1.5: return -25
    return -50


def _score_growth(rev_g: float | None, eps_g: float | None) -> float:
    parts = []
    for g in (rev_g, eps_g):
        if g is None:
            continue
        if g > 0.20: parts.append(50)
        elif g > 0.10: parts.append(25)
        elif g > 0: parts.append(5)
        elif g > -0.10: parts.append(-20)
        else: parts.append(-50)
    return sum(parts) / len(parts) if parts else 0


def _score_margin(margin: float | None) -> float:
    if margin is None:
        return 0.0
    if margin > 0.20: return 40
    if margin > 0.10: return 20
    if margin > 0.05: return 5
    if margin > 0: return -10
    return -45


def _score_dividend(dy: float | None) -> float:
    if dy is None:
        return 0.0
    # yfinance ritorna sia in formato decimale che in % a seconda della versione
    rate = dy / 100 if dy > 1 else dy
    if rate > 0.06: return 25
    if rate > 0.03: return 15
    if rate > 0.01: return 5
    return 0


def compute(f: Fundamentals | None) -> FundamentalResult:
    if f is None:
        return FundamentalResult(score=0.0, details={"reason": "no_fundamentals"}, confidence=0.0)

    parts = {
        "pe": _score_pe(f.pe_ratio),
        "pb": _score_pb(f.price_to_book),
        "roe": _score_roe(f.return_on_equity),
        "debt": _score_debt(f.debt_to_equity),
        "growth": _score_growth(f.revenue_growth, f.earnings_growth),
        "margin": _score_margin(f.profit_margin),
        "dividend": _score_dividend(f.dividend_yield),
    }
    # Pesi per categoria
    weights = {
        "pe": 0.20, "pb": 0.10, "roe": 0.20, "debt": 0.15,
        "growth": 0.20, "margin": 0.10, "dividend": 0.05,
    }
    weighted = sum(parts[k] * weights[k] for k in parts)

    # Confidence: ridotta se molti valori mancanti
    n_present = sum(1 for v in [f.pe_ratio, f.price_to_book, f.return_on_equity,
                                f.debt_to_equity, f.revenue_growth, f.profit_margin] if v is not None)
    confidence = min(n_present / 6.0, 1.0)

    return FundamentalResult(
        score=_clip(weighted),
        details=parts,
        confidence=confidence,
    )
