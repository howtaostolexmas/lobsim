"""FastOrderBook: the C++ engine with the same Python API as OrderBook."""

from __future__ import annotations

from lobsim._core import CoreOrderBook
from lobsim.types import Event, Side


class FastOrderBook:
    def __init__(self) -> None:
        self._core = CoreOrderBook()

    def add_limit(self, order_id: int, side: Side, price: int, qty: int) -> list[Event]:
        raise NotImplementedError

    def add_market(self, order_id: int, side: Side, qty: int) -> list[Event]:
        raise NotImplementedError

    def cancel(self, order_id: int) -> list[Event]:
        raise NotImplementedError

    def best_bid(self) -> int | None:
        raise NotImplementedError

    def best_ask(self) -> int | None:
        raise NotImplementedError

    def depth(self, side: Side, n: int) -> list[tuple[int, int]]:
        raise NotImplementedError

    def resting_qty(self, order_id: int) -> int | None:
        raise NotImplementedError
