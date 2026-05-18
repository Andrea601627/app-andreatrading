"""Arbitrage loop — monitora coppie cross-listed e compra il leg più economico.

Logica:
1. Ogni N secondi scarica i prezzi di tutte le coppie in parallelo
2. Se lo spread tra i due leg supera le commissioni → BUY del leg economico
3. Exit: quando lo spread si chiude (prezzi convergono) → SELL con profitto
   oppure stop loss normale (-1.5%) se i prezzi divergono ulteriormente
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..execution.order_manager import get_broker
from ..risk.drawdown_guard import is_blocked
from ..risk.profit_guard import check as profit_guard_check
from ..strategy.arbitrage import detect_all, _fetch_price
from ..utils.config import load_config, project_path
from ..utils.db import connect, init_db
from ..utils.logger import get_logger

log = get_logger()


def _load_pairs() -> list[dict]:
    path = project_path("config/arb_pairs.yaml")
    with open(path) as f:
        return yaml.safe_load(f)["pairs"]


def _get_arb_positions() -> dict[str, dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT ticker, quantity, avg_price, high_water_mark FROM positions WHERE horizon='arb'"
        ).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def _update_hwm(ticker: str, hwm: float) -> None:
    with connect() as c:
        c.execute(
            "UPDATE positions SET high_water_mark=? WHERE ticker=? AND horizon='arb'",
            (hwm, ticker),
        )


def run_arb_cycle() -> dict:
    cfg = load_config()
    cfg_m = cfg["momentum"]
    cfg_arb = cfg.get("arbitrage", {})
    init_db()
    broker = get_broker()

    if is_blocked():
        return {"status": "blocked"}

    pairs = _load_pairs()
    commission_eur = cfg_m["commission_eur"]
    size_pct = cfg_arb.get("size_pct", 0.02)           # 2% del capitale per arb trade
    max_positions = cfg_arb.get("max_positions", 6)

    # Soglia minima spread: deve coprire commissioni andata+ritorno
    # Su posizione tipica 2k EUR: 2*2.95/2000 = 0.295% → arrotondiamo a 0.35%
    min_spread = cfg_arb.get("min_spread_pct", 0.0035)

    buys = 0
    sells = 0

    # ── 1. Controlla uscite su posizioni arb già aperte ──────────────
    arb_positions = _get_arb_positions()
    for ticker, pos in arb_positions.items():
        _, current_price = _fetch_price(ticker)
        if not current_price:
            continue

        hwm = pos.get("high_water_mark") or pos["avg_price"]
        if current_price > (hwm or 0):
            hwm = current_price
            _update_hwm(ticker, hwm)

        guard = profit_guard_check(
            buy_price=float(pos["avg_price"]),
            current_price=current_price,
            high_water_mark=float(hwm),
            quantity=int(pos["quantity"]),
            cfg_momentum=cfg_m,
        )
        if guard.should_sell:
            result = broker.sell(ticker, int(pos["quantity"]),
                                  current_price, close_reason=f"arb_{guard.reason}")
            if result.success:
                log.info(f"ARB SELL {ticker} @ {current_price:.4f} | "
                         f"reason={guard.reason} net={guard.net_gain_pct:.2%}")
                sells += 1

    # ── 2. Cerca nuove opportunità ────────────────────────────────────
    open_count = len(_get_arb_positions())
    if open_count >= max_positions:
        return {"status": "ok", "arb_buys": buys, "arb_sells": sells}

    signals = detect_all(pairs, min_spread)
    if not signals:
        log.info("Arb: nessuna opportunità sopra soglia")
        return {"status": "ok", "arb_buys": buys, "arb_sells": sells}

    cash = broker.cash()
    existing = set(_get_arb_positions().keys())

    for sig in signals:
        if open_count >= max_positions:
            break
        if sig.leg_buy in existing:
            continue

        budget = cfg["capital"]["initial"] * size_pct
        qty = int(math.floor(budget / sig.price_buy))
        if qty <= 0:
            continue

        cost = qty * sig.price_buy + commission_eur
        if cost > cash:
            log.info(f"ARB skip {sig.leg_buy}: cash insufficiente ({cost:.2f} > {cash:.2f})")
            continue

        result = broker.buy(
            sig.leg_buy, qty, sig.price_buy,
            horizon="arb",
            score=round(sig.spread_pct * 100, 2),
            reason_json=(
                f'{{"pair":"{sig.pair_name}",'
                f'"spread_pct":{sig.spread_pct:.4f},'
                f'"leg_ref":"{sig.leg_ref}",'
                f'"price_ref_eur":{sig.price_ref_eur:.4f}}}'
            ),
        )
        if result.success:
            log.info(
                f"ARB BUY {sig.leg_buy} qty={qty} @ {sig.price_buy:.4f} | "
                f"pair={sig.pair_name} spread={sig.spread_pct:.3%} vs {sig.leg_ref}"
            )
            buys += 1
            open_count += 1
            cash -= cost

    return {"status": "ok", "arb_buys": buys, "arb_sells": sells}


def run_forever() -> None:
    cfg = load_config()
    interval = cfg.get("arbitrage", {}).get("cycle_seconds", 30)
    log.info(f"Arb loop avviato — ciclo ogni {interval}s")
    while True:
        try:
            result = run_arb_cycle()
            if result.get("arb_buys") or result.get("arb_sells"):
                log.info(f"Arb cycle: {result}")
        except Exception as e:
            log.exception(f"Errore arb cycle: {e}")
        time.sleep(interval)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
