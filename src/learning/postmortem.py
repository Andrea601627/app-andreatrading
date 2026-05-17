"""Classifica ogni trade chiuso in una categoria di errore.

Categorie:
  - signal_error: il segnale era sbagliato (tecnica/fondamentale dava buy ma è andato giù)
  - regime_mismatch: strategia inadatta al regime di mercato
  - black_swan: variazione estrema (>15%) tra entrata e uscita per evento esterno
  - execution_error: slippage o timing esecuzione fuori scala
  - risk_sizing_error: stop loss colpito da rumore (volatilità normale lo prevedeva)
  - bad_luck: decisione corretta ex-ante, esito negativo per rumore -> nessuna correzione
  - successful_trade: P&L positivo, no errore
"""
from __future__ import annotations

import json
from datetime import datetime

from ..utils.db import connect
from ..utils.logger import get_logger

log = get_logger()


def classify_trade(trade: dict) -> str:
    pnl_pct = trade.get("pnl_pct") or 0
    close_reason = (trade.get("close_reason") or "").lower()
    reason_json = trade.get("reason_json") or "{}"
    try:
        reason = json.loads(reason_json)
    except Exception:
        reason = {}

    if pnl_pct > 0:
        return "successful_trade"

    # Black swan: perdita >15% in tempi rapidi
    if pnl_pct < -0.15:
        return "black_swan"

    # Stop loss hit
    if close_reason == "stop_loss":
        # rumore normale (vol ~ 2% giornaliera) -> sizing/stop troppo stretto
        if -0.10 <= pnl_pct < -0.03:
            return "risk_sizing_error"

    # Cambio segnale: il modello aveva ragione ad uscire? se ancora pnl negativo,
    # probabile signal_error originario
    if close_reason in ("signal", "target"):
        return "signal_error"

    # regime mismatch: se in entry era bull e ora bear (o viceversa)
    regime_entry = (trade.get("market_regime") or "").split("/")[0]
    if regime_entry and regime_entry in ("bull", "bear"):
        # heuristic: se la perdita è moderata ed eravamo in bull, mercato è cambiato
        if regime_entry == "bull" and pnl_pct < -0.05:
            return "regime_mismatch"

    # Bad luck: piccola perdita, decisione sembrava ragionevole
    if -0.03 <= pnl_pct < 0:
        return "bad_luck"

    return "signal_error"


def run_postmortem() -> int:
    """Classifica tutti i trade chiusi senza error_class. Ritorna count."""
    count = 0
    with connect() as c:
        rows = c.execute("""
            SELECT * FROM trades
            WHERE side='SELL' AND closed_at IS NOT NULL AND error_class IS NULL
        """).fetchall()
        for r in rows:
            d = dict(r)
            cls = classify_trade(d)
            c.execute("UPDATE trades SET error_class=? WHERE id=?", (cls, d["id"]))
            count += 1
            log.info(f"Postmortem trade #{d['id']} {d['ticker']}: {cls} pnl={d.get('pnl_pct', 0):.2%}")
    return count


def error_class_summary() -> dict[str, int]:
    with connect() as c:
        rows = c.execute("""
            SELECT error_class, COUNT(*) AS n FROM trades
            WHERE error_class IS NOT NULL
            GROUP BY error_class
        """).fetchall()
    return {r["error_class"]: r["n"] for r in rows}
