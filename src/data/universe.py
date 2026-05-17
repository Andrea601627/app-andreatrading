"""Universo titoli investibili — multi-mercato con reliability score."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from ..utils.config import load_config, project_path

RELIABILITY_MULTIPLIER: dict[str, float] = {
    "high": 1.0,
    "medium_high": 0.85,
    "medium": 0.70,
    "low": 0.50,
}


@dataclass(frozen=True)
class Asset:
    ticker: str
    name: str
    sector: str
    segment: str
    market: str = "Unknown"
    exchange: str = "Unknown"
    reliability: str = "medium"

    def size_multiplier(self) -> float:
        return RELIABILITY_MULTIPLIER.get(self.reliability, 0.70)


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
                market=str(r.get("market", "Unknown")),
                exchange=str(r.get("exchange", "Unknown")),
                reliability=str(r.get("reliability", "medium")),
            ))
    return rows


def tickers() -> list[str]:
    return [a.ticker for a in load_universe()]


def get_asset(ticker: str) -> Asset | None:
    for a in load_universe():
        if a.ticker == ticker:
            return a
    return None
