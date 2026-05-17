"""Validazione qualità dati - se non passano i check, niente trade.

Principio: meglio non operare che operare su dati sporchi.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..utils.config import load_config


@dataclass
class QualityReport:
    ticker: str
    ok: bool
    issues: list[str] = field(default_factory=list)
    rows: int = 0
    missing_pct: float = 0.0
    max_gap_days: int = 0
    last_date: str | None = None

    def fail(self, msg: str) -> None:
        self.ok = False
        self.issues.append(msg)


def validate_price_history(ticker: str, df: pd.DataFrame) -> QualityReport:
    """Verifica che la serie storica sia utilizzabile per analisi/trading."""
    cfg = load_config()["data_quality"]
    rep = QualityReport(ticker=ticker, ok=True, rows=len(df) if df is not None else 0)

    if df is None or df.empty:
        rep.fail("empty_dataframe")
        return rep

    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        rep.fail(f"missing_columns:{sorted(missing_cols)}")
        return rep

    if len(df) < cfg["min_history_days"]:
        rep.fail(f"insufficient_history:{len(df)}<{cfg['min_history_days']}")

    # Valori mancanti
    missing_pct = df[list(required_cols)].isna().mean().max()
    rep.missing_pct = float(missing_pct)
    if missing_pct > cfg["max_missing_pct"]:
        rep.fail(f"too_many_missing:{missing_pct:.3%}")

    # Gap temporali (giorni di mercato consecutivi mancanti)
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
        diffs = df.index.to_series().diff().dt.days.dropna()
        # ignoriamo i weekend (gap 3 giorni venerdì→lunedì è normale)
        max_gap = int(diffs.max()) if len(diffs) else 0
        rep.max_gap_days = max_gap
        if max_gap > cfg["max_gap_days"] + 2:  # tolleranza weekend
            rep.fail(f"large_gap:{max_gap}d")
        rep.last_date = df.index[-1].strftime("%Y-%m-%d")

    # Valori non finiti o negativi
    if not np.isfinite(df["Close"].iloc[-1]):
        rep.fail("last_close_not_finite")
    if (df["Close"] <= 0).any():
        rep.fail("non_positive_price")
    if (df["Volume"] < 0).any():
        rep.fail("negative_volume")

    # Outlier estremi: variazione > 50% in un singolo giorno = sospetto
    returns = df["Close"].pct_change().abs()
    if (returns > 0.5).any():
        rep.fail(f"extreme_outlier:{returns.max():.2%}")

    # Prezzo "congelato" (stesso valore per troppi giorni di fila → fonte rotta)
    same_run = (df["Close"].diff() == 0).astype(int)
    if same_run.rolling(window=10).sum().max() >= 10:
        rep.fail("frozen_price_10d")

    return rep


def validate_fundamentals(ticker: str, fundamentals: dict | None) -> QualityReport:
    """Validazione metriche fondamentali."""
    rep = QualityReport(ticker=ticker, ok=True)
    if not fundamentals:
        rep.fail("no_fundamentals")
        return rep
    # Almeno alcune metriche chiave devono esserci
    required_any = ["market_cap", "pe_ratio", "trailing_eps", "dividend_yield"]
    if not any(fundamentals.get(k) not in (None, 0) for k in required_any):
        rep.fail("no_key_fundamentals")
    return rep
