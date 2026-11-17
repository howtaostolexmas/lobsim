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
    was in front of us (optimistic).
  - The same trade usually shows up twice: as a trade print and as the level
    shrinking. Trade first, then book: min() makes the book update a no-op.
    Book first, then trade at the *same* µs: the drop is remembered and the
    trade is first matched against it, so it is not subtracted again. A drop
    and a trade at *different* µs are treated as separate events, which can
    over-count (an optimistic, documented limit).
  - Market moves through us (opposite best reaches our price): fill fully.

Aggressive orders (market orders, or limits that cross on arrival) fill at
once against the *visible* levels, best first, never taking more than a
level shows, and pay the taker fee. Any remainder is cancelled (IOC): since
we never remove liquidity from the replayed book, a resting remainder would
otherwise keep matching the same quantity again and again.
"""

from __future__ import annotations

from dataclasses import dataclass

from lobsim.backtest.types import (
    Liquidity,
    Notice,
    OrderAccepted,
    OrderCancelled,
    OrderFilled,
    OrderRejected,
    SimConfig,
)
from lobsim.level_book import LevelBook
from lobsim.marketdata.instrument import Instrument
from lobsim.marketdata.replay import BookBatch, Replayer, TradePrint
from lobsim.types import Side


@dataclass(slots=True, eq=False)
class _SimOrder:
    order_id: int
    side: Side
    price: int
    qty: int  # remaining
    ahead: int  # estimated quantity queued in front of us
    drop_ts: int = -1  # µs of the last book update that shrank `ahead`
    drop_qty: int = 0  # by how much: a same-µs trade at our price is matched against this


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
                    if o.side is side and o.price == price and qty < o.ahead:
                        if o.drop_ts != batch.ts_local:
                            o.drop_ts, o.drop_qty = batch.ts_local, 0
                        o.drop_qty += o.ahead - qty
                        o.ahead = qty
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
                traded = trade.qty
                if o.drop_ts == trade.ts_local:  # the book already showed this trade
                    seen = min(traded, o.drop_qty)
                    o.drop_qty -= seen
                    traded -= seen
                o.ahead -= traded
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
        opposite_best = self._opposite_best(side)
        if opposite_best is not None and _crosses(side, price, opposite_best):
            return self._take(ts, order_id, side, qty, limit_price=price)
        ahead = self.book.qty_at(side, price)  # 0 if we are a new, better price
        self._orders[order_id] = _SimOrder(order_id, side, price, qty, ahead)
        return [OrderAccepted(ts, order_id)]

    def submit_market(self, ts: int, order_id: int, side: Side, qty: int) -> list[Notice]:
        reason = self._validate(order_id, qty, price=None)
        if reason:
            return [OrderRejected(ts, order_id, reason)]
        return self._take(ts, order_id, side, qty, limit_price=None)

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

    def _opposite_best(self, side: Side) -> int | None:
        return self.book.best_ask() if side is Side.BUY else self.book.best_bid()

    def _take(
        self, ts: int, order_id: int, side: Side, qty: int, limit_price: int | None
    ) -> list[Notice]:
        """Fill against visible opposite levels, best first; cancel the rest (IOC)."""
        out: list[Notice] = [OrderAccepted(ts, order_id)]
        remaining = qty
        for price, visible in self.book.levels(side.opposite):
            if remaining == 0 or (limit_price is not None and not _crosses(side, limit_price, price)):
                break
            filled = min(remaining, visible)
            remaining -= filled
            out.append(OrderFilled(ts, order_id, side, price, filled, Liquidity.TAKER, remaining))
        if remaining:
            out.append(OrderCancelled(ts, order_id, remaining))
        return out

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


def _crosses(side: Side, limit_price: int, opposite_price: int) -> bool:
    return limit_price >= opposite_price if side is Side.BUY else limit_price <= opposite_price


def fee_quote(price: int, qty: int, liquidity: Liquidity, inst: Instrument, cfg: SimConfig) -> float:
    """Fee in quote currency (e.g. USDT) for one fill. Negative means a rebate."""
    bps = cfg.maker_fee_bps if liquidity is Liquidity.MAKER else cfg.taker_fee_bps
    notional = price * 10**-inst.price_decimals * qty * 10**-inst.qty_decimals
    return notional * bps / 10_000


def _better_for_taker_than(o: _SimOrder, trade_price: int) -> bool:
    """Did the trade print at a price worse for us than our own? Then we were skipped."""
    if o.side is Side.BUY:
        return trade_price < o.price  # someone sold below our bid
    return trade_price > o.price  # someone bought above our ask
