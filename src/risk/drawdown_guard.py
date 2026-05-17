"""Circuit breaker globale sul drawdown del portafoglio.

Se l'equity scende oltre la soglia dal picco storico, il sistema:
  - chiude tutte le posizioni (paper/live)
  - blocca nuovi ingressi
  - richiede sblocco esplicito dell'utente

Garantisce, insieme al vincolo cash-only, che la perdita massima resta
limitata al capitale investito.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..utils.config import load_config
from ..utils.db import connect


@dataclass
class GuardStatus:
    triggered: bool
    drawdown_pct: float
    peak_equity: float
    current_equity: float
    reason: str | None = None


def peak_equity() -> float:
    with connect() as c:
        row = c.execute("SELECT MAX(total_equity) AS m FROM equity_curve").fetchone()
        return float(row["m"] or 0.0)


def is_blocked() -> bool:
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM circuit_breaker WHERE resolved_at IS NULL LIMIT 1"
        ).fetchone()
        return row is not None


def check(current_equity: float) -> GuardStatus:
    cfg = load_config()["risk"]
    threshold = cfg["drawdown_circuit_breaker_pct"]
    peak = max(peak_equity(), current_equity)
    if peak <= 0:
        return GuardStatus(triggered=False, drawdown_pct=0.0,
                           peak_equity=peak, current_equity=current_equity)
    dd = (peak - current_equity) / peak
    triggered = dd >= threshold
    return GuardStatus(
        triggered=triggered,
        drawdown_pct=dd,
        peak_equity=peak,
        current_equity=current_equity,
        reason=f"drawdown {dd:.2%} >= {threshold:.2%}" if triggered else None,
    )


def trip(status: GuardStatus) -> None:
    with connect() as c:
        c.execute("""
            INSERT INTO circuit_breaker (triggered_at, reason, equity_at_trigger,
                                          peak_equity, drawdown_pct)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.utcnow(), status.reason, status.current_equity,
              status.peak_equity, status.drawdown_pct))


def resolve(resolved_by: str = "user") -> None:
    with connect() as c:
        c.execute("""
            UPDATE circuit_breaker
            SET resolved_at = ?, resolved_by = ?
            WHERE resolved_at IS NULL
        """, (datetime.utcnow(), resolved_by))


def record_equity(cash: float, positions_value: float) -> float:
    """Salva punto della equity curve e ritorna l'equity totale."""
    total = cash + positions_value
    peak = max(peak_equity(), total)
    dd = (peak - total) / peak if peak > 0 else 0
    with connect() as c:
        c.execute("""
            INSERT OR REPLACE INTO equity_curve
                (timestamp, cash, positions_value, total_equity, drawdown_pct)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.utcnow(), cash, positions_value, total, dd))
    return total
