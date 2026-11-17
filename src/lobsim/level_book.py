"""LevelBook: an L2 book that stores only total quantity per price.

Market data feeds (L2) report "price P now has quantity Q" without any
individual orders. LevelBook mirrors that. It shares PriceLevels with the
order-level OrderBook, and will hold the replayed market book in M3/M4.
"""

from __future__ import annotations

from itertools import islice

from lobsim.levels import PriceLevels
from lobsim.types import Side


class LevelBook:
    def __init__(self) -> None:
        self._levels: dict[Side, PriceLevels[int]] = {
            Side.BUY: PriceLevels(Side.BUY),
            Side.SELL: PriceLevels(Side.SELL),
        }

    def set_level(self, side: Side, price: int, qty: int) -> None:
        """Replace the quantity at a price. qty 0 removes the level.

        We deliberately do not reject a crossed book (best bid >= best ask):
        real feeds can cross briefly between updates, and the replay should
        reflect the data, not hide it.
        """
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        if qty < 0:
            raise ValueError(f"qty must not be negative, got {qty}")
        levels = self._levels[side]
        if qty == 0:
            if price in levels:
                levels.remove(price)
        else:
            levels.set(price, qty)

    def qty_at(self, side: Side, price: int) -> int:
        return self._levels[side].get(price) or 0

    def best_bid(self) -> int | None:
        return self._levels[Side.BUY].best()

    def best_ask(self) -> int | None:
        return self._levels[Side.SELL].best()

    def depth(self, side: Side, n: int) -> list[tuple[int, int]]:
        return list(islice(self._levels[side].items_from_best(), n))

    def clear(self) -> None:
        for side in self._levels:
            self._levels[side] = PriceLevels(side)
