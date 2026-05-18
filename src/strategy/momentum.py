"""Rilevamento trend su candele 5-minuto per il fast loop.

Logica dual-timeframe:
- Trend giornaliero: prezzo attuale vs apertura di oggi (cattura il trend vero)
- Momentum recente: ultimi N candles (conferma che il trend è ancora attivo)
Entrambi devono essere positivi per un segnale BUY.
"""
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

    if df_1m is None or len(df_1m) < 4:
        return _none

    threshold = cfg_momentum["threshold_pct"]
    strong_thr = cfg_momentum["strong_threshold_pct"]
    vol_mult = cfg_momentum["volume_multiplier"]

    prices = df_1m["Close"].dropna()
    opens  = df_1m["Open"].dropna()
    volumes = df_1m["Volume"].dropna()

    if len(prices) < 2:
        return _none

    current = float(prices.iloc[-1])

    # --- Trend giornaliero: dal prezzo di apertura ad ora ---
    day_open = float(opens.iloc[0])   # prima candela di oggi = apertura
    if day_open <= 0:
        return _none
    day_trend = (current - day_open) / day_open

    # --- Momentum recente: ultimi 3 candles (15 min) ---
    lookback = min(cfg_momentum["lookback_minutes"], len(prices) - 1)
    past = float(prices.iloc[-lookback])
    recent_mom = (current - past) / past if past > 0 else 0.0

    # Segnale: trend giornaliero positivo + momentum recente non in contraddizione
    # (non deve stare già invertendo rispetto al trend del giorno)
    if day_trend > 0 and recent_mom >= -threshold:
        direction = "BUY"
        momentum_pct = day_trend
    elif day_trend < 0 and recent_mom <= threshold:
        direction = "SELL"
        momentum_pct = day_trend
    else:
        return _none

    abs_mom = abs(momentum_pct)
    if abs_mom < threshold:
        return _none

    # Conferma volume
    avg_vol = float(volumes.iloc[-20:].mean()) if len(volumes) >= 20 else float(volumes.mean())
    cur_vol = float(volumes.iloc[-1])
    volume_confirmed = (cur_vol >= avg_vol * vol_mult) if avg_vol > 0 else False

    # Forza del segnale
    if abs_mom >= strong_thr:
        strength = 1.0
        size_pct = cfg_momentum["strong_size_pct"]
    elif abs_mom >= (threshold + strong_thr) / 2:
        strength = 0.65
        size_pct = cfg_momentum["medium_size_pct"]
    else:
        strength = 0.35
        size_pct = cfg_momentum["weak_size_pct"]

    if not volume_confirmed:
        size_pct *= 0.7

    return MomentumSignal(
        ticker=ticker,
        direction=direction,
        strength=strength,
        momentum_pct=momentum_pct,
        volume_confirmed=volume_confirmed,
        size_pct=size_pct,
    )
