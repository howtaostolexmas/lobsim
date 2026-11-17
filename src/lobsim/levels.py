"""PriceLevels: a sorted map from price to a value, iterated best price first."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Generic, TypeVar

from sortedcontainers import SortedDict

from lobsim.types import Side

V = TypeVar("V")


class PriceLevels(Generic[V]):
    """Price levels for one side of a book.

    "Best" depends on the side: the highest bid, or the lowest ask. Prices are
    kept in ascending order and the bid side simply iterates in reverse. We do
    not store negated prices for bids, because that trick is easy to get wrong
    and harder to explain.
    """

    def __init__(self, side: Side) -> None:
        self.side = side
        self._levels: SortedDict = SortedDict()

    def get(self, price: int) -> V | None:
        return self._levels.get(price)

    def set(self, price: int, value: V) -> None:
        self._levels[price] = value

    def remove(self, price: int) -> None:
        del self._levels[price]  # raises KeyError if absent

    def best(self) -> int | None:
        if not self._levels:
            return None
        # SortedDict.peekitem is O(log n): index -1 is the highest key.
        index = -1 if self.side is Side.BUY else 0
        return self._levels.peekitem(index)[0]

    def items_from_best(self) -> Iterator[tuple[int, V]]:
        keys = self._levels.keys()
        ordered = reversed(keys) if self.side is Side.BUY else iter(keys)
        for price in ordered:
            yield price, self._levels[price]

    def __len__(self) -> int:
        return len(self._levels)

    def __contains__(self, price: object) -> bool:
        return price in self._levels
