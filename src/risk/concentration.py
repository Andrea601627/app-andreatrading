"""Vincoli di concentrazione per settore e per singolo titolo."""
from __future__ import annotations

from collections import defaultdict

from ..data.universe import get_asset
from ..utils.config import load_config
from ..utils.db import connect


def current_exposure_by_sector(price_map: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    with connect() as c:
        rows = c.execute("SELECT ticker, quantity FROM positions").fetchall()
    for r in rows:
        asset = get_asset(r["ticker"])
        sector = asset.sector if asset else "Unknown"
        p = price_map.get(r["ticker"], 0)
        out[sector] += r["quantity"] * p
    return dict(out)


def can_open_new(ticker: str, notional: float,
                 total_equity: float, price_map: dict[str, float]) -> tuple[bool, str]:
    cfg = load_config()["risk"]
    asset = get_asset(ticker)
    sector = asset.sector if asset else "Unknown"

    # Position size cap
    if notional > total_equity * cfg["max_position_pct"]:
        return False, f"exceeds_max_position_pct ({notional:.2f} > {total_equity * cfg['max_position_pct']:.2f})"

    # Concentrazione settore
    sector_exp = current_exposure_by_sector(price_map)
    new_sector_exp = sector_exp.get(sector, 0) + notional
    if new_sector_exp > total_equity * cfg["max_sector_pct"]:
        return False, f"sector_cap_{sector}:{new_sector_exp:.2f} > {total_equity * cfg['max_sector_pct']:.2f}"

    # Numero posizioni
    with connect() as c:
        count = c.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]
    if count >= cfg["max_concurrent_positions"]:
        return False, f"max_positions_reached:{count}"

    return True, "ok"
