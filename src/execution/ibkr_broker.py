"""Adapter Interactive Brokers (ib_insync).

Skeleton: si attiva solo con mode=live in config + credenziali corrette.
Per testarlo serve TWS / IB Gateway in esecuzione localmente.
"""
from __future__ import annotations

from datetime import datetime

from ..utils.config import load_config
from ..utils.logger import get_logger
from .broker_base import Broker, OrderResult, Position

log = get_logger()


class IBKRBroker(Broker):
    def __init__(self) -> None:
        try:
            from ib_insync import IB  # noqa
        except ImportError as e:
            raise RuntimeError("ib_insync non installato. pip install ib_insync") from e

        from ib_insync import IB
        cfg = load_config()["broker"]["ibkr"]
        self.ib = IB()
        self.ib.connect(cfg["host"], cfg["port"], clientId=cfg["client_id"])
        log.info(f"IBKR connesso: {cfg['host']}:{cfg['port']}")

    def _contract(self, ticker: str):
        from ib_insync import Stock
        sym = ticker.replace(".MI", "")
        return Stock(sym, "BVME", "EUR")

    def cash(self) -> float:
        summary = self.ib.accountSummary()
        for s in summary:
            if s.tag == "TotalCashValue" and s.currency == "EUR":
                return float(s.value)
        return 0.0

    def positions(self) -> dict[str, Position]:
        out: dict[str, Position] = {}
        for p in self.ib.positions():
            sym = p.contract.symbol + ".MI"
            out[sym] = Position(sym, float(p.position), float(p.avgCost))
        return out

    def buy(self, ticker: str, quantity: int, ref_price: float, **kwargs) -> OrderResult:
        from ib_insync import MarketOrder
        contract = self._contract(ticker)
        self.ib.qualifyContracts(contract)
        order = MarketOrder("BUY", quantity)
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(2)
        fill_price = trade.orderStatus.avgFillPrice or ref_price
        return OrderResult(
            success=trade.orderStatus.status in ("Filled", "Submitted"),
            ticker=ticker, side="BUY", quantity=quantity,
            fill_price=float(fill_price), commission=0.0,
            timestamp=datetime.utcnow().isoformat(),
            message=trade.orderStatus.status,
        )

    def sell(self, ticker: str, quantity: int, ref_price: float, **kwargs) -> OrderResult:
        from ib_insync import MarketOrder
        contract = self._contract(ticker)
        self.ib.qualifyContracts(contract)
        order = MarketOrder("SELL", quantity)
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(2)
        fill_price = trade.orderStatus.avgFillPrice or ref_price
        return OrderResult(
            success=trade.orderStatus.status in ("Filled", "Submitted"),
            ticker=ticker, side="SELL", quantity=quantity,
            fill_price=float(fill_price), commission=0.0,
            timestamp=datetime.utcnow().isoformat(),
            message=trade.orderStatus.status,
        )
