"""The event loop: replayed market data, our orders, and two kinds of latency.

Two clocks are in play:
  - Exchange time. Market events are applied to SimExchange at their
    ts_local. Our orders reach it `order_latency_us` after we send them.
  - Strategy time (ctx.now). Market events and exchange notices reach the
    strategy `feed_latency_us` after they happen at the exchange.

Everything waits on one heap ordered by (time, priority, seq). Market events
are not put on the heap: they are pulled lazily from the (sorted) input
stream, so a full day never sits in memory. Priorities at the same µs:
  0  market data applied to the exchange   (the market moves first:
  1  our order arriving at the exchange     pessimistic for us)
  2  delivery to the strategy
`seq` keeps insertion order among equal (time, priority).

Why the strategy cannot look ahead: it never holds a reference to the
exchange or to the input stream. Its Context has a read-only view of a
*separate* book that only changes when a delayed delivery is processed.
"""

from __future__ import annotations

import heapq
import itertools
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from lobsim.backtest.exchange import SimExchange, fee_quote
from lobsim.backtest.result import Result
from lobsim.backtest.strategy import Strategy
from lobsim.backtest.types import (
    Notice,
    OrderAccepted,
    OrderCancelled,
    OrderFilled,
    OrderRejected,
    SimConfig,
)
from lobsim.marketdata.instrument import BTCUSDT, Instrument
from lobsim.marketdata.replay import BookBatch, Replayer, TradePrint
from lobsim.types import Side

_ARRIVE, _DELIVER = 1, 2  # heap priorities (0 is market data, handled outside the heap)


class BookView:
    """Read-only window onto the strategy's own, delayed copy of the book."""

    __slots__ = ("_book",)

    def __init__(self, book) -> None:
        self._book = book

    def best_bid(self) -> int | None:
        return self._book.best_bid()

    def best_ask(self) -> int | None:
        return self._book.best_ask()

    def depth(self, side: Side, n: int) -> list[tuple[int, int]]:
        return self._book.depth(side, n)


@dataclass(slots=True)
class OpenOrder:
    """An order as the strategy knows it (from notices delivered so far)."""

    order_id: int
    side: Side
    price: int | None  # None for market orders
    remaining: int
    live: bool = False  # True once OrderAccepted has been delivered


class Context:
    """The only object a strategy touches.

    Order requests go into an outbox that the engine drains after each
    callback, so the context needs no reference to the engine or exchange.
    """

    def __init__(self, book_view: BookView) -> None:
        self.now = 0
        self.book = book_view
        self.position = 0  # lots, positive = long
        self.cash = 0  # tick*lot units (exact); divide by 10**(price_dec+qty_dec) for quote
        self.fees = 0.0  # quote currency
        self.open_orders: dict[int, OpenOrder] = {}
        self._outbox: list[tuple] = []
        self._next_id = 1

    def place_limit(self, side: Side, price: int, qty: int) -> int:
        oid = self._new_id()
        self.open_orders[oid] = OpenOrder(oid, side, price, qty)
        self._outbox.append(("limit", oid, side, price, qty))
        return oid

    def place_market(self, side: Side, qty: int) -> int:
        oid = self._new_id()
        self.open_orders[oid] = OpenOrder(oid, side, None, qty)
        self._outbox.append(("market", oid, side, qty))
        return oid

    def cancel(self, order_id: int) -> None:
        self._outbox.append(("cancel", order_id))

    def _new_id(self) -> int:
        oid = self._next_id
        self._next_id += 1
        return oid


