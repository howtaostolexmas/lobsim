"""SimExchange: the replayed market plus our own simulated orders.

The market book comes from historical L2 data and is never changed by our
orders (no market impact). Our orders live beside it. For each resting order
we keep `ahead`: an estimate of how much quantity is queued in front of us at
our price. L2 data has no individual orders, so this has to be a model:

  - Join: ahead = visible quantity at our price (0 if we improve the price).
  - Trade at our price, taker on the other side: ahead -= trade qty. Once it
    goes below zero, the excess fills us (as maker).
  - Trade through our price: we would have been hit first -> fill fully.
  - Level shrinks to q: ahead = min(ahead, q). We assume the size that left
    was in front of us (optimistic). Using min() also means a trade and the
    book update that reports the same trade never count twice.
  - Market moves through us (opposite best reaches our price): fill fully.
"""

from __future__ import annotations

from dataclasses import dataclass

from lobsim.backtest.types import Liquidity, Notice, OrderAccepted, OrderCancelled, OrderFilled, OrderRejected
from lobsim.level_book import LevelBook
from lobsim.marketdata.replay import BookBatch, Replayer, TradePrint
from lobsim.types import Side


@dataclass(slots=True, eq=False)
class _SimOrder:
    order_id: int
    side: Side
    price: int
    qty: int  # remaining
    ahead: int  # estimated quantity queued in front of us


class SimExchange:
    def __init__(self) -> None:
        self._replayer = Replayer()
        self._orders: dict[int, _SimOrder] = {}  # insertion order = submission order

    @property
    def book(self) -> LevelBook:
        """The replayed market book (exchange-side, live). Never given to strategies."""
        return self._replayer.book

    # ---- market data ------------------------------------------------------

    def on_book(self, batch: BookBatch) -> list[Notice]:
        self._replayer.apply(batch)
        if not self._replayer.ready or not self._orders:
            return []
        if batch.is_snapshot:
            # The whole book was rebuilt: cap every order by its level's new size
            # (0 if the level is gone).
            for o in self._orders.values():
                o.ahead = min(o.ahead, self.book.qty_at(o.side, o.price))
        else:
            for side, price, qty in batch.levels:
                for o in self._orders.values():
                    if o.side is side and o.price == price:
                        o.ahead = min(o.ahead, qty)
        return self._fill_if_market_crossed_us(batch.ts_local)

    def on_trade(self, trade: TradePrint) -> list[Notice]:
        if trade.side is None:  # unknown aggressor: can't tell which side was hit
            return []
        maker_side = trade.side.opposite
        out: list[Notice] = []
        for o in list(self._orders.values()):
            if o.side is not maker_side:
                continue
            if _better_for_taker_than(o, trade.price):
                out.append(self._fill(trade.ts_local, o, o.qty))  # traded through us
            elif trade.price == o.price:
                o.ahead -= trade.qty
                if o.ahead < 0:
                    filled = min(-o.ahead, o.qty)
                    o.ahead = 0
                    out.append(self._fill(trade.ts_local, o, filled))
        return out

    # ---- our orders ---------------------------------------------------------

    def submit_limit(self, ts: int, order_id: int, side: Side, price: int, qty: int) -> list[Notice]:
        reason = self._validate(order_id, qty, price)
        if reason:
            return [OrderRejected(ts, order_id, reason)]
        ahead = self.book.qty_at(side, price)  # 0 if we are a new, better price
        self._orders[order_id] = _SimOrder(order_id, side, price, qty, ahead)
        return [OrderAccepted(ts, order_id)]

    def cancel(self, ts: int, order_id: int) -> list[Notice]:
        o = self._orders.pop(order_id, None)
        if o is None:  # never existed, already filled, or already cancelled
            return [OrderRejected(ts, order_id, "unknown_order")]
        return [OrderCancelled(ts, order_id, o.qty)]

    def queue_ahead(self, order_id: int) -> int | None:
        o = self._orders.get(order_id)
        return None if o is None else o.ahead

    # ---- internals ----------------------------------------------------------

    def _validate(self, order_id: int, qty: int, price: int | None) -> str | None:
        if qty <= 0:
            return "non_positive_qty"
        if price is not None and price <= 0:
            return "non_positive_price"
        if order_id in self._orders:
            return "duplicate_id"
        return None

    def _fill(self, ts: int, o: _SimOrder, qty: int) -> OrderFilled:
        o.qty -= qty
        if o.qty == 0:
            del self._orders[o.order_id]
        return OrderFilled(ts, o.order_id, o.side, o.price, qty, Liquidity.MAKER, o.qty)

    def _fill_if_market_crossed_us(self, ts: int) -> list[Notice]:
        bid, ask = self.book.best_bid(), self.book.best_ask()
        out: list[Notice] = []
        for o in list(self._orders.values()):
            if o.side is Side.BUY and ask is not None and ask <= o.price:
                out.append(self._fill(ts, o, o.qty))
            elif o.side is Side.SELL and bid is not None and bid >= o.price:
                out.append(self._fill(ts, o, o.qty))
        return out


def _better_for_taker_than(o: _SimOrder, trade_price: int) -> bool:
    """Did the trade print at a price worse for us than our own? Then we were skipped."""
    if o.side is Side.BUY:
        return trade_price < o.price  # someone sold below our bid
    return trade_price > o.price  # someone bought above our ask
