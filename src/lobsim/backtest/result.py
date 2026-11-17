"""Backtest results: fills, exact cash/position, fees, PnL and an equity curve."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lobsim.backtest.types import Liquidity, OrderFilled
from lobsim.marketdata.instrument import Instrument


@dataclass
class Result:
    fills: list[OrderFilled] = field(default_factory=list)  # as delivered to the strategy
    orders_placed: int = 0  # limit + market orders sent
    qty_placed: int = 0  # lots
    fees_quote: float = 0.0  # quote currency; negative = net rebate
    final_position: int = 0  # lots
    final_cash: int = 0  # exact, in tick*lot units
    max_abs_position: int = 0
    final_mid: float | None = None  # exchange-side mid at the end, in ticks
    market_events: int = 0
    runtime_s: float = 0.0
    unit_quote: float = 10**-2 * 10**-5  # value of one tick*lot (BTCUSDT default); set by Backtest
    # (sample time us, position lots, mark-to-mid PnL in quote currency after fees)
    equity: list[tuple[int, int, float]] = field(default_factory=list)

    @property
    def qty_filled(self) -> int:
        return sum(f.qty for f in self.fills)

    @property
    def maker_qty(self) -> int:
        return sum(f.qty for f in self.fills if f.liquidity is Liquidity.MAKER)

    @property
    def taker_qty(self) -> int:
        return sum(f.qty for f in self.fills if f.liquidity is Liquidity.TAKER)

    @property
    def fill_ratio(self) -> float:
        return self.qty_filled / self.qty_placed if self.qty_placed else 0.0

    def pnl_quote(self, mark_price: float) -> float:
        """Cash plus position valued at `mark_price` (ticks), minus fees, in quote currency.

        cash and position are exact integers; only the final conversion to
        quote currency (and the fees) are floats.
        """
        return (self.final_cash + self.final_position * mark_price) * self.unit_quote - self.fees_quote

    def summary(self, inst: Instrument) -> dict:
        mark = self.final_mid if self.final_mid is not None else 0.0
        pnl = self.pnl_quote(mark)
        lot = 10**-inst.qty_decimals
        return {
            "pnl_quote": pnl,
            "gross_pnl_quote": pnl + self.fees_quote,
            "fees_quote": self.fees_quote,
            "fills": len(self.fills),
            "orders_placed": self.orders_placed,
            "fill_ratio": self.fill_ratio,
            "maker_qty": self.maker_qty * lot,
            "taker_qty": self.taker_qty * lot,
            "final_position": self.final_position * lot,
            "max_abs_position": self.max_abs_position * lot,
            "market_events": self.market_events,
            "runtime_s": self.runtime_s,
        }

    def chart(self, path: Path, title: str, inst: Instrument) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        t0 = self.equity[0][0]
        hours = [(ts - t0) / 3.6e9 for ts, _, _ in self.equity]
        lot = 10**-inst.qty_decimals
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 5), sharex=True)
        ax1.plot(hours, [p for _, _, p in self.equity], color="#2563eb")
        ax1.set_ylabel("PnL after fees (quote)")
        ax1.set_title(title)
        ax2.plot(hours, [pos * lot for _, pos, _ in self.equity], color="#ea580c")
        ax2.set_ylabel("position (base)")
        ax2.set_xlabel("hours since first sample")
        for ax in (ax1, ax2):
            ax.grid(alpha=0.3)
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=120)
