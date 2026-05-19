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


def _adaptive_profit_trigger(
    vol_per_candle: float,
    notional: float,
    commission_eur: float,
) -> float:
    """Soglia profit lock proporzionale alla volatilità dell'asset.

    Più il titolo oscilla per candela, prima accettiamo un profitto minore
    (perché i guadagni rischiano di svanire velocemente).
    La soglia non scende mai sotto 1.5× il costo round-trip delle commissioni.
    """
    commission_breakeven = (2 * commission_eur / notional) if notional > 0 else 0.005

    if vol_per_candle < 0.001:       # quasi fermo  → aspetta +1.2%
        base = 0.012
    elif vol_per_candle < 0.003:     # bassa        → aspetta +1.0%
        base = 0.010
    elif vol_per_candle < 0.005:     # media        → aspetta +0.7%
        base = 0.007
    elif vol_per_candle < 0.010:     # alta         → vendi a +0.5%
        base = 0.005
    else:                            # molto alta   → vendi a +0.35%
        base = 0.0035

    return max(base, commission_breakeven * 1.5)


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

    # Soglia profit lock: adattiva se abbiamo la volatilità, fissa altrimenti
    if volatility is not None and volatility > 0:
        trigger = _adaptive_profit_trigger(volatility, notional, commission_eur)
    else:
        trigger = cfg_momentum["profit_lock_trigger_pct"]

    hwm = high_water_mark if high_water_mark and high_water_mark > buy_price else buy_price
    gain_at_hwm = (hwm - buy_price) / buy_price
    drop_from_hwm = (hwm - current_price) / hwm if hwm > 0 else 0

    # Protezione breakeven: se la posizione era mai salita abbastanza da coprire
    # le commissioni (>= 0.5%) e ora è tornata a zero o in perdita → vendi subito.
    # Evita di trasformare un guadagno mancato in una perdita reale.
    breakeven_trigger = max(commission_pct * 1.5, 0.005)  # almeno 0.5%
    if gain_at_hwm >= breakeven_trigger and raw_gain <= commission_pct:
        return GuardResult(should_sell=True, reason="breakeven_protection",
                           net_gain_pct=net_gain, profit_trigger_used=breakeven_trigger)

    # Profit lock trailing: gain ha raggiunto soglia e poi sceso dal picco
    if gain_at_hwm >= trigger and drop_from_hwm >= cfg_momentum["profit_trail_pct"]:
        return GuardResult(should_sell=True, reason="profit_lock",
                           net_gain_pct=net_gain, profit_trigger_used=trigger)

    return GuardResult(should_sell=False, reason="hold",
                       net_gain_pct=net_gain, profit_trigger_used=trigger)
