"""Universo titoli investibili (FTSE MIB + estensioni future)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from ..utils.config import load_config, project_path


@dataclass(frozen=True)
class Asset:
    ticker: str
    name: str
    sector: str
    segment: str


@lru_cache(maxsize=1)
def load_universe() -> list[Asset]:
    cfg = load_config()
    rows: list[Asset] = []
    seen: set[str] = set()
    for rel_path in cfg["universe"]["files"]:
        df = pd.read_csv(project_path(rel_path))
        for _, r in df.iterrows():
            t = str(r["ticker"]).strip()
            if not t or t in seen:
                continue
            seen.add(t)
            rows.append(Asset(
                ticker=t,
                name=str(r.get("name", t)),
                sector=str(r.get("sector", "Unknown")),
                segment=str(r.get("segment", "Unknown")),
            ))
    return rows


def tickers() -> list[str]:
    return [a.ticker for a in load_universe()]


def get_asset(ticker: str) -> Asset | None:
    for a in load_universe():
        if a.ticker == ticker:
            return a
    return None
