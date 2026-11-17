"""OrderBook: a limit order book with price-time priority matching."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from lobsim.levels import PriceLevels
from lobsim.types import Accepted, Cancelled, Event, Rejected, Side, Trade


@dataclass(slots=True, eq=False)  # compare by identity: deque.remove finds this exact object
class _Resting:
    """An order sitting in the book. Mutable: qty shrinks as it gets filled."""

    order_id: int
    side: Side
    price: int
    qty: int


class OrderBook:
    """Order-level book. Each price level is a FIFO queue (deque) of orders.

    Data structures:
      - one PriceLevels[deque[_Resting]] per side, for best-price access
      - a dict from order_id to _Resting, so cancel finds an order in O(1)
    """

    def __init__(self) -> None:
        self._levels: dict[Side, PriceLevels[deque[_Resting]]] = {
            Side.BUY: PriceLevels(Side.BUY),
            Side.SELL: PriceLevels(Side.SELL),
        }
        self._orders: dict[int, _Resting] = {}

    # ---- commands ---------------------------------------------------------

    def add_limit(self, order_id: int, side: Side, price: int, qty: int) -> list[Event]:
        events: list[Event] = [Accepted(order_id)]
        trades, remaining = self._match(order_id, side, qty, limit_price=price)
        events.extend(trades)
        if remaining > 0:
            self._rest(_Resting(order_id, side, price, remaining))
        return events

    def cancel(self, order_id: int) -> list[Event]:
        order = self._orders.pop(order_id, None)
        if order is None:
            return [Rejected(order_id, "unknown_id")]
        levels = self._levels[order.side]
        queue = levels.get(order.price)
        # Finding the order inside its level is O(number of orders at that
        # price). Fine for M1; it is the first thing to profile in M2.
        queue.remove(order)
        if not queue:
            levels.remove(order.price)
        return [Cancelled(order_id, order.qty)]

    # ---- queries ----------------------------------------------------------

    def best_bid(self) -> int | None:
        return self._levels[Side.BUY].best()

    def best_ask(self) -> int | None:
        return self._levels[Side.SELL].best()

    def depth(self, side: Side, n: int) -> list[tuple[int, int]]:
        """Top n price levels as (price, total qty), best first."""
        result: list[tuple[int, int]] = []
        for price, queue in self._levels[side].items_from_best():
            if len(result) >= n:
                break
            result.append((price, sum(o.qty for o in queue)))
        return result

    def resting_qty(self, order_id: int) -> int | None:
        order = self._orders.get(order_id)
        return None if order is None else order.qty

    # ---- internals --------------------------------------------------------

    def _match(
        self, taker_id: int, side: Side, qty: int, limit_price: int | None
    ) -> tuple[list[Trade], int]:
        """Fill an incoming order against the opposite side.

        Walks levels from the best price and orders within a level in arrival
        order (price-time priority). limit_price=None means "no price limit"
        (a market order). Returns the trades and the unfilled quantity.
        """
        opposite = self._levels[side.opposite]
        trades: list[Trade] = []
        remaining = qty
        while remaining > 0:
            best = opposite.best()
            if best is None or not _crosses(side, limit_price, best):
                break
            queue = opposite.get(best)
            maker = queue[0]
            fill = min(remaining, maker.qty)
            maker.qty -= fill
            remaining -= fill
            trades.append(Trade(maker.order_id, taker_id, best, fill, side, maker.qty, remaining))
            if maker.qty == 0:
                queue.popleft()
                del self._orders[maker.order_id]
                if not queue:
                    opposite.remove(best)
        return trades, remaining

    def _rest(self, order: _Resting) -> None:
        levels = self._levels[order.side]
        queue = levels.get(order.price)
        if queue is None:
            queue = deque()
            levels.set(order.price, queue)
        queue.append(order)
        self._orders[order.order_id] = order


def _crosses(side: Side, limit_price: int | None, opposite_best: int) -> bool:
    """Can an order on `side` with `limit_price` trade at `opposite_best`?"""
    if limit_price is None:
        return True
    if side is Side.BUY:
        return limit_price >= opposite_best
    return limit_price <= opposite_best
