"""Trade journal: scrive snapshot completo della decisione."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..utils.db import connect


def log_signal(ticker: str, decision, agg, regime, quality_ok: bool,
                notes: str = "") -> None:
    components = {k: v for k, v in agg.components.items()} if agg else {}
    weights = agg.weights_used if agg else {}
    with connect() as c:
        c.execute("""
            INSERT INTO signals (ticker, generated_at, score, decision, horizon,
                                  components_json, weights_json, regime,
                                  quality_ok, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, datetime.utcnow(),
            decision.score if decision else 0.0,
            decision.action if decision else "HOLD",
            decision.horizon if decision else "short_medium",
            json.dumps(components),
            json.dumps(weights),
            f"{regime.trend}/{regime.volatility}" if regime else "unknown",
            int(quality_ok),
            notes,
        ))


def get_open_trades() -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("""
            SELECT * FROM trades
            WHERE side='BUY' AND closed_at IS NULL
            ORDER BY executed_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_closed_trades(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("""
            SELECT * FROM trades
            WHERE closed_at IS NOT NULL AND side='SELL'
            ORDER BY closed_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_recent_signals(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("""
            SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]
