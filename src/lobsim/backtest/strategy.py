"""Strategy interface. Subclass and override the hooks you need."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lobsim.types import Side

if TYPE_CHECKING:
    from lobsim.backtest.engine import Context
    from lobsim.backtest.types import Notice
    from lobsim.marketdata.replay import BookBatch, TradePrint


class Strategy:
    """All hooks are no-ops by default.

    The strategy only ever receives a Context: its clock (`ctx.now`), its own
    delayed copy of the book (`ctx.book`), its position/cash, and methods to
    send orders. Everything it is shown has already "arrived" by ctx.now.
    """

    def on_start(self, ctx: Context) -> None: ...

    def on_book(self, ctx: Context, batch: BookBatch) -> None:
        """Called after ctx.book has applied `batch`."""

    def on_trade(self, ctx: Context, trade: TradePrint) -> None: ...

    def on_notice(self, ctx: Context, notice: Notice) -> None:
        """Called after ctx.position / cash / open_orders reflect `notice`."""

    def on_end(self, ctx: Context) -> None: ...


class NaiveMarketMaker(Strategy):
    """Quote one bid at the best bid and one ask at the best ask.

    Deliberately naive, to exercise the backtester rather than to make money:
      - when a best price moves, cancel that side and re-quote once the
        cancel is confirmed (never two live quotes on one side)
      - don't quote a side that could push |position| past max_position_lots
    """

    def __init__(self, size_lots: int = 100, max_position_lots: int = 1_000) -> None:
        self.size = size_lots
        self.max_position = max_position_lots
        self._quote_id = {Side.BUY: None, Side.SELL: None}
        self._cancelling: set[int] = set()

    def on_book(self, ctx: Context, batch: BookBatch) -> None:
        self._requote(ctx)

    def on_notice(self, ctx: Context, notice: Notice) -> None:
        if notice.order_id not in ctx.open_orders:  # the order is finished: no cancel pending
            self._cancelling.discard(notice.order_id)
        self._requote(ctx)

    def _requote(self, ctx: Context) -> None:
        bid, ask = ctx.book.best_bid(), ctx.book.best_ask()
        if bid is None or ask is None or bid >= ask:
            return
        for side, target in ((Side.BUY, bid), (Side.SELL, ask)):
            allowed = (
                ctx.position + self.size <= self.max_position
                if side is Side.BUY
                else ctx.position - self.size >= -self.max_position
            )
            oid = self._quote_id[side]
            order = ctx.open_orders.get(oid) if oid is not None else None
            if order is not None:
                if (order.price != target or not allowed) and oid not in self._cancelling:
                    ctx.cancel(oid)
                    self._cancelling.add(oid)
                continue  # wait until the old quote is gone before placing a new one
            if allowed:
                self._quote_id[side] = ctx.place_limit(side, target, self.size)
