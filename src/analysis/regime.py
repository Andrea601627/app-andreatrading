"""Market regime detector: bull/bear/range + volatilità.

Usato per modulare i pesi dell'aggregator e per gate delle strategie.
Usa FTSE MIB (^FTSEMIB.MI) come proxy del mercato italiano.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.fetcher import get_price_history


@dataclass
class MarketRegime:
    trend: str        # "bull" | "bear" | "range"
    volatility: str   # "low" | "normal" | "high"
    score: float      # -100..+100 (positivo = bullish)
    annualized_vol_pct: float


def detect(benchmark: str = "FTSEMIB.MI") -> MarketRegime:
    df = get_price_history(benchmark, period="1y", max_cache_age_seconds=3600)
    if df.empty or len(df) < 60:
        # fallback: regime neutro se non riesco a leggere il benchmark
        return MarketRegime(trend="range", volatility="normal", score=0.0, annualized_vol_pct=0.0)

    close = df["Close"].astype(float)
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean() if len(close) >= 200 else sma50

    last = float(close.iloc[-1])
    last_sma50 = float(sma50.iloc[-1])
    last_sma200 = float(sma200.iloc[-1])

    # Trend
    if last > last_sma50 > last_sma200:
        trend = "bull"
        trend_score = 60
    elif last < last_sma50 < last_sma200:
        trend = "bear"
        trend_score = -60
    else:
        trend = "range"
        trend_score = (last / last_sma200 - 1) * 100 if last_sma200 else 0

    # Volatilità annualizzata (60 giorni)
    rets = close.pct_change().dropna().tail(60)
    vol = float(rets.std() * np.sqrt(252) * 100) if len(rets) > 10 else 0
    if vol < 12:
        vol_label = "low"
    elif vol < 22:
        vol_label = "normal"
    else:
        vol_label = "high"

    return MarketRegime(
        trend=trend,
        volatility=vol_label,
        score=float(max(-100, min(100, trend_score))),
        annualized_vol_pct=round(vol, 2),
    )
