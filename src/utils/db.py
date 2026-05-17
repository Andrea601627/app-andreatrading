"""SQLite storage per journal, portfolio state, signals, learning."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import load_config, project_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,                 -- BUY | SELL
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    commission REAL NOT NULL DEFAULT 0,
    horizon TEXT NOT NULL,              -- short_medium | long_term
    score REAL,
    reason_json TEXT,                   -- snapshot completo (motivazioni)
    market_regime TEXT,
    executed_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    close_price REAL,
    pnl REAL,
    pnl_pct REAL,
    close_reason TEXT,                  -- target | stop_loss | signal | drawdown_guard | manual
    error_class TEXT                    -- popolato dal postmortem
);

CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades(executed_at);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    quantity REAL NOT NULL,
    avg_price REAL NOT NULL,
    horizon TEXT NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    high_water_mark REAL,
    trade_id INTEGER REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    generated_at TIMESTAMP NOT NULL,
    score REAL NOT NULL,
    decision TEXT NOT NULL,             -- STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL
    horizon TEXT NOT NULL,
    components_json TEXT,               -- breakdown per dimensione
    weights_json TEXT,
    regime TEXT,
    quality_ok INTEGER NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at ON signals(generated_at);

CREATE TABLE IF NOT EXISTS equity_curve (
    timestamp TIMESTAMP PRIMARY KEY,
    cash REAL NOT NULL,
    positions_value REAL NOT NULL,
    total_equity REAL NOT NULL,
    drawdown_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,           -- weight_adjust | strategy_retire | threshold_update
    target TEXT NOT NULL,               -- es: "short_medium.technical" o "BAMI.MI"
    old_value REAL,
    new_value REAL,
    rationale TEXT,
    sample_size INTEGER,
    p_value REAL,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    approved INTEGER
);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at TIMESTAMP NOT NULL,
    reason TEXT NOT NULL,
    equity_at_trigger REAL NOT NULL,
    peak_equity REAL NOT NULL,
    drawdown_pct REAL NOT NULL,
    resolved_at TIMESTAMP,
    resolved_by TEXT
);
"""


def _db_path() -> Path:
    cfg = load_config()
    p = project_path(cfg["storage"]["db_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(_db_path(), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
