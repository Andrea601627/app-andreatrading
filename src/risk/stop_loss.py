"""Stop-loss ATR-based + trailing stop dopo profitto."""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.config import load_config


@dataclass
class StopLevels:
    stop_loss: float
    take_profit: float | None = None
    method: str = "atr"


def initial_stop(entry_price: float, atr: float | None) -> StopLevels:
    cfg = load_config()["risk"]["stop_loss"]
    if atr and atr > 0:
        sl = entry_price - cfg["atr_multiple"] * atr
        method = "atr"
    else:
        sl = entry_price * (1 - cfg["fallback_pct"])
        method = "pct_fallback"
    # Take profit a 2x rischio (RR 2:1)
    risk = entry_price - sl
    tp = entry_price + 2 * risk
    return StopLevels(stop_loss=round(sl, 4), take_profit=round(tp, 4), method=method)


def update_trailing(
    entry_price: float,
    current_price: float,
    current_stop: float,
    high_water_mark: float | None,
    atr: float | None,
) -> tuple[float, float]:
    """Restituisce (new_stop, new_high_water_mark).

    Attiva trailing solo dopo profitto >= trailing_stop_activate_at_pct.
    """
    cfg = load_config()["risk"]
    activate_at = cfg["trailing_stop_activate_at_pct"]
    atr_mult = cfg["stop_loss"]["atr_multiple"]
    fallback_pct = cfg["stop_loss"]["fallback_pct"]

    hwm = max(high_water_mark or entry_price, current_price)
    profit_pct = (current_price / entry_price) - 1

    if profit_pct < activate_at:
        return current_stop, hwm

    if atr and atr > 0:
        candidate = hwm - atr_mult * atr
    else:
        candidate = hwm * (1 - fallback_pct)

    # lo stop sale ma non scende mai
    new_stop = max(current_stop, candidate)
    return round(new_stop, 4), round(hwm, 4)
