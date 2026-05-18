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
    """Trend following: compra se il titolo è in trend positivo oggi.

    Logica:
    - Confronta prezzo attuale con il prezzo di chiusura di ieri (prima candela del df)
    - Se è salito → BUY (il trend della watchlist continua oggi)
    - Se è sceso → SELL (il trend si è invertito)
    - Forza proporzionale all'entità del movimento
    Il filtro principale è lo screener (watchlist): qui si valida solo la direzione.
    """
    _none = MomentumSignal(ticker=ticker, direction="NONE", strength=0.0,
                           momentum_pct=0.0, volume_confirmed=False, size_pct=0.0)

    if df_1m is None or len(df_1m) < 2:
        return _none

    strong_thr = cfg_momentum["strong_threshold_pct"]
    vol_mult   = cfg_momentum["volume_multiplier"]

    prices  = df_1m["Close"].dropna()
    volumes = df_1m["Volume"].dropna()

    if len(prices) < 2:
        return _none

    current    = float(prices.iloc[-1])
    prev_close = float(prices.iloc[0])   # prima candela disponibile oggi = riferimento
    if prev_close <= 0:
        return _none

    momentum_pct = (current - prev_close) / prev_close

    # Direzione: basta che sia positivo o negativo, nessuna soglia minima
    if momentum_pct > 0:
        direction = "BUY"
    elif momentum_pct < 0:
        direction = "SELL"
    else:
        return _none

    abs_mom = abs(momentum_pct)

    # Conferma volume (riduce sizing se assente, non blocca)
    avg_vol = float(volumes.iloc[-20:].mean()) if len(volumes) >= 20 else float(volumes.mean())
    cur_vol = float(volumes.iloc[-1])
    volume_confirmed = (cur_vol >= avg_vol * vol_mult) if avg_vol > 0 else False

    # Forza del segnale in base all'entità del movimento
    if abs_mom >= strong_thr:
        strength = 1.0
        size_pct = cfg_momentum["strong_size_pct"]
    elif abs_mom >= strong_thr / 2:
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
