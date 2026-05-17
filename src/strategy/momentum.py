"""Rilevamento momentum su candele a 1 minuto per il fast loop."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class MomentumSignal:
    ticker: str
    direction: str       # "BUY" | "SELL" | "NONE"
    strength: float      # 0.0 - 1.0
    momentum_pct: float
    volume_confirmed: bool
    size_pct: float      # % del capitale da allocare


def detect(ticker: str, df_1m: pd.DataFrame, cfg_momentum: dict) -> MomentumSignal:
    _none = MomentumSignal(ticker=ticker, direction="NONE", strength=0.0,
                           momentum_pct=0.0, volume_confirmed=False, size_pct=0.0)

    if df_1m is None or len(df_1m) < 12:
        return _none

    lookback = cfg_momentum["lookback_minutes"]
    threshold = cfg_momentum["threshold_pct"]
    strong_thr = cfg_momentum["strong_threshold_pct"]
    vol_mult = cfg_momentum["volume_multiplier"]

    prices = df_1m["Close"].dropna()
    volumes = df_1m["Volume"].dropna()

    if len(prices) < lookback + 1:
        return _none

    current = float(prices.iloc[-1])
    past = float(prices.iloc[-lookback])
    if past <= 0:
        return _none

    momentum_pct = (current - past) / past
    abs_mom = abs(momentum_pct)

    # Conferma volume: il movimento deve essere sostenuto da volumi sopra la media
    avg_vol = float(volumes.iloc[-20:].mean()) if len(volumes) >= 20 else float(volumes.mean())
    cur_vol = float(volumes.iloc[-1])
    volume_confirmed = (cur_vol >= avg_vol * vol_mult) if avg_vol > 0 else False

    if abs_mom < threshold or not volume_confirmed:
        return _none

    # Forza del segnale e sizing
    if abs_mom >= strong_thr:
        strength = 1.0
        size_pct = cfg_momentum["strong_size_pct"]
    elif abs_mom >= (threshold + strong_thr) / 2:
        strength = 0.65
        size_pct = cfg_momentum["medium_size_pct"]
    else:
        strength = 0.35
        size_pct = cfg_momentum["weak_size_pct"]

    return MomentumSignal(
        ticker=ticker,
        direction="BUY" if momentum_pct > 0 else "SELL",
        strength=strength,
        momentum_pct=momentum_pct,
        volume_confirmed=volume_confirmed,
        size_pct=size_pct,
    )
