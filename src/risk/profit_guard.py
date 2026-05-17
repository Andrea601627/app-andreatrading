"""Protezione guadagni: trailing stop adattivo + hard stop loss.

Logica:
- Hard stop: se il prezzo scende di hard_stop_pct dal prezzo di acquisto → vendi sempre
- Profit lock: se il guadagno ha raggiunto profit_lock_trigger_pct e poi il prezzo
  scende di profit_trail_pct dal picco → vendi per proteggere il guadagno
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardResult:
    should_sell: bool
    reason: str          # "hard_stop" | "profit_lock" | "hold"
    net_gain_pct: float  # guadagno netto dopo commissioni


def check(
    buy_price: float,
    current_price: float,
    high_water_mark: float,
    quantity: int,
    cfg_momentum: dict,
) -> GuardResult:
    if buy_price <= 0 or current_price <= 0:
        return GuardResult(should_sell=False, reason="hold", net_gain_pct=0.0)

    commission_eur = cfg_momentum["commission_eur"]
    notional = buy_price * quantity
    commission_pct = (2 * commission_eur) / notional if notional > 0 else 0

    raw_gain = (current_price - buy_price) / buy_price
    net_gain = raw_gain - commission_pct

    # Hard stop: perdita massima tollerata
    if raw_gain <= -cfg_momentum["hard_stop_pct"]:
        return GuardResult(should_sell=True, reason="hard_stop", net_gain_pct=net_gain)

    # Profit lock: se abbiamo raggiunto il trigger e il prezzo scende dal picco
    hwm = high_water_mark if high_water_mark and high_water_mark > buy_price else buy_price
    gain_at_hwm = (hwm - buy_price) / buy_price
    drop_from_hwm = (hwm - current_price) / hwm if hwm > 0 else 0

    if (gain_at_hwm >= cfg_momentum["profit_lock_trigger_pct"]
            and drop_from_hwm >= cfg_momentum["profit_trail_pct"]):
        return GuardResult(should_sell=True, reason="profit_lock", net_gain_pct=net_gain)

    return GuardResult(should_sell=False, reason="hold", net_gain_pct=net_gain)
