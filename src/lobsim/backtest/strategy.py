"""Strategy interface. Subclass and override the hooks you need."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
