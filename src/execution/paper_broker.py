"""Broker simulato - simula commissioni, slippage e tiene contabilità su SQLite."""
from __future__ import annotations

from datetime import datetime, timezone

from ..utils.config import load_config
from ..utils.db import connect
from .broker_base import Broker, OrderResult, Position


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PaperBroker(Broker):
    def __init__(self) -> None:
        cfg = load_config()
        self._cfg_broker = cfg["broker"]["paper"]
        self.slippage_bps = self._cfg_broker["slippage_bps"]
        self._initial = cfg["capital"]["initial"]
        self._ensure_seeded()

    def _calc_commission(self, notional: float) -> float:
        model = self._cfg_broker.get("commission_model", "fixed")
        if model == "ibkr_tiered":
            pct = self._cfg_broker.get("commission_pct", 0.0005)
            min_eur = self._cfg_broker.get("commission_min_eur", 1.25)
            max_pct = self._cfg_broker.get("commission_max_pct", 0.01)
            return min(max(notional * pct, min_eur), notional * max_pct)
        return self._cfg_broker.get("commission_per_trade_eur", 2.95)

    def _ensure_seeded(self) -> None:
        with connect() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM equity_curve").fetchone()
            if row["n"] == 0:
                c.execute("""
                    INSERT INTO equity_curve
                    (timestamp, cash, positions_value, total_equity, drawdown_pct)
                    VALUES (?, ?, 0, ?, 0)
                """, (datetime.now(timezone.utc), self._initial, self._initial))

    def cash(self) -> float:
        with connect() as c:
            buys = c.execute(
                "SELECT COALESCE(SUM(quantity*price + commission),0) AS s "
                "FROM trades WHERE side='BUY'"
            ).fetchone()["s"]
            sells_open = c.execute(
                "SELECT COALESCE(SUM(quantity*price - commission),0) AS s "
                "FROM trades WHERE side='SELL'"
            ).fetchone()["s"]
        return float(self._initial - buys + sells_open)

    def positions(self) -> dict[str, Position]:
        with connect() as c:
            rows = c.execute(
                "SELECT ticker, quantity, avg_price FROM positions"
            ).fetchall()
        return {r["ticker"]: Position(r["ticker"], r["quantity"], r["avg_price"])
                for r in rows}

    def _apply_slippage(self, price: float, side: str) -> float:
        slip = price * self.slippage_bps / 10000
        return price + slip if side == "BUY" else price - slip

    def buy(self, ticker: str, quantity: int, ref_price: float,
            *, horizon: str = "short_medium", score: float | None = None,
            reason_json: str = "", market_regime: str = "",
            stop_loss: float | None = None,
            take_profit: float | None = None) -> OrderResult:
        if quantity <= 0:
            return OrderResult(False, ticker, "BUY", 0, 0, 0, _now(), "qty<=0")
        fill = self._apply_slippage(ref_price, "BUY")
        commission = self._calc_commission(fill * quantity)
        cost = fill * quantity + commission
        if cost > self.cash():
            return OrderResult(False, ticker, "BUY", quantity, fill, commission,
                                _now(), "insufficient_cash")

        ts = datetime.now(timezone.utc)
        with connect() as c:
            cur = c.execute("""
                INSERT INTO trades (ticker, side, quantity, price, commission,
                                     horizon, score, reason_json, market_regime,
                                     executed_at)
                VALUES (?, 'BUY', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, quantity, fill, commission,
                  horizon, score, reason_json, market_regime, ts))
            trade_id = cur.lastrowid

            # aggiorna position (media ponderata se già esiste)
            existing = c.execute(
                "SELECT quantity, avg_price FROM positions WHERE ticker=?",
                (ticker,)).fetchone()
            if existing:
                new_qty = existing["quantity"] + quantity
                new_avg = (existing["quantity"] * existing["avg_price"] +
                            quantity * fill) / new_qty
                c.execute("""
                    UPDATE positions SET quantity=?, avg_price=?, stop_loss=?,
                                          take_profit=?, high_water_mark=?
                    WHERE ticker=?
                """, (new_qty, new_avg, stop_loss, take_profit, fill, ticker))
            else:
                c.execute("""
                    INSERT INTO positions (ticker, quantity, avg_price, horizon,
                                            opened_at, stop_loss, take_profit,
                                            high_water_mark, trade_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ticker, quantity, fill, horizon, ts, stop_loss, take_profit,
                      fill, trade_id))

        return OrderResult(True, ticker, "BUY", quantity, fill, commission,
                            _now(), "filled")

    def sell(self, ticker: str, quantity: int, ref_price: float,
              *, close_reason: str = "signal") -> OrderResult:
        if quantity <= 0:
            return OrderResult(False, ticker, "SELL", 0, 0, 0, _now(), "qty<=0")

        with connect() as c:
            pos = c.execute(
                "SELECT quantity, avg_price, trade_id, horizon FROM positions WHERE ticker=?",
                (ticker,)).fetchone()
            if not pos or pos["quantity"] < quantity:
                return OrderResult(False, ticker, "SELL", quantity, 0, 0, _now(),
                                    "insufficient_position")

            fill = self._apply_slippage(ref_price, "SELL")
            commission = self._calc_commission(fill * quantity)
            ts = datetime.now(timezone.utc)
            pnl = (fill - pos["avg_price"]) * quantity - commission
            pnl_pct = ((fill / pos["avg_price"]) - 1) if pos["avg_price"] else 0

            c.execute("""
                INSERT INTO trades (ticker, side, quantity, price, commission,
                                     horizon, executed_at, closed_at, close_price,
                                     pnl, pnl_pct, close_reason)
                VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, quantity, fill, commission, pos["horizon"],
                  ts, ts, fill, pnl, pnl_pct, close_reason))

            # marca anche il BUY originale come chiuso
            if pos["trade_id"]:
                c.execute("""
                    UPDATE trades SET closed_at=?, close_price=?, pnl=?, pnl_pct=?,
                                       close_reason=?
                    WHERE id=?
                """, (ts, fill, pnl, pnl_pct, close_reason, pos["trade_id"]))

            new_qty = pos["quantity"] - quantity
            if new_qty <= 0:
                c.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
            else:
                c.execute("UPDATE positions SET quantity=? WHERE ticker=?",
                          (new_qty, ticker))

        return OrderResult(True, ticker, "SELL", quantity, fill, commission,
                            _now(), f"filled pnl={pnl:.2f}")
