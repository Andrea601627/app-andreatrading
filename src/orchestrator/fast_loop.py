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
from ..risk.drawdown_guard import check as drawdown_check, is_blocked, record_equity, trip as drawdown_trip
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
    """Top titoli per il fast loop: puro trend following sull'universo completo.

    Scansiona tutti i titoli disponibili ordinati per momentum recente
    (rendimento 5 giorni + rendimento 1 giorno). Nessuna dipendenza dal
    loop lento — il fast loop decide autonomamente in base al trend di prezzo.
    """
    open_sfx = _open_market_suffixes()
    log.info(f"Mercati aperti ora: {open_sfx or 'nessuno'}")

    if not open_sfx:
        return []

    # Screener su universo completo — prende più del necessario poi riordina
    screened = screener_run(top_n=limit * 4)

    candidates = []
    for s in screened:
        if not _ticker_market_open(s.ticker, open_sfx):
            continue
        if not s.cached:
            continue
        # Score composito: 70% trend 5gg + 30% slancio 1gg
        score = (s.return_5d * 0.70 + s.return_1d * 0.30) * 100
        candidates.append({"ticker": s.ticker, "score": score})

    # Riordina per score composito (il più forte in cima)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    result = candidates[:limit]

    log.info(f"Watchlist trend following: {len(result)} titoli su mercati aperti")
    return result


def _get_fast_positions() -> dict[str, dict]:
    """Posizioni aperte dal fast loop (horizon='fast')."""
    with connect() as c:
        rows = c.execute("""
            SELECT ticker, quantity, avg_price, high_water_mark
            FROM positions WHERE horizon = 'fast'
        """).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def _daily_loss_blacklist() -> set[str]:
    """Ticker con >= 2 trade in perdita oggi (fast) — da non riaprire per oggi."""
    with connect() as c:
        rows = c.execute("""
            SELECT ticker, COUNT(*) AS n
            FROM trades
            WHERE side='SELL' AND pnl < 0 AND horizon='fast'
              AND date(closed_at) = date('now', 'localtime')
            GROUP BY ticker
            HAVING n >= 2
        """).fetchall()
    return {r["ticker"] for r in rows}


def _update_hwm(ticker: str, new_hwm: float) -> None:
    with connect() as c:
        c.execute(
            "UPDATE positions SET high_water_mark=? WHERE ticker=? AND horizon='fast'",
            (new_hwm, ticker),
        )


