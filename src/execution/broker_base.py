"""Interfaccia astratta del broker."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OrderResult:
    success: bool
    ticker: str
    side: str
    quantity: float
    fill_price: float
    commission: float
    timestamp: str
    message: str = ""


@dataclass
class Position:
    ticker: str
    quantity: float
    avg_price: float


class Broker(ABC):
    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def buy(self, ticker: str, quantity: int, ref_price: float) -> OrderResult: ...

    @abstractmethod
    def sell(self, ticker: str, quantity: int, ref_price: float) -> OrderResult: ...
