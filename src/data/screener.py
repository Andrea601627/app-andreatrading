"""Screener veloce: ordina tutti i ticker per rendimento recente e ritorna i top N.

Usa la cache parquet locale (dati giornalieri) — nessun download durante lo screening.
Aggiornato dal fetcher come parte del ciclo normale.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .cache import read_price_history
from .universe import tickers as all_tickers
from ..utils.logger import get_logger

log = get_logger()


@dataclass
class ScreenerResult:
    ticker: str
    return_5d: float    # rendimento % ultimi 5 giorni
    return_1d: float    # rendimento % ieri
    cached: bool        # dati disponibili in cache


def run(top_n: int = 100, lookback_days: int = 5) -> list[ScreenerResult]:
    """Ordina tutti i ticker per rendimento recente, ritorna i top_n.

    Usa solo cache locale — nessuna chiamata di rete.
    """
    results: list[ScreenerResult] = []

    for ticker in all_tickers():
        df = read_price_history(ticker)
        if df is None or len(df) < 2:
            results.append(ScreenerResult(ticker=ticker, return_5d=0.0,
                                           return_1d=0.0, cached=False))
            continue

        try:
            close = df["Close"].dropna()
            if len(close) < 2:
                results.append(ScreenerResult(ticker=ticker, return_5d=0.0,
                                               return_1d=0.0, cached=False))
                continue

            ret_1d = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2])

            lookback = min(lookback_days, len(close) - 1)
            ret_5d = float((close.iloc[-1] - close.iloc[-1 - lookback])
                           / close.iloc[-1 - lookback])

            results.append(ScreenerResult(ticker=ticker, return_5d=ret_5d,
                                           return_1d=ret_1d, cached=True))
        except Exception as e:
            log.debug(f"Screener error {ticker}: {e}")
            results.append(ScreenerResult(ticker=ticker, return_5d=0.0,
                                           return_1d=0.0, cached=False))

    # ordina per rendimento 5 giorni decrescente
    results.sort(key=lambda r: r.return_5d, reverse=True)

    cached_count = sum(1 for r in results if r.cached)
    log.info(f"Screener: {len(results)} titoli, {cached_count} con dati cache, "
             f"top {top_n} selezionati")

    return results[:top_n]


def top_tickers(top_n: int = 100) -> list[str]:
    """Ritorna solo i ticker dei top N per rendimento recente."""
    return [r.ticker for r in run(top_n=top_n)]
