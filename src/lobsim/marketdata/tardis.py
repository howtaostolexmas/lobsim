"""Tardis.dev CSV files: typed row readers (and, later, the downloader).

Column layouts (timestamps are microseconds since the Unix epoch, UTC):
  incremental_book_L2: exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount
  trades:              exchange,symbol,timestamp,local_timestamp,id,side,price,amount
`timestamp` is the exchange's clock; `local_timestamp` is when the message
arrived at Tardis's collector. In L2 files `amount` is the level's new total
size (0 removes the level), not a change.
"""

from __future__ import annotations

import csv
import gzip
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, TextIO

from lobsim.marketdata.instrument import Instrument
from lobsim.types import Side


class L2Row(NamedTuple):
    ts_exchange: int
    ts_local: int
    is_snapshot: bool
    side: Side
    price: int
    qty: int


class TradeRow(NamedTuple):
    ts_exchange: int
    ts_local: int
    side: Side | None  # the taker's side; None when the exchange doesn't say
    price: int
    qty: int
    trade_id: str


_BOOK_SIDES = {"bid": Side.BUY, "ask": Side.SELL}
_TRADE_SIDES = {"buy": Side.BUY, "sell": Side.SELL, "unknown": None}


def _open_text(path: Path) -> TextIO:
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="")
    return path.open("r", newline="")


def read_l2(path: Path, inst: Instrument) -> Iterator[L2Row]:
    with _open_text(path) as f:
        reader = csv.DictReader(f)
        for rec in reader:
            try:
                yield L2Row(
                    int(rec["timestamp"]),
                    int(rec["local_timestamp"]),
                    rec["is_snapshot"] == "true",
                    _lookup(_BOOK_SIDES, rec["side"]),
                    inst.price(rec["price"]),
                    inst.qty(rec["amount"]),
                )
            except ValueError as e:
                raise ValueError(f"{path}, line {reader.line_num}: {e}") from None


def read_trades(path: Path, inst: Instrument) -> Iterator[TradeRow]:
    with _open_text(path) as f:
        reader = csv.DictReader(f)
        for rec in reader:
            try:
                yield TradeRow(
                    int(rec["timestamp"]),
                    int(rec["local_timestamp"]),
                    _lookup(_TRADE_SIDES, rec["side"]),
                    inst.price(rec["price"]),
                    inst.qty(rec["amount"]),
                    rec["id"],
                )
            except ValueError as e:
                raise ValueError(f"{path}, line {reader.line_num}: {e}") from None


def _lookup(table: dict, key: str):
    try:
        return table[key]
    except KeyError:
        raise ValueError(f"unknown side {key!r}") from None