class Backtest:
    def __init__(
        self,
        strategy: Strategy,
        config: SimConfig = SimConfig(),
        inst: Instrument = BTCUSDT,
        sample_every_us: int = 600_000_000,  # equity curve: one point per 10 minutes
    ):
        self.strategy = strategy
        self.config = config
        self.inst = inst
        self.sample_every_us = sample_every_us

    def run(self, market_events: Iterable[BookBatch | TradePrint]) -> Result:
        cfg = self.config
        exchange = SimExchange()
        replica = Replayer()  # the strategy's delayed copy of the book
        ctx = Context(BookView(replica.book))
        result = Result()
        heap: list[tuple] = []
        seq = itertools.count()

        def push(time: int, priority: int, kind: str, payload) -> None:
            heapq.heappush(heap, (time, priority, next(seq), kind, payload))

        def deliver(notices: list[Notice]) -> None:
            for n in notices:
                push(n.ts + cfg.feed_latency_us, _DELIVER, "notice", n)

        def flush_outbox() -> None:
            for request in ctx._outbox:
                if request[0] != "cancel":
                    result.orders_placed += 1
                    result.qty_placed += request[-1]
                push(ctx.now + cfg.order_latency_us, _ARRIVE, "order", request)
            ctx._outbox.clear()

        started = time.perf_counter()
        step = self.sample_every_us
        unit = 10**-self.inst.price_decimals * 10**-self.inst.qty_decimals
        result.unit_quote = unit

        def sample(at: int) -> None:
            mid = _mid(exchange)
            if mid is not None:
                pnl = (ctx.cash + ctx.position * mid) * unit - ctx.fees
                result.equity.append((at, ctx.position, pnl))

        market: Iterator = iter(market_events)
        next_market = next(market, None)
        next_sample = None
        if next_market is not None:
            ctx.now = next_market.ts_local
            next_sample = next_market.ts_local - next_market.ts_local % step + step
        self.strategy.on_start(ctx)
        flush_outbox()

        while next_market is not None or heap:
            # Market data (priority 0) goes first when it is due at or before the heap top.
            if next_market is not None and (not heap or (next_market.ts_local, 0) <= heap[0][:2]):
                ev = next_market
                next_market = next(market, None)
                result.market_events += 1
                while ev.ts_local >= next_sample:  # state as of each boundary we pass
                    sample(next_sample)
                    next_sample += step
                if isinstance(ev, BookBatch):
                    notices = exchange.on_book(ev)
                else:
                    notices = exchange.on_trade(ev)
                push(ev.ts_local + cfg.feed_latency_us, _DELIVER, "market", ev)
                deliver(notices)
                continue

            at, _prio, _seq, kind, payload = heapq.heappop(heap)
            if kind == "order":
                deliver(self._arrive(exchange, at, payload))
                continue

            ctx.now = at
            if kind == "market":
                if isinstance(payload, BookBatch):
                    replica.apply(payload)
                    self.strategy.on_book(ctx, payload)
                else:
                    self.strategy.on_trade(ctx, payload)
            else:
                self._apply_notice(ctx, payload, result)
                self.strategy.on_notice(ctx, payload)
            flush_outbox()

        self.strategy.on_end(ctx)
        result.final_position = ctx.position
        result.final_cash = ctx.cash
        result.fees_quote = ctx.fees
        result.final_mid = _mid(exchange)
        result.runtime_s = time.perf_counter() - started
        return result

    @staticmethod
    def _arrive(exchange: SimExchange, time: int, request: tuple) -> list[Notice]:
        kind = request[0]
        if kind == "limit":
            _, oid, side, price, qty = request
            return exchange.submit_limit(time, oid, side, price, qty)
        if kind == "market":
            _, oid, side, qty = request
            return exchange.submit_market(time, oid, side, qty)
        return exchange.cancel(time, request[1])

    def _apply_notice(self, ctx: Context, n: Notice, result: Result) -> None:
        order = ctx.open_orders.get(n.order_id)
        if isinstance(n, OrderAccepted):
            if order is not None:
                order.live = True
        elif isinstance(n, OrderFilled):
            signed = n.qty if n.side is Side.BUY else -n.qty
            ctx.position += signed
            result.max_abs_position = max(result.max_abs_position, abs(ctx.position))
            ctx.cash -= signed * n.price
            ctx.fees += fee_quote(n.price, n.qty, n.liquidity, self.inst, self.config)
            result.fills.append(n)
            if order is not None:
                order.remaining = n.remaining
                if n.remaining == 0:
                    del ctx.open_orders[n.order_id]
        elif isinstance(n, (OrderCancelled, OrderRejected)):
            ctx.open_orders.pop(n.order_id, None)


def _mid(exchange: SimExchange) -> float | None:
    bid, ask = exchange.book.best_bid(), exchange.book.best_ask()
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2
