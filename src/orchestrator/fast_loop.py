"""Fast loop - momentum trading ogni 60 secondi.

Logica:
1. Legge la watchlist dal loop lento (top titoli con score positivo)
2. Scarica candele 1-minuto solo per watchlist + posizioni aperte
3. Controlla profit guard su posizioni esistenti
4. Apre nuove posizioni se rileva momentum + conferma volume
5. Il sizing dipende dalla forza del trend
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from ..data.screener import run as screener_run
from ..execution.order_manager import get_broker
from ..risk.drawdown_guard import is_blocked, record_equity
from ..risk.profit_guard import check as profit_guard_check
from ..strategy.momentum import detect as detect_momentum
from ..utils.config import load_config
from ..utils.db import connect, init_db
from ..utils.logger import get_logger

log = get_logger()


def _is_market_hours(cfg: dict) -> bool:
    if not cfg["orchestrator"]["trade_only_market_hours"]:
        return True
    tz = ZoneInfo("Europe/Rome")
    now_it = datetime.now(tz)
    now = now_it.time()
    open_t = dtime.fromisoformat(cfg["orchestrator"]["market_hours"]["open"])
    close_t = dtime.fromisoformat(cfg["orchestrator"]["market_hours"]["close"])
    return now_it.weekday() < 5 and open_t <= now <= close_t


def _open_market_suffixes() -> set[str]:
    """Restituisce i suffissi dei mercati attualmente aperti (orario UTC)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return set()
    h = now.hour + now.minute / 60
    open_sfx: set[str] = set()
    if 7.0 <= h <= 15.5:                        # Borsa Italiana, Euronext
        open_sfx.update([".MI", ".PA", ".DE", ".AS", ".BR", ".MC"])
    if 8.0 <= h <= 16.5:                        # LSE Londra
        open_sfx.add(".L")
    if 13.5 <= h <= 20.0:                       # NYSE / NASDAQ
        open_sfx.add("US")
    if h <= 6.0 or h >= 23.5:                   # Tokyo
        open_sfx.add(".T")
    if 1.5 <= h <= 8.0:                         # Hong Kong
        open_sfx.add(".HK")
    return open_sfx


def _ticker_market_open(ticker: str, open_sfx: set[str]) -> bool:
    """True se il mercato del ticker è attualmente aperto."""
    for sfx in [".MI", ".PA", ".DE", ".L", ".AS", ".BR", ".MC", ".T", ".HK"]:
        if ticker.upper().endswith(sfx.upper()):
            return sfx in open_sfx
    return "US" in open_sfx   # nessun suffisso = titolo USA


def _get_watchlist(limit: int) -> list[dict]:
    """Top titoli per il fast loop, filtrati per mercato aperto.

    Priorità 1: segnali recenti dal loop lento (score > 0, quality_ok).
    Priorità 2: screener per rendimento 5-giorni (usato se segnali insufficienti).
    """
    open_sfx = _open_market_suffixes()
    log.info(f"Mercati aperti ora: {open_sfx or 'nessuno'}")

    with connect() as c:
        rows = c.execute("""
            SELECT s.ticker, s.score
            FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(generated_at) AS mx
                FROM signals GROUP BY ticker
            ) latest ON s.ticker = latest.ticker AND s.generated_at = latest.mx
            WHERE s.score > 0 AND s.quality_ok = 1 AND s.horizon = 'short_medium'
            ORDER BY s.score DESC
        """).fetchall()

    # Filtra solo titoli con mercato aperto
    result = [
        {"ticker": r["ticker"], "score": float(r["score"])}
        for r in rows
        if _ticker_market_open(r["ticker"], open_sfx)
    ][:limit]

    # Se pochi segnali usa lo screener (solo mercati aperti)
    if len(result) < limit // 2:
        screened = screener_run(top_n=limit * 2)
        seen = {r["ticker"] for r in result}
        for s in screened:
            if s.ticker not in seen and _ticker_market_open(s.ticker, open_sfx):
                result.append({"ticker": s.ticker, "score": s.return_5d * 100})
                if len(result) >= limit:
                    break

    return result[:limit]


def _get_fast_positions() -> dict[str, dict]:
    """Posizioni aperte dal fast loop (horizon='fast')."""
    with connect() as c:
        rows = c.execute("""
            SELECT ticker, quantity, avg_price, high_water_mark
            FROM positions WHERE horizon = 'fast'
        """).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def _update_hwm(ticker: str, new_hwm: float) -> None:
    with connect() as c:
        c.execute(
            "UPDATE positions SET high_water_mark=? WHERE ticker=? AND horizon='fast'",
            (new_hwm, ticker),
        )


