"""Protezione guadagni: trailing stop adattivo + hard stop loss.

Logica:
- Hard stop: se il prezzo scende di hard_stop_pct dal prezzo di acquisto → vendi sempre
- Profit lock: soglia adattiva alla volatilità — asset molto volatile → vende prima in
  profit perché i guadagni svaniscono più rapidamente; asset tranquillo → aspetta di più
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardResult:
    should_sell: bool
    reason: str          # "hard_stop" | "profit_lock" | "hold"
    net_gain_pct: float  # guadagno netto dopo commissioni
    profit_trigger_used: float = 0.0  # soglia effettivamente usata (per log/debug)


def _adaptive_thresholds(
    vol_per_candle: float,
    notional: float,
    commission_eur: float,
) -> tuple[float, float]:
    """Soglie adattive alla volatilità: (take_profit_diretto, trailing_trigger).

    take_profit_diretto: vende immediatamente appena il gain lo supera.
    trailing_trigger: attiva il trailing stop (poi aspetta -trail% dal picco).
    Più il titolo oscilla, più basse sono le soglie — i guadagni svaniscono in fretta.
    La soglia non scende mai sotto 1.5× il costo round-trip delle commissioni.
    """
    commission_breakeven = (2 * commission_eur / notional) if notional > 0 else 0.005
    floor = commission_breakeven * 1.5

    if vol_per_candle < 0.001:       # quasi fermo
        take_profit, trailing = 0.010, 0.012
    elif vol_per_candle < 0.003:     # bassa volatilità
        take_profit, trailing = 0.008, 0.010
    elif vol_per_candle < 0.005:     # media
        take_profit, trailing = 0.006, 0.008
    elif vol_per_candle < 0.010:     # alta
        take_profit, trailing = 0.004, 0.006
    else:                            # molto alta
        take_profit, trailing = 0.003, 0.004

    return max(take_profit, floor), max(trailing, floor)


def check(
    buy_price: float,
    current_price: float,
    high_water_mark: float,
    quantity: int,
    cfg_momentum: dict,
    volatility: float | None = None,  # std rendimenti per candela (da fast_loop)
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
        return GuardResult(should_sell=True, reason="hard_stop",
                           net_gain_pct=net_gain, profit_trigger_used=0.0)

    # Soglie adattive alla volatilità
    if volatility is not None and volatility > 0:
        take_profit_thr, trailing_thr = _adaptive_thresholds(volatility, notional, commission_eur)
    else:
        take_profit_thr = cfg_momentum["profit_lock_trigger_pct"]
        trailing_thr    = cfg_momentum["profit_lock_trigger_pct"]

    hwm = high_water_mark if high_water_mark and high_water_mark > buy_price else buy_price
    gain_at_hwm = (hwm - buy_price) / buy_price
    drop_from_hwm = (hwm - current_price) / hwm if hwm > 0 else 0

    # Take profit diretto: vende subito appena il gain corrente supera la soglia.
    # Non aspetta la discesa — cattura i picchi rapidi.
    if raw_gain >= take_profit_thr:
        return GuardResult(should_sell=True, reason="take_profit",
                           net_gain_pct=net_gain, profit_trigger_used=take_profit_thr)

    # Protezione breakeven: se eravamo in gain significativo e siamo tornati a zero → vendi.
    breakeven_floor = max(commission_pct * 1.5, 0.005)
    if gain_at_hwm >= breakeven_floor and raw_gain <= commission_pct:
        return GuardResult(should_sell=True, reason="breakeven_protection",
                           net_gain_pct=net_gain, profit_trigger_used=breakeven_floor)

    # Profit lock trailing: gain ha raggiunto soglia e poi sceso dal picco
    if gain_at_hwm >= trailing_thr and drop_from_hwm >= cfg_momentum["profit_trail_pct"]:
        return GuardResult(should_sell=True, reason="profit_lock",
                           net_gain_pct=net_gain, profit_trigger_used=trailing_thr)

    return GuardResult(should_sell=False, reason="hold",
                       net_gain_pct=net_gain, profit_trigger_used=take_profit_thr)
