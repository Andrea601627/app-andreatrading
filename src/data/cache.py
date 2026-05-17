"""Cache locale dei dati su disco (parquet).

Due livelli:
- Cache temporanea (max_age_seconds): per dati che scadono (intraday, fondamentali)
- Cache storica permanente (prices): non scade mai, viene aggiornata con delta
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from ..utils.config import load_config, project_path


def _cache_dir() -> Path:
    p = project_path(load_config()["storage"]["cache_path"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_path(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_")
    return _cache_dir() / f"{safe}.parquet"


def read_cache(key: str, max_age_seconds: int = 86400) -> pd.DataFrame | None:
    p = cache_path(key)
    if not p.exists():
        return None
    if (time.time() - p.stat().st_mtime) > max_age_seconds:
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def write_cache(key: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    try:
        df.to_parquet(cache_path(key))
    except Exception:
        pass


# --- Cache storica permanente per prezzi giornalieri ---

def _prices_dir() -> Path:
    p = _cache_dir() / "prices"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _price_file(ticker: str) -> Path:
    safe = ticker.replace("/", "_").replace(":", "_")
    return _prices_dir() / f"{safe}.parquet"


def read_price_history(ticker: str) -> pd.DataFrame | None:
    """Legge la storia prezzi dalla cache permanente. Nessuna scadenza."""
    p = _price_file(ticker)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception:
        return None


def write_price_history(ticker: str, df: pd.DataFrame) -> None:
    """Salva la storia prezzi in cache permanente."""
    if df is None or df.empty:
        return
    try:
        df.to_parquet(_price_file(ticker))
    except Exception:
        pass


def last_cached_date(ticker: str) -> pd.Timestamp | None:
    """Ritorna l'ultima data disponibile nella cache storica."""
    df = read_price_history(ticker)
    if df is None or df.empty:
        return None
    return df.index[-1]
