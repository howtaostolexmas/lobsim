"""Tardis.dev CSV files: download them, and read them as typed rows.

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
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import BinaryIO, NamedTuple, TextIO

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
_SNAPSHOT_FLAGS = {"true": True, "false": False}


def open_text(path: Path) -> TextIO:
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="")
    return path.open("r", newline="")


def read_l2(path: Path, inst: Instrument) -> Iterator[L2Row]:
    with open_text(path) as f:
        reader = csv.DictReader(f)
        for rec in reader:
            try:
                yield L2Row(
                    int(rec["timestamp"]),
                    int(rec["local_timestamp"]),
                    _lookup(_SNAPSHOT_FLAGS, rec["is_snapshot"], "is_snapshot"),
                    _lookup(_BOOK_SIDES, rec["side"]),
                    inst.price(rec["price"]),
                    inst.qty(rec["amount"]),
                )
            except ValueError as e:
                raise ValueError(f"{path}, line {reader.line_num}: {e}") from None


def read_trades(path: Path, inst: Instrument) -> Iterator[TradeRow]:
    with open_text(path) as f:
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


def _lookup(table: dict, key: str, what: str = "side"):
    try:
        return table[key]
    except KeyError:
        raise ValueError(f"unknown {what} {key!r}") from None


# ---- downloading -------------------------------------------------------------

BASE_URL = "https://datasets.tardis.dev/v1"
GZIP_MAGIC = b"\x1f\x8b"


def tardis_url(exchange: str, data_type: str, day: date, symbol: str) -> str:
    return f"{BASE_URL}/{exchange}/{data_type}/{day:%Y/%m/%d}/{symbol}.csv.gz"


def _urllib_fetch(url: str, out: BinaryIO) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "lobsim"})
    with urllib.request.urlopen(request, timeout=60) as response:
        shutil.copyfileobj(response, out, length=1 << 20)


def download(
    exchange: str,
    data_type: str,
    day: date,
    symbol: str,
    dest_dir: Path,
    fetch: Callable[[str, BinaryIO], None] = _urllib_fetch,
) -> Path:
    """Download one Tardis CSV file, or return it if it is already there.

    The file is written to a .part name first and renamed only once complete
    and checked, so an interrupted download never looks like a finished one.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{exchange}_{data_type}_{day:%Y-%m-%d}_{symbol}.csv.gz"
    if final.exists():
        return final

    part = final.with_name(final.name + ".part")
    url = tardis_url(exchange, data_type, day, symbol)
    try:
        with part.open("wb") as out:
            fetch(url, out)
        with part.open("rb") as f:
            if f.read(2) != GZIP_MAGIC:
                raise ValueError(f"{url} did not return a gzip file")
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        if e.code == 401:
            raise RuntimeError(
                f"{url}: 401 Unauthorized. Without an API key, Tardis only serves "
                "day 1 of each month (e.g. 2026-11-01)."
            ) from None
        raise
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(final)
    return final