def _fetch_1m(ticker: str) -> tuple[str, pd.DataFrame | None]:
    try:
        df = yf.download(ticker, period="1d", interval="1m",
                         auto_adjust=True, progress=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return ticker, None
        df.index = pd.to_datetime(df.index)
        return ticker, df
    except Exception as e:
        log.debug(f"1m fetch failed {ticker}: {e}")
        return ticker, None


def _min_qty(price: float, commission_eur: float, min_net_gain: float) -> int:
    """Quantità minima per coprire le commissioni e avere margine."""
    if price <= 0 or min_net_gain <= 0:
        return 1
    return max(1, math.ceil((2 * commission_eur) / (price * min_net_gain)))


def run_fast_cycle() -> dict:
    cfg = load_config()
    cfg_m = cfg["momentum"]
    init_db()
    broker = get_broker()

    if is_blocked():
        return {"status": "blocked"}

    if not _is_market_hours(cfg):
        return {"status": "outside_hours"}

    watchlist = _get_watchlist(cfg_m["watchlist_size"])
    positions = _get_fast_positions()

    watch_set = {w["ticker"] for w in watchlist}
    all_tickers = list(set(positions.keys()) | watch_set)

    if not all_tickers:
        return {"status": "no_tickers"}

    # Download candele 1-minuto in parallelo
    data_map: dict[str, pd.DataFrame | None] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(all_tickers))) as pool:
        futures = {pool.submit(_fetch_1m, t): t for t in all_tickers}
        for future in as_completed(futures):
            ticker, df = future.result()
            data_map[ticker] = df

    sells = 0
    buys = 0

    # Equity totale per sizing
    cash = broker.cash()
    pos_value = sum(
        p["quantity"] * float(data_map[t]["Close"].iloc[-1])
        if data_map.get(t) is not None
        else p["quantity"] * p["avg_price"]
        for t, p in positions.items()
    )
    total_equity = cash + pos_value

    # 1. Profit guard su posizioni aperte
    for ticker, pos in positions.items():
        df = data_map.get(ticker)
        if df is None or df.empty:
            continue

        current_price = float(df["Close"].iloc[-1])
        hwm = pos.get("high_water_mark") or pos["avg_price"]

        # Aggiorna high water mark se il prezzo è salito
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
                                 current_price, close_reason=guard.reason)
            if result.success:
                log.info(f"FAST SELL {ticker} @ {current_price:.4f} | "
                         f"reason={guard.reason} net={guard.net_gain_pct:.2%}")
                sells += 1

    # 2. Nuovi acquisti dalla watchlist
    open_count = len(_get_fast_positions())
    slow_scores = {w["ticker"]: w["score"] for w in watchlist}

    log.info(f"Fast cycle: watchlist={len(watchlist)} titoli, posizioni_aperte={open_count}, "
             f"max_posizioni={cfg_m['max_fast_positions']}, cash={cash:.2f}")

    for w in watchlist:
        ticker = w["ticker"]
        if open_count >= cfg_m["max_fast_positions"]:
            log.info(f"Max posizioni raggiunto ({cfg_m['max_fast_positions']}), stop acquisti")
            break
        if ticker in positions:
            continue

        df = data_map.get(ticker)
        if df is None or df.empty:
            log.debug(f"{ticker}: nessun dato 1m disponibile")
            continue

        sig = detect_momentum(ticker, df, cfg_m)
        if sig.direction != "BUY":
            log.info(f"{ticker}: momentum={sig.momentum_pct:.3%} vol_ok={sig.volume_confirmed} → {sig.direction}")
            continue

        log.info(f"{ticker}: segnale BUY momentum={sig.momentum_pct:.3%} strength={sig.strength:.2f} volume_ok={sig.volume_confirmed}")

        current_price = float(df["Close"].iloc[-1])
        if current_price <= 0:
            continue

        # Sizing: momentum strength + bonus da slow score
        slow_bonus = min(slow_scores.get(ticker, 0) / 100.0, 0.20)
        effective_size = sig.size_pct * (1 + slow_bonus)
        budget = total_equity * effective_size

        qty = int(math.floor(budget / current_price))
        qty = max(qty, _min_qty(current_price, cfg_m["commission_eur"],
                                cfg_m["min_net_gain_pct"]))

        if qty * current_price > cash:
            log.info(f"{ticker}: skip — costo {qty * current_price:.2f} EUR > cash {cash:.2f} EUR")
            continue

        result = broker.buy(
            ticker, qty, current_price,
            horizon="fast",
            score=round(sig.momentum_pct * 100, 2),
            reason_json=json.dumps({
                "momentum_pct": round(sig.momentum_pct, 5),
                "strength": sig.strength,
                "volume_confirmed": sig.volume_confirmed,
                "slow_score": slow_scores.get(ticker, 0),
            }),
        )
        if result.success:
            log.info(f"FAST BUY {ticker} qty={qty} @ {current_price:.4f} | "
                     f"momentum={sig.momentum_pct:.2%} strength={sig.strength:.2f}")
            buys += 1
            open_count += 1
            cash -= qty * current_price

    # Aggiorna equity curve ad ogni ciclo fast
    pos_value = sum(
        p["quantity"] * float(data_map[t]["Close"].iloc[-1])
        if data_map.get(t) is not None
        else p["quantity"] * p["avg_price"]
        for t, p in _get_fast_positions().items()
    )
    record_equity(broker.cash(), pos_value)

    return {"status": "ok", "buys": buys, "sells": sells, "fast_positions": open_count}


def run_forever() -> None:
    cfg = load_config()
    interval = cfg["momentum"]["fast_cycle_seconds"]
    log.info(f"Fast loop avviato — ciclo ogni {interval}s")
    while True:
        try:
            result = run_fast_cycle()
            if result.get("buys") or result.get("sells"):
                log.info(f"Fast cycle: {result}")
        except Exception as e:
            log.exception(f"Errore fast cycle: {e}")
        time.sleep(interval)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
