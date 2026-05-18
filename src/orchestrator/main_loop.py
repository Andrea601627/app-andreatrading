"""Loop principale: fetch -> analyze -> decide -> execute -> learn.

Eseguibile in due modi:
  - single cycle: `python -m src.orchestrator.main_loop --once`
  - loop continuo: `python -m src.orchestrator.main_loop` (rispetta cycle_interval_seconds)
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from ..analysis.regime import detect as detect_regime
from ..data.fetcher import get_last_price
from ..data.screener import top_tickers
from ..data.universe import tickers
from ..execution.order_manager import get_broker
from ..learning.adaptive import propose_weight_adjustments, record_proposal
from ..learning.journal import log_signal
from ..learning.postmortem import run_postmortem
from ..risk.concentration import can_open_new
from ..risk.drawdown_guard import check as dd_check, is_blocked, record_equity, trip
from ..risk.position_sizer import calculate_size
from ..risk.stop_loss import initial_stop, update_trailing
from ..strategy.allocation import split_capital
from ..strategy.signals import analyze_ticker
from ..utils.config import load_config
from ..utils.db import init_db
from ..utils.logger import get_logger

_ANALYSIS_WORKERS = 8

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


def _portfolio_value(broker, price_map: dict[str, float]) -> tuple[float, float, float]:
    cash = broker.cash()
    positions = broker.positions()
    pos_value = sum(p.quantity * price_map.get(t, p.avg_price) for t, p in positions.items())
    return cash, pos_value, cash + pos_value


def _update_trailing_stops(broker, price_map):
    from ..utils.db import connect
    updated = 0
    with connect() as c:
        rows = c.execute("""
            SELECT ticker, avg_price, stop_loss, high_water_mark, horizon
            FROM positions
        """).fetchall()
        for r in rows:
            cur = price_map.get(r["ticker"])
            if cur is None or r["stop_loss"] is None:
                continue
            # ATR approssimato come 2% del prezzo (placeholder, in produzione lo
            # passeremmo dalle indicators del segnale corrente)
            atr_approx = cur * 0.02
            new_stop, new_hwm = update_trailing(
                r["avg_price"], cur, r["stop_loss"],
                r["high_water_mark"], atr_approx
            )
            if new_stop != r["stop_loss"] or new_hwm != r["high_water_mark"]:
                c.execute("""
                    UPDATE positions SET stop_loss=?, high_water_mark=?
                    WHERE ticker=?
                """, (new_stop, new_hwm, r["ticker"]))
                updated += 1
    if updated:
        log.info(f"Trailing stops aggiornati: {updated}")


def _check_stop_loss_hits(broker, price_map):
    """Se prezzo corrente <= stop_loss, chiudi posizione."""
    from ..utils.db import connect
    with connect() as c:
        rows = c.execute("""
            SELECT ticker, quantity, stop_loss, take_profit FROM positions
        """).fetchall()
    for r in rows:
        cur = price_map.get(r["ticker"])
        if cur is None:
            continue
        if r["stop_loss"] and cur <= r["stop_loss"]:
            log.warning(f"STOP LOSS hit on {r['ticker']} @ {cur:.4f} (stop={r['stop_loss']:.4f})")
            broker.sell(r["ticker"], int(r["quantity"]), cur, close_reason="stop_loss")
        elif r["take_profit"] and cur >= r["take_profit"]:
            log.info(f"TAKE PROFIT hit on {r['ticker']} @ {cur:.4f}")
            broker.sell(r["ticker"], int(r["quantity"]), cur, close_reason="target")


def run_cycle() -> dict:
    cfg = load_config()
    init_db()
    broker = get_broker()

    if is_blocked():
        log.error("CIRCUIT BREAKER ATTIVO — nessuna operazione. Risolvi dal dashboard.")
        return {"status": "blocked"}

    if not _is_market_hours(cfg):
        log.info("Fuori orari di mercato — solo analisi, no ordini.")

    log.info("=== Inizio ciclo ===")
    regime = detect_regime()
    log.info(f"Regime mercato: {regime.trend} / vol={regime.volatility} ({regime.annualized_vol_pct}%)")

    # 1) screener pre-filter: top 100 per rendimento recente (usa cache parquet, no download)
    screener_top_n = cfg.get("screener", {}).get("top_n_for_analysis", 100)
    universe_size = len(tickers())
    selected = top_tickers(top_n=screener_top_n)
    log.info(f"Screener: {universe_size} titoli totali → top {len(selected)} selezionati per analisi")

    log.info(f"Analisi {len(selected)} titoli con {_ANALYSIS_WORKERS} worker paralleli...")
    raw_signals: list = []
    with ThreadPoolExecutor(max_workers=_ANALYSIS_WORKERS) as pool:
        future_map = {pool.submit(analyze_ticker, t, regime): t for t in selected}
        for future in as_completed(future_map):
            t = future_map[future]
            try:
                raw_signals.append(future.result())
            except Exception as e:
                log.warning(f"Analisi fallita per {t}: {e}")

    # scrittura DB sequenziale (thread-safe)
    signals = []
    price_map: dict[str, float] = {}
    for sig in raw_signals:
        signals.append(sig)
        if sig.last_price:
            price_map[sig.ticker] = sig.last_price
        log_signal(
            sig.ticker,
            sig.decision_short,
            sig.short_medium,
            regime,
            sig.quality_ok,
            notes=";".join(sig.quality_issues) if sig.quality_issues else "",
        )

    # 2) aggiorna prezzi correnti per le posizioni
    for held in broker.positions().keys():
        if held not in price_map:
            p = get_last_price(held)
            if p:
                price_map[held] = p

    # 3) trailing stop + check stop loss
    _update_trailing_stops(broker, price_map)
    _check_stop_loss_hits(broker, price_map)

    # 4) equity update + circuit breaker
    cash, pos_value, total = _portfolio_value(broker, price_map)
    record_equity(cash, pos_value)
    dd = dd_check(total)
    if dd.triggered:
        log.error(f"DRAWDOWN CIRCUIT BREAKER: {dd.reason}")
        trip(dd)
        for ticker, p in broker.positions().items():
            cur = price_map.get(ticker, p.avg_price)
            broker.sell(ticker, int(p.quantity), cur, close_reason="drawdown_guard")
        return {"status": "circuit_breaker_tripped", "equity": total}

    # 5) ricava budget per orizzonte
    alloc = split_capital(total)
    log.info(f"Equity={total:.2f} cash={cash:.2f} | budget S/M={alloc.short_medium_budget:.2f} "
              f"L={alloc.long_term_budget:.2f}")

    # 6) genera ordini
    if not _is_market_hours(cfg):
        log.info("Ordini posticipati (fuori orari).")
    else:
        _execute_decisions(broker, signals, alloc, total, price_map)

    # 7) postmortem + learning
    run_postmortem()
    suggestions = propose_weight_adjustments()
    for s in suggestions:
        record_proposal(s, horizon="short_medium")

    log.info("=== Fine ciclo ===")
    return {
        "status": "ok",
        "equity": total,
        "regime": f"{regime.trend}/{regime.volatility}",
        "signals_analyzed": len(signals),
        "open_positions": len(broker.positions()),
    }


def _execute_decisions(broker, signals, alloc, total_equity, price_map):
    from ..utils.db import connect
    # Conta posizioni aperte per orizzonte
    with connect() as c:
        rows = c.execute("SELECT horizon, COUNT(*) AS n FROM positions GROUP BY horizon").fetchall()
        open_by_h = {r["horizon"]: r["n"] for r in rows}

    open_positions = {t for t in broker.positions()}

    # SELL prima
    for sig in signals:
        if not sig.quality_ok:
            continue
        for horizon, decision in (("short_medium", sig.decision_short),
                                    ("long_term", sig.decision_long)):
            if decision and decision.action in ("SELL", "STRONG_SELL") and sig.ticker in open_positions:
                pos = broker.positions().get(sig.ticker)
                if pos:
                    result = broker.sell(sig.ticker, int(pos.quantity),
                                          sig.last_price, close_reason="signal")
                    log.info(f"SELL {sig.ticker} qty={pos.quantity} -> {result.message}")

    # BUY dopo (per liberare cash)
    sm_budget_left = alloc.short_medium_budget
    lt_budget_left = alloc.long_term_budget

    for horizon, decisions in (
        ("short_medium", [(s, s.decision_short) for s in signals if s.decision_short and s.quality_ok]),
        ("long_term", [(s, s.decision_long) for s in signals if s.decision_long and s.quality_ok]),
    ):
        # ranking: STRONG_BUY > BUY, poi score desc
        candidates = [(s, d) for s, d in decisions if d.action in ("BUY", "STRONG_BUY")
                       and s.ticker not in open_positions]
        candidates.sort(key=lambda x: (x[1].action != "STRONG_BUY", -x[1].score))

        budget_left = sm_budget_left if horizon == "short_medium" else lt_budget_left
        for sig, dec in candidates:
            if budget_left <= 0:
                break
            sizing = calculate_size(
                sig.ticker, sig.last_price, dec.score,
                budget_left, total_equity, open_by_h.get(horizon, 0),
            )
            if sizing.quantity == 0:
                continue
            ok, reason = can_open_new(sig.ticker, sizing.notional, total_equity, price_map)
            if not ok:
                log.info(f"SKIP {sig.ticker}: {reason}")
                continue

            # stop levels
            atr = sig.raw_components.get("technical", {}).get("indicators", {}).get("atr14")
            stops = initial_stop(sig.last_price, atr)
            reason_payload = json.dumps({
                "decision": asdict(dec),
                "components": sig.raw_components,
                "regime": f"{(sig.short_medium.regime.trend if sig.short_medium and sig.short_medium.regime else 'unknown')}",
                "weights": sig.short_medium.weights_used if sig.short_medium else {},
            }, default=str)

            result = broker.buy(
                sig.ticker, sizing.quantity, sig.last_price,
                horizon=horizon, score=dec.score, reason_json=reason_payload,
                market_regime=f"{sig.short_medium.regime.trend}/{sig.short_medium.regime.volatility}"
                              if sig.short_medium and sig.short_medium.regime else "",
                stop_loss=stops.stop_loss, take_profit=stops.take_profit,
            )
            if result.success:
                budget_left -= sizing.notional
                open_by_h[horizon] = open_by_h.get(horizon, 0) + 1
                log.info(f"BUY {sig.ticker} qty={sizing.quantity} @ {result.fill_price:.4f} "
                          f"score={dec.score:.1f} stop={stops.stop_loss:.4f}")
            else:
                log.warning(f"BUY {sig.ticker} fallito: {result.message}")

        if horizon == "short_medium":
            sm_budget_left = budget_left
        else:
            lt_budget_left = budget_left


def run_forever() -> None:
    cfg = load_config()
    interval = cfg["orchestrator"]["cycle_interval_seconds"]
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.exception(f"Errore nel ciclo: {e}")
        log.info(f"Sleep {interval}s prima del prossimo ciclo")
        time.sleep(interval)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="Esegui un solo ciclo")
    args = p.parse_args()
    if args.once:
        out = run_cycle()
        print(json.dumps(out, indent=2, default=str))
    else:
        run_forever()


if __name__ == "__main__":
    main()
