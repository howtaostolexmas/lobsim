"""FastOrderBook: the C++ engine with the same Python API as OrderBook.

The C++ side returns plain tuples (cheap to build). This wrapper turns them
into the same event dataclasses the Python engine returns, so callers and the
shared test suite cannot tell the two engines apart.
"""

from __future__ import annotations

from lobsim._core import CoreOrderBook
from lobsim.types import Accepted, Cancelled, Event, Rejected, Side, Trade

# Index = the C++ lob::Reason enum value.
_REASONS = ("non_positive_qty", "non_positive_price", "duplicate_id", "unknown_id")


def _to_events(raw: list[tuple]) -> list[Event]:
    events: list[Event] = []
    for t in raw:
        kind = t[0]
        if kind == "T":
            _, maker, taker, price, qty, is_buy, maker_rem, taker_rem = t
            side = Side.BUY if is_buy else Side.SELL
            events.append(Trade(maker, taker, price, qty, side, maker_rem, taker_rem))
        elif kind == "A":
            events.append(Accepted(t[1]))
        elif kind == "C":
            events.append(Cancelled(t[1], t[2]))
        else:
            events.append(Rejected(t[1], _REASONS[t[2]]))
    return events


class FastOrderBook:
    def __init__(self) -> None:
        self._core = CoreOrderBook()

    def add_limit(self, order_id: int, side: Side, price: int, qty: int) -> list[Event]:
        return _to_events(self._core.add_limit(order_id, side is Side.BUY, price, qty))

    def add_market(self, order_id: int, side: Side, qty: int) -> list[Event]:
        return _to_events(self._core.add_market(order_id, side is Side.BUY, qty))

    def cancel(self, order_id: int) -> list[Event]:
        return _to_events(self._core.cancel(order_id))

    def best_bid(self) -> int | None:
        return self._core.best_bid()

    def best_ask(self) -> int | None:
        return self._core.best_ask()

    def depth(self, side: Side, n: int) -> list[tuple[int, int]]:
        return self._core.depth(side is Side.BUY, n)

    def resting_qty(self, order_id: int) -> int | None:
        return self._core.resting_qty(order_id)


def encode_ops(ops: list[tuple]) -> list[tuple]:
    """Turn bench ops into the (kind, id, is_buy, price, qty) tuples run_ops takes."""
    out = []
    for op in ops:
        if op[0] == "limit":
            out.append((0, op[1], op[2] is Side.BUY, op[3], op[4]))
        elif op[0] == "market":
            out.append((1, op[1], op[2] is Side.BUY, 0, op[3]))
        else:
            out.append((2, op[1], False, 0, 0))
    return out
