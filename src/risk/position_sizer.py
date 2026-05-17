"""Position sizing conservativo con reliability multiplier per mercato.

Quantità = floor(budget_per_titolo * reliability_mult / prezzo).
Vincoli:
  - max_position_pct: max X% capitale totale per singolo titolo
  - max_concurrent_positions: limita N posizioni aperte simultaneamente
  - score modulator: posizione più grande per score più alto (fino al cap)
  - reliability multiplier: riduce la size per mercati con meno dati affidabili
    high=100%, medium_high=85%, medium=70%, low=50%
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..data.universe import get_asset
from ..utils.config import load_config


@dataclass
class SizingDecision:
    ticker: str
    quantity: int
    notional: float
    rationale: str


def calculate_size(
    ticker: str,
    price: float,
    score: float,
    horizon_budget: float,
    total_equity: float,
    open_positions_in_horizon: int,
) -> SizingDecision:
    cfg = load_config()["risk"]
    cfg_b = load_config()["broker"]["paper"]

    max_concurrent = cfg["max_concurrent_positions"]
    max_per_position = total_equity * cfg["max_position_pct"]

    asset = get_asset(ticker)
    reliability_mult = asset.size_multiplier() if asset else 0.70

    free_slots = max(3, max_concurrent - open_positions_in_horizon)

    base = horizon_budget / free_slots
    score_mult = min(1.0, 0.6 + 0.4 * (abs(score) - 40) / 60) if abs(score) > 40 else 0.7
    proposed = min(base * score_mult * reliability_mult, max_per_position * reliability_mult)

    if price <= 0 or proposed <= 0:
        return SizingDecision(ticker=ticker, quantity=0, notional=0, rationale="invalid_inputs")

    min_notional = cfg_b["commission_per_trade_eur"] / 0.02
    if proposed < min_notional:
        return SizingDecision(
            ticker=ticker, quantity=0, notional=0,
            rationale=f"notional_too_small (<{min_notional:.0f}€, commission would eat >2%)",
        )

    qty = int(math.floor(proposed / price))
    if qty < 1:
        return SizingDecision(
            ticker=ticker, quantity=0, notional=0,
            rationale=f"price_too_high_for_budget (price={price:.2f}, budget={proposed:.2f})",
        )

    notional = qty * price
    return SizingDecision(
        ticker=ticker,
        quantity=qty,
        notional=notional,
        rationale=(f"base={base:.2f} score_mult={score_mult:.2f} "
                   f"reliability_mult={reliability_mult:.2f} "
                   f"cap={max_per_position:.2f} -> notional={notional:.2f}"),
    )
