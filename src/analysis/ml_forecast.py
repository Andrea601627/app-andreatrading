"""Forecast a breve termine (5-20 giorni) -> score [-100, +100].

Modello principale: Holt-Winters exponential smoothing (robusto, no dipendenze pesanti).
Modello secondario: linear regression sui rendimenti recenti come sanity check.

NB: i forecast su prezzi singoli hanno potere predittivo modesto out-of-sample.
Per questo il peso assegnato nel decision aggregator è basso (10-15%).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.holtwinters import ExponentialSmoothing


@dataclass
class ForecastResult:
    score: float
    details: dict = field(default_factory=dict)
    confidence: float = 1.0


def _clip(x: float, lo: float = -100, hi: float = 100) -> float:
    return float(max(lo, min(hi, x)))


def compute(df: pd.DataFrame, horizon_days: int = 10) -> ForecastResult:
    if df is None or df.empty or len(df) < 60:
        return ForecastResult(score=0.0, details={"reason": "insufficient_data"}, confidence=0.0)

    close = df["Close"].astype(float).dropna()
    if len(close) < 60:
        return ForecastResult(score=0.0, details={"reason": "insufficient_data"}, confidence=0.0)

    last = float(close.iloc[-1])

    # --- Holt-Winters exponential smoothing ---
    hw_pred = None
    try:
        # serie giornaliera; trend additive, no seasonality forzata
        model = ExponentialSmoothing(
            close.values, trend="add", seasonal=None, initialization_method="estimated"
        ).fit(optimized=True, use_brute=False)
        hw_pred = float(model.forecast(horizon_days)[-1])
    except Exception:
        pass

    # --- Linear regression sui rendimenti (sanity check) ---
    lr_pred = None
    try:
        n = min(60, len(close))
        y = close.tail(n).values
        X = np.arange(n).reshape(-1, 1)
        lr = LinearRegression().fit(X, y)
        lr_pred = float(lr.predict(np.array([[n + horizon_days]]))[0])
    except Exception:
        pass

    # Ensemble semplice
    preds = [p for p in (hw_pred, lr_pred) if p is not None and np.isfinite(p)]
    if not preds:
        return ForecastResult(score=0.0, details={"reason": "models_failed"}, confidence=0.0)

    avg_pred = float(np.mean(preds))
    expected_return = (avg_pred / last) - 1.0  # es 0.04 = +4%

    # Score: scala lineare, 5% atteso -> ~+60
    score = _clip(expected_return * 1200)

    # Confidence: maggiore se i due modelli concordano sul segno
    agree = (hw_pred is not None and lr_pred is not None and
             ((hw_pred - last) * (lr_pred - last) > 0))
    confidence = 0.8 if agree else 0.5
    if len(preds) == 1:
        confidence = 0.4

    return ForecastResult(
        score=score,
        details={
            "horizon_days": horizon_days,
            "last_price": round(last, 4),
            "hw_forecast": round(hw_pred, 4) if hw_pred is not None else None,
            "lr_forecast": round(lr_pred, 4) if lr_pred is not None else None,
            "ensemble_forecast": round(avg_pred, 4),
            "expected_return_pct": round(expected_return * 100, 2),
            "models_agree": agree,
        },
        confidence=confidence,
    )
