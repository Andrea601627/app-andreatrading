"""Analisi tecnica multi-indicatore -> score [-100, +100].

Indicatori:
- Trend: EMA20/50/200, ADX
- Momentum: RSI(14), MACD, ROC
- Volatilità: Bollinger Bands, ATR
- Volume: OBV trend
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, ROCIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator


@dataclass
class TechnicalResult:
    score: float
    details: dict = field(default_factory=dict)
    indicators: dict = field(default_factory=dict)
    confidence: float = 1.0   # 0..1, ridotto se serie troppo corta o NaN


def _clip(x: float, lo: float = -100, hi: float = 100) -> float:
    return float(max(lo, min(hi, x)))


def compute(df: pd.DataFrame) -> TechnicalResult:
    """Calcola tutti gli indicatori e aggrega in uno score."""
    if df is None or df.empty or len(df) < 60:
        return TechnicalResult(score=0.0, details={"reason": "insufficient_data"}, confidence=0.0)

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)

    # --- Trend ---
    ema20 = EMAIndicator(close=close, window=20).ema_indicator()
    ema50 = EMAIndicator(close=close, window=50).ema_indicator()
    ema200 = EMAIndicator(close=close, window=200).ema_indicator() if len(close) >= 200 else pd.Series([np.nan]*len(close), index=close.index)
    adx = ADXIndicator(high=high, low=low, close=close, window=14).adx()

    # --- Momentum ---
    rsi = RSIIndicator(close=close, window=14).rsi()
    macd = MACD(close=close)
    macd_line = macd.macd()
    macd_signal = macd.macd_signal()
    macd_diff = macd.macd_diff()
    roc = ROCIndicator(close=close, window=20).roc()

    # --- Volatilità ---
    bb = BollingerBands(close=close, window=20, window_dev=2)
    bb_high = bb.bollinger_hband()
    bb_low = bb.bollinger_lband()
    bb_pct = bb.bollinger_pband()  # 0..1 dove sta il prezzo nella banda
    atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    # --- Volume ---
    obv = OnBalanceVolumeIndicator(close=close, volume=vol).on_balance_volume()
    obv_slope = (obv.iloc[-1] - obv.iloc[-20]) / (abs(obv.iloc[-20]) + 1e-9) if len(obv) >= 20 else 0

    last_close = close.iloc[-1]
    last_ema20 = ema20.iloc[-1]
    last_ema50 = ema50.iloc[-1]
    last_ema200 = ema200.iloc[-1] if not np.isnan(ema200.iloc[-1]) else last_ema50
    last_rsi = rsi.iloc[-1]
    last_macd_diff = macd_diff.iloc[-1]
    last_adx = adx.iloc[-1] if not np.isnan(adx.iloc[-1]) else 0
    last_roc = roc.iloc[-1]
    last_bb_pct = bb_pct.iloc[-1] if not np.isnan(bb_pct.iloc[-1]) else 0.5

    # --- Sub-score (-100..+100) ---
    # Trend: prezzo vs EMA + ordine delle EMA
    trend_score = 0
    if last_close > last_ema20: trend_score += 20
    if last_close > last_ema50: trend_score += 20
    if last_close > last_ema200: trend_score += 30
    if last_ema20 > last_ema50: trend_score += 15
    if last_ema50 > last_ema200: trend_score += 15
    trend_score -= 100 if last_close < last_ema200 and last_ema20 < last_ema50 else 0
    trend_score = _clip(trend_score)

    # ADX modulator: rafforza il trend se ADX > 25
    trend_strength = min(last_adx / 40.0, 1.0)
    trend_score *= (0.5 + 0.5 * trend_strength)

    # Momentum: RSI + MACD + ROC
    rsi_score = 0
    if last_rsi < 30: rsi_score = +60          # oversold -> potenziale buy
    elif last_rsi < 45: rsi_score = +25
    elif last_rsi > 70: rsi_score = -60        # overbought -> potenziale sell
    elif last_rsi > 55: rsi_score = -15
    macd_score = _clip(last_macd_diff / (abs(macd_line.iloc[-1]) + 1e-6) * 100)
    roc_score = _clip(last_roc * 4)            # ROC 20d, scale ~25% -> 100
    momentum_score = _clip(0.4 * rsi_score + 0.4 * macd_score + 0.2 * roc_score)

    # Volatilità / mean reversion: prezzo vicino alla banda bassa = buy bias
    vol_score = _clip((0.5 - last_bb_pct) * 200)  # bb_pct=0 -> +100, bb_pct=1 -> -100

    # Volume: OBV trending up = supporto al trend
    volume_score = _clip(obv_slope * 50)

    # Aggregazione finale (pesi interni)
    final = (
        0.35 * trend_score +
        0.35 * momentum_score +
        0.15 * vol_score +
        0.15 * volume_score
    )
    final = _clip(final)

    # Confidence: ridotta se mancano serie lunghe o se valori NaN
    confidence = 1.0
    if len(close) < 200: confidence *= 0.85
    if np.isnan(last_adx) or last_adx == 0: confidence *= 0.9

    return TechnicalResult(
        score=final,
        details={
            "trend_score": round(trend_score, 2),
            "momentum_score": round(momentum_score, 2),
            "volatility_score": round(vol_score, 2),
            "volume_score": round(volume_score, 2),
        },
        indicators={
            "close": round(float(last_close), 4),
            "ema20": round(float(last_ema20), 4),
            "ema50": round(float(last_ema50), 4),
            "ema200": round(float(last_ema200), 4),
            "rsi14": round(float(last_rsi), 2),
            "macd_diff": round(float(last_macd_diff), 4),
            "adx14": round(float(last_adx), 2),
            "atr14": round(float(atr.iloc[-1]), 4),
            "bb_pct": round(float(last_bb_pct), 3),
            "obv_slope_20d": round(float(obv_slope), 4),
            "roc_20d": round(float(last_roc), 2),
        },
        confidence=confidence,
    )
