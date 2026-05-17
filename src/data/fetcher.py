"""Fetcher dati di mercato (yfinance) con cache storica permanente e delta update.

Logica:
- Prima volta: scarica 2 anni di storia e salva su disco (parquet permanente)
- Cicli successivi: legge il parquet, scarica solo i giorni mancanti, aggiorna
- Dopo riavvio PC: il parquet è ancora lì, scarica solo il delta
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from ..utils.logger import get_logger
from .cache import (
    last_cached_date,
    read_cache,
    read_price_history,
    write_cache,
    write_price_history,
)

log = get_logger()

_FULL_PERIOD = "2y"
_DELTA_BUFFER_DAYS = 7  # scarica 7 giorni indietro per sicurezza (weekend, festivi)


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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _download_range(ticker: str, start: str, interval: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        start=start,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if new is None or new.empty:
        return old
    combined = pd.concat([old, new])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def get_price_history(
    ticker: str,
    period: str = _FULL_PERIOD,
    interval: str = "1d",
    use_cache: bool = True,
    max_cache_age_seconds: int = 3600,
) -> pd.DataFrame:
    """Scarica prezzi con cache storica permanente + delta update."""

    # Solo per dati giornalieri usiamo la cache storica permanente
    if use_cache and interval == "1d":
        cached = read_price_history(ticker)
        if cached is not None and not cached.empty:
            last_date = cached.index[-1]
            today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
            days_old = (today - last_date.tz_localize(None) if last_date.tzinfo else today - last_date).days

            if days_old <= 1:
                # Dati aggiornati a ieri (o oggi) — nessun download necessario
                return cached

            # Scarica solo il delta dall'ultima data in cache
            start_str = (last_date - timedelta(days=_DELTA_BUFFER_DAYS)).strftime("%Y-%m-%d")
            try:
                new_data = _download_range(ticker, start=start_str, interval=interval)
                if new_data is not None and not new_data.empty:
                    merged = _merge(cached, new_data)
                    write_price_history(ticker, merged)
                    log.debug(f"{ticker}: delta update +{len(new_data)} righe")
                    return merged
            except Exception as e:
                log.warning(f"{ticker}: delta download fallito, uso cache esistente ({e})")
            return cached

    # Prima volta o dati intraday: download completo
    key = f"prices_{ticker}_{period}_{interval}"
    if use_cache and interval != "1d":
        cached_tmp = read_cache(key, max_age_seconds=max_cache_age_seconds)
        if cached_tmp is not None and not cached_tmp.empty:
            return cached_tmp

    try:
        df = _download(ticker, period=period, interval=interval)
        if df is None or df.empty:
            log.warning(f"No data for {ticker}")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)

        if interval == "1d":
            write_price_history(ticker, df)
            log.debug(f"{ticker}: storia completa salvata ({len(df)} righe)")
        else:
            write_cache(key, df)
        return df
    except Exception as e:
        log.error(f"Fetch failed for {ticker}: {e}")
        # Fallback: restituisci cache storica anche se vecchia
        if interval == "1d":
            fallback = read_price_history(ticker)
            if fallback is not None:
                log.warning(f"{ticker}: usando cache storica come fallback")
                return fallback
        return pd.DataFrame()


def get_intraday(ticker: str) -> pd.DataFrame:
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


def get_batch_prices(tickers: list[str], period: str = _FULL_PERIOD) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        out[t] = get_price_history(t, period=period)
    return out


def market_snapshot_timestamp() -> datetime:
    return datetime.utcnow()
