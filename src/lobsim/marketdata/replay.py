"""Replay historical market data on one event clock.

Two inputs, both sorted by arrival time:
  - L2 rows, grouped into BookBatch objects (one exchange message each)
  - trades, as TradePrint objects
`events()` merges them into a single stream ordered by `ts_local`, the time
the data arrived. That is what a strategy would have seen, so it is the
clock a backtest must use. (Sorting by exchange time instead would let a
strategy react to data before it could have arrived: look-ahead bias.)

Tie-break when a book batch and a trade arrive at the same ts_local: the
book batch first. Within each source, file order is kept.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from lobsim.level_book import LevelBook
from lobsim.marketdata.tardis import L2Row, TradeRow
from lobsim.types import Side


@dataclass(frozen=True, slots=True)
class BookBatch:
    """L2 rows that share ts_local and is_snapshot: one exchange message."""

    ts_local: int
    ts_exchange: int  # of the batch's first row
    is_snapshot: bool
    levels: tuple[tuple[Side, int, int], ...]  # (side, price, new total qty)


@dataclass(frozen=True, slots=True)
class TradePrint:
    ts_local: int
    ts_exchange: int
    side: Side | None  # taker side
    price: int
    qty: int
    trade_id: str


def batches(rows: Iterable[L2Row]) -> Iterator[BookBatch]:
    current: list[L2Row] = []
    for row in rows:
        if current and (row.ts_local, row.is_snapshot) != (current[0].ts_local, current[0].is_snapshot):
            yield _make_batch(current)
            current = []
        current.append(row)
    if current:
        yield _make_batch(current)


def _make_batch(rows: list[L2Row]) -> BookBatch:
    first = rows[0]
    levels = tuple((r.side, r.price, r.qty) for r in rows)
    return BookBatch(first.ts_local, first.ts_exchange, first.is_snapshot, levels)


def trade_prints(rows: Iterable[TradeRow]) -> Iterator[TradePrint]:
    for r in rows:
        yield TradePrint(r.ts_local, r.ts_exchange, r.side, r.price, r.qty, r.trade_id)


_BOOK, _TRADE = 0, 1  # source rank: on equal ts_local, book batches go first


def events(
    l2_rows: Iterable[L2Row], trade_rows: Iterable[TradeRow]
) -> Iterator[BookBatch | TradePrint]:
    """Merge book batches and trades into one stream ordered by ts_local."""
    keyed_books = _keyed(batches(l2_rows), _BOOK, "L2")
    keyed_trades = _keyed(trade_prints(trade_rows), _TRADE, "trades")
    for _key, event in heapq.merge(keyed_books, keyed_trades, key=lambda pair: pair[0]):
        yield event


def _keyed(items: Iterable, rank: int, name: str) -> Iterator[tuple[tuple[int, int, int], object]]:
    """Attach the sort key (ts_local, source rank, position) and check sortedness.

    heapq.merge assumes each input is already sorted; if one is not, it would
    silently produce a wrong order, so we fail loudly instead.
    """
    last = None
    for seq, item in enumerate(items):
        if last is not None and item.ts_local < last:
            raise ValueError(f"{name} input is not sorted by ts_local: {item.ts_local} after {last}")
        last = item.ts_local
        yield (item.ts_local, rank, seq), item


class Replayer:
    """Applies BookBatch objects to a LevelBook using Tardis snapshot rules.

    - Non-snapshot batches before the first snapshot are skipped: without a
      starting picture, updates to individual levels are meaningless.
    - A snapshot that follows non-snapshot data means "start over": the book
      is cleared first. A snapshot can span several batches in a row, so we
      clear only on the transition into a snapshot, not on every batch.
    """

    def __init__(self) -> None:
        self.book = LevelBook()
        self.ready = False
        self.last_ts_local: int | None = None
        self._in_snapshot = False

    def apply(self, batch: BookBatch) -> None:
        self.last_ts_local = batch.ts_local
        if batch.is_snapshot:
            if not self._in_snapshot:
                self.book.clear()
            self._in_snapshot = True
            self.ready = True
        else:
            self._in_snapshot = False
            if not self.ready:
                return
        for side, price, qty in batch.levels:
            self.book.set_level(side, price, qty)