def _fetch_1m(ticker: str) -> tuple[str, pd.DataFrame | None]:
    """Scarica candele 5-minuto. Restituisce tutti i dati recenti (2 giorni).
    Il momentum userà solo le candele di oggi; per la valutazione posizioni
    si usa l'ultima candela disponibile anche se di ieri (mercato chiuso).
    """
    try:
        df = yf.download(ticker, period="2d", interval="5m",
                         auto_adjust=True, progress=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return ticker, None
        df.index = pd.to_datetime(df.index)
        return ticker, df
    except Exception as e:
        log.debug(f"5m fetch failed {ticker}: {e}")
        return ticker, None


def _df_today_only(df: pd.DataFrame) -> pd.DataFrame:
    """Filtra solo le candele di oggi (UTC) — usato per il momentum."""
    today = pd.Timestamp.now(tz="UTC").date()
    try:
        idx = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
        return df[idx.date == today]
    except Exception:
        return pd.DataFrame()


def _try_rotation(
    pending_signals: list[dict],
    data_map: dict,
    broker,
    cfg_m: dict,
    total_equity: float,
) -> tuple[int, int]:
    """Sostituisce posizioni peggiori con opportunità migliori dalla watchlist.

    Ruota se la posizione peggiore è in perdita E la perdita supera il costo
    delle commissioni di rotazione (unico freno: non perdere soldi in commissioni).
    Non cambia il numero totale di posizioni aperte (1 sell → 1 buy).
    """
    if not cfg_m.get("rotation_enabled", True) or not pending_signals:
        return 0, 0

    commission_eur = cfg_m["commission_eur"]

    # Calcola P&L netto corrente per ogni posizione aperta
    current_positions = _get_fast_positions()
    if not current_positions:
        return 0, 0

    pos_perf = []
    for ticker, pos in current_positions.items():
        df = data_map.get(ticker)
        current_price = float(df["Close"].iloc[-1]) if (df is not None and not df.empty) else float(pos["avg_price"])
        avg_price = float(pos["avg_price"])
        qty = int(pos["quantity"])
        notional = avg_price * qty
        # Soglia minima dinamica = costo round-trip commissioni sulla posizione
        commission_pct = (2 * commission_eur / notional) if notional > 0 else 0.002
        net_gain = (current_price - avg_price) / avg_price
        pos_perf.append({
            "ticker": ticker,
            "net_gain": net_gain,
            "commission_pct": commission_pct,
            "current_price": current_price,
            "quantity": qty,
        })

    # Ordina: le posizioni più in perdita prima
    pos_perf.sort(key=lambda x: x["net_gain"])

    sells = 0
    buys = 0

    for sig_info in pending_signals:
        if not pos_perf:
            break
        sig = sig_info["signal"]

        worst = pos_perf[0]
        # Ruota solo se la perdita supera il costo delle commissioni di rotazione
        # (evita di pagare più in commissioni di quanto si recupera)
        if worst["net_gain"] >= -worst["commission_pct"]:
            break

        new_ticker = sig_info["ticker"]
        new_price = sig_info["current_price"]

        # Vendi la posizione debole
        sell_result = broker.sell(worst["ticker"], worst["quantity"],
                                   worst["current_price"], close_reason="rotation")
        if not sell_result.success:
            pos_perf.pop(0)
            continue

        log.info(f"ROTATION SELL {worst['ticker']} net={worst['net_gain']:.2%} "
                 f"→ slot per {new_ticker} (strength={sig.strength:.2f})")
        sells += 1
        pos_perf.pop(0)

        # Sizing del nuovo acquisto
        slow_bonus = min(sig_info.get("slow_score", 0) / 100.0, 0.20)
        effective_size = sig.size_pct * (1 + slow_bonus)
        budget = total_equity * effective_size
        qty = int(math.floor(budget / new_price))
        qty = max(qty, _min_qty(new_price, commission_eur, cfg_m["min_net_gain_pct"]))

        if qty * new_price > broker.cash():
            log.info(f"Rotation: skip BUY {new_ticker} — cash insufficiente dopo vendita")
            continue

        buy_result = broker.buy(
            new_ticker, qty, new_price,
            horizon="fast",
            score=round(sig.momentum_pct * 100, 2),
            reason_json=json.dumps({
                "momentum_pct": round(sig.momentum_pct, 5),
                "strength": sig.strength,
                "volume_confirmed": sig.volume_confirmed,
                "slow_score": sig_info.get("slow_score", 0),
                "rotation_from": worst["ticker"],
                "rotation_replaced_gain": round(worst["net_gain"], 5),
            }),
        )
        if buy_result.success:
            log.info(f"ROTATION BUY {new_ticker} qty={qty} @ {new_price:.4f} | "
                     f"momentum={sig.momentum_pct:.2%}")
            buys += 1

    return sells, buys


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

    # Download candele 5-minuto in parallelo
    data_map: dict[str, pd.DataFrame | None] = {}
    with ThreadPoolExecutor(max_workers=min(16, len(all_tickers))) as pool:
        futures = {pool.submit(_fetch_1m, t): t for t in all_tickers}
        for future in as_completed(futures):
            try:
                ticker, df = future.result(timeout=15)
            except Exception as e:
                log.debug(f"Fetch timeout/error: {e}")
                continue
            data_map[ticker] = df

    sells = 0
    buys = 0

    # Equity totale per sizing (snapshot iniziale)
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
    # Rilegge cash dopo le eventuali vendite — le vendite liberano liquidità
    cash = broker.cash()
    open_count = len(_get_fast_positions())
    slow_scores = {w["ticker"]: w["score"] for w in watchlist}
    loss_blacklist = _daily_loss_blacklist()

    if loss_blacklist:
        log.info(f"Blacklist giornaliera: {len(loss_blacklist)} ticker con 2+ perdite oggi → {loss_blacklist}")

    log.info(f"Fast cycle: watchlist={len(watchlist)} titoli, posizioni_aperte={open_count}, "
             f"max_posizioni={cfg_m['max_fast_positions']}, cash={cash:.2f}")

    pending_signals: list[dict] = []  # segnali forti in attesa per eventuale rotazione

    for w in watchlist:
        ticker = w["ticker"]
        if ticker in positions:
            continue
        if ticker in loss_blacklist:
            log.debug(f"{ticker}: skip — 2+ perdite oggi (blacklist giornaliera)")
            continue

        df = data_map.get(ticker)
        if df is None or df.empty:
            log.debug(f"{ticker}: nessun dato disponibile")
            continue

        df_today = _df_today_only(df)
        sig = detect_momentum(ticker, df_today, cfg_m)
        if sig.direction != "BUY":
            log.info(f"{ticker}: momentum={sig.momentum_pct:.3%} vol_ok={sig.volume_confirmed} → {sig.direction}")
            continue

        current_price = float(df["Close"].iloc[-1])
        if current_price <= 0:
            continue

        if open_count >= cfg_m["max_fast_positions"]:
            # Limite raggiunto: raccoglie segnali forti come candidati rotazione
            if sig.strength >= cfg_m.get("rotation_min_strength", 0.65):
                pending_signals.append({
                    "ticker": ticker, "signal": sig,
                    "current_price": current_price,
                    "slow_score": slow_scores.get(ticker, 0),
                })
            continue

        log.info(f"{ticker}: segnale BUY momentum={sig.momentum_pct:.3%} strength={sig.strength:.2f} volume_ok={sig.volume_confirmed}")

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

    # 3. Rotazione: sostituisce posizioni deboli con opportunità più promettenti
    if pending_signals:
        pending_signals = [s for s in pending_signals if s["ticker"] not in loss_blacklist]
        log.info(f"Rotazione: {len(pending_signals)} candidati in attesa, verifico posizioni deboli")
        rot_sells, rot_buys = _try_rotation(pending_signals, data_map, broker, cfg_m, total_equity)
        sells += rot_sells
        buys += rot_buys

    # Aggiorna equity curve ad ogni ciclo fast
    current_positions = _get_fast_positions()
    pos_value = sum(
        p["quantity"] * float(data_map[t]["Close"].iloc[-1])
        if data_map.get(t) is not None
        else p["quantity"] * p["avg_price"]
        for t, p in current_positions.items()
    )
    current_cash = broker.cash()
    total_equity_now = record_equity(current_cash, pos_value)

    # Valuta il circuit breaker ad ogni ciclo fast (non solo nel loop lento)
    dd_status = drawdown_check(total_equity_now)
    if dd_status.triggered:
        drawdown_trip(dd_status)
        log.warning(f"CIRCUIT BREAKER ATTIVATO: {dd_status.reason}")

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
