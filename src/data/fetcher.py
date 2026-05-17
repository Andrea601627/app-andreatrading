"""Fetcher dati di mercato (yfinance) con cache e retry."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.logger import get_logger
from .cache import read_cache, write_cache

log = get_logger()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _download(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def get_price_history(
    ticker: str,
    period: str = "2y",
    interval: str = "1d",
    use_cache: bool = True,
    max_cache_age_seconds: int = 3600,
) -> pd.DataFrame:
    """Scarica prezzi (con cache). Ritorna df vuoto in caso di errore."""
    key = f"prices_{ticker}_{period}_{interval}"
    if use_cache:
        cached = read_cache(key, max_age_seconds=max_cache_age_seconds)
        if cached is not None and not cached.empty:
            return cached
    try:
        df = _download(ticker, period=period, interval=interval)
        if df is None or df.empty:
            log.warning(f"No data for {ticker}")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)
        write_cache(key, df)
        return df
    except Exception as e:
        log.error(f"Fetch failed for {ticker}: {e}")
        return pd.DataFrame()


def get_intraday(ticker: str) -> pd.DataFrame:
    """Ultime barre intraday a 5 minuti (utile per esecuzione)."""
    return get_price_history(ticker, period="5d", interval="5m",
                             use_cache=True, max_cache_age_seconds=300)


def get_last_price(ticker: str) -> float | None:
    df = get_intraday(ticker)
    if df.empty:
        df = get_price_history(ticker, period="5d", interval="1d",
                               max_cache_age_seconds=900)
    if df.empty:
        return None
    return float(df["Close"].iloc[-1])


def get_batch_prices(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        out[t] = get_price_history(t, period=period)
    return out


def market_snapshot_timestamp() -> datetime:
    return datetime.utcnow()
