"""Allocazione capitale per orizzonte (short_medium 80% / long_term 20%)."""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.config import load_config


@dataclass
class CapitalAllocation:
    total_capital: float
    cash_reserve: float
    short_medium_budget: float
    long_term_budget: float


def split_capital(total_equity: float) -> CapitalAllocation:
    cfg = load_config()["capital"]
    reserve = total_equity * cfg["cash_reserve_pct"]
    investable = total_equity - reserve
    return CapitalAllocation(
        total_capital=total_equity,
        cash_reserve=reserve,
        short_medium_budget=investable * cfg["allocation"]["short_medium"],
        long_term_budget=investable * cfg["allocation"]["long_term"],
    )
