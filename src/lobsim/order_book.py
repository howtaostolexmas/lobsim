"""OrderBook: a limit order book with price-time priority matching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from lobsim.levels import PriceLevels
from lobsim.types import Accepted, Cancelled, Event, Rejected, Side, Trade


@dataclass(slots=True, eq=False)
class _Resting:
    """An order sitting in the book. Mutable: qty shrinks as it gets filled."""

    order_id: int
    side: Side
    price: int
    qty: int


@dataclass(slots=True, eq=False)
class _Level:
    """All resting orders at one price, oldest first, plus their total qty.

    Why OrderedDict (keyed by order id) instead of a deque or a plain dict:
      - deque: removing an order from the middle (cancel) is an O(k) scan.
      - plain dict: keeps insertion order, but deleting from the front leaves
        dummy slots that next(iter(d)) must skip, which can get slow.
      - OrderedDict: a hash table plus a doubly-linked list, so "oldest order",
        "pop oldest" and "delete by id" are all O(1).
    `total` is kept up to date on every change so depth() never re-sums.
    """

    orders: OrderedDict[int, _Resting] = field(default_factory=OrderedDict)
    total: int = 0


class OrderBook:
    """Order-level book with price-time priority.

    Data structures:
      - one PriceLevels[_Level] per side, for best-price access
      - a dict from order_id to _Resting, so cancel finds an order in O(1)
    """

    def __init__(self) -> None:
        # Two attributes instead of a dict keyed by Side: the M2 profile showed
        # hashing the Side enum on every lookup was a measurable cost.
        self._bids: PriceLevels[_Level] = PriceLevels(Side.BUY)
        self._asks: PriceLevels[_Level] = PriceLevels(Side.SELL)
        self._orders: dict[int, _Resting] = {}

    # ---- commands ---------------------------------------------------------

    def add_limit(self, order_id: int, side: Side, price: int, qty: int) -> list[Event]:
        reason = self._validate(order_id, qty, price)
        if reason is not None:
            return [Rejected(order_id, reason)]
        events: list[Event] = [Accepted(order_id)]
        trades, remaining = self._match(order_id, side, qty, limit_price=price)
        events.extend(trades)
        if remaining > 0:
            self._rest(_Resting(order_id, side, price, remaining))
        return events

    def add_market(self, order_id: int, side: Side, qty: int) -> list[Event]:
        """Fill as much as possible now; cancel the rest. Never rests."""
        reason = self._validate(order_id, qty, price=None)
        if reason is not None:
            return [Rejected(order_id, reason)]
        events: list[Event] = [Accepted(order_id)]
        trades, remaining = self._match(order_id, side, qty, limit_price=None)
        events.extend(trades)
        if remaining > 0:
            events.append(Cancelled(order_id, remaining))
        return events

    def cancel(self, order_id: int) -> list[Event]:
        order = self._orders.pop(order_id, None)
        if order is None:
            return [Rejected(order_id, "unknown_id")]
        levels = self._side(order.side)
        level = levels.get(order.price)
        del level.orders[order_id]  # O(1) thanks to OrderedDict
        level.total -= order.qty
        if not level.orders:
            levels.remove(order.price)
        return [Cancelled(order_id, order.qty)]

    # ---- queries ----------------------------------------------------------

    def best_bid(self) -> int | None:
        return self._bids.best()

    def best_ask(self) -> int | None:
        return self._asks.best()

    def depth(self, side: Side, n: int) -> list[tuple[int, int]]:
        """Top n price levels as (price, total qty), best first."""
        result: list[tuple[int, int]] = []
        for price, level in self._side(side).items_from_best():
            if len(result) >= n:
                break
            result.append((price, level.total))
        return result

    def resting_qty(self, order_id: int) -> int | None:
        order = self._orders.get(order_id)
        return None if order is None else order.qty

    # ---- internals --------------------------------------------------------

    def _side(self, side: Side) -> PriceLevels[_Level]:
        return self._bids if side is Side.BUY else self._asks

    def _validate(self, order_id: int, qty: int, price: int | None) -> str | None:
        """Return a rejection reason, or None if the order is valid.

        Checks run in a fixed order so the reason is deterministic.
        An id is only reserved while its order rests in the book.
        """
        if qty <= 0:
            return "non_positive_qty"
        if price is not None and price <= 0:
            return "non_positive_price"
        if order_id in self._orders:
            return "duplicate_id"
        return None

    def _match(
        self, taker_id: int, side: Side, qty: int, limit_price: int | None
    ) -> tuple[list[Trade], int]:
        """Fill an incoming order against the opposite side.

        Walks levels from the best price and orders within a level in arrival
        order (price-time priority). limit_price=None means "no price limit"
        (a market order). Returns the trades and the unfilled quantity.
        """
        opposite = self._side(side.opposite)
        trades: list[Trade] = []
        remaining = qty
        while remaining > 0:
            top = opposite.best_item()  # one sorted-dict lookup, not two
            if top is None:
                break
            best, level = top
            if not _crosses(side, limit_price, best):
                break
            maker = next(iter(level.orders.values()))  # oldest order at this price
            fill = min(remaining, maker.qty)
            maker.qty -= fill
            level.total -= fill
            remaining -= fill
            trades.append(Trade(maker.order_id, taker_id, best, fill, side, maker.qty, remaining))
            if maker.qty == 0:
                level.orders.popitem(last=False)
                del self._orders[maker.order_id]
                if not level.orders:
                    opposite.remove(best)
        return trades, remaining

    def _rest(self, order: _Resting) -> None:
        levels = self._side(order.side)
        level = levels.get(order.price)
        if level is None:
            level = _Level()
            levels.set(order.price, level)
        level.orders[order.order_id] = order
        level.total += order.qty
        self._orders[order.order_id] = order


def _crosses(side: Side, limit_price: int | None, opposite_best: int) -> bool:
    """Can an order on `side` with `limit_price` trade at `opposite_best`?"""
    if limit_price is None:
        return True
    if side is Side.BUY:
        return limit_price >= opposite_best
    return limit_price <= opposite_best
