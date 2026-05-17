"""Backtest event-driven semplice per validare le strategie sui dati storici.

Walk-forward: per ogni giorno T della finestra, calcola i segnali usando
solo i dati fino a T-1 e simula trade per T. Misura Sharpe, max DD, hit-rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from ..analysis import ml_forecast as ml_mod
from ..analysis import sentiment as sa_mod
from ..analysis import technical as ta_mod
from ..analysis.regime import MarketRegime
from ..data.fetcher import get_price_history
from ..strategy.aggregator import aggregate
from ..strategy.decision import decide
from ..utils.logger import get_logger

log = get_logger()


@dataclass
class BacktestResult:
    n_trades: int
    win_rate: float
    avg_return: float
    total_return: float
    sharpe: float
    max_drawdown: float
    equity_curve: pd.Series


def backtest_ticker(
    ticker: str,
    start: str = "2020-01-01",
    end: str | None = None,
    initial_capital: float = 10000.0,
    rebalance_days: int = 5,
) -> BacktestResult | None:
    df = get_price_history(ticker, period="max")
    if df.empty or len(df) < 250:
        return None
    df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if len(df) < 100:
        return None

    cash = initial_capital
    position = 0
    entry_price = 0.0
    equity = []
    trades = []

    # Sentiment + fundamental in backtest sono troppo costosi / non disponibili
    # storicamente. Usiamo solo technical + ml_forecast come baseline.
    from ..analysis.fundamental import FundamentalResult
    fund_empty = FundamentalResult(score=0, details={}, confidence=0)
    sent_empty = sa_mod.SentimentResult(score=0, details={}, confidence=0)
    regime = MarketRegime(trend="range", volatility="normal", score=0,
                           annualized_vol_pct=15)

    for i in range(200, len(df), rebalance_days):
        window = df.iloc[: i]
        last_price = float(window["Close"].iloc[-1])

        tech = ta_mod.compute(window)
        fc = ml_mod.compute(window, horizon_days=rebalance_days)
        agg = aggregate("short_medium", tech, fund_empty, fc, sent_empty,
                         regime=regime)
        dec = decide(agg)

        if dec.action in ("BUY", "STRONG_BUY") and position == 0:
            qty = int(cash // last_price)
            if qty > 0:
                cost = qty * last_price
                cash -= cost
                position = qty
                entry_price = last_price
        elif dec.action in ("SELL", "STRONG_SELL") and position > 0:
            proceeds = position * last_price
            pnl_pct = (last_price / entry_price) - 1
            trades.append(pnl_pct)
            cash += proceeds
            position = 0
            entry_price = 0

        equity.append({"date": df.index[i], "equity": cash + position * last_price})

    # liquida ultima posizione
    if position > 0:
        last_price = float(df["Close"].iloc[-1])
        cash += position * last_price
        trades.append((last_price / entry_price) - 1)
        equity.append({"date": df.index[-1], "equity": cash})

    if not equity:
        return None
    eq_df = pd.DataFrame(equity).set_index("date")["equity"]
    returns = eq_df.pct_change().dropna()
    sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
    max_dd = float(((eq_df / eq_df.cummax()) - 1).min())

    return BacktestResult(
        n_trades=len(trades),
        win_rate=float(np.mean([1 if t > 0 else 0 for t in trades])) if trades else 0,
        avg_return=float(np.mean(trades)) if trades else 0,
        total_return=float(eq_df.iloc[-1] / initial_capital - 1),
        sharpe=sharpe,
        max_drawdown=max_dd,
        equity_curve=eq_df,
    )
