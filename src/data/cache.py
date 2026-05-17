"""Cache locale dei dati su disco (parquet)."""
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
