"""CSV.gz -> Parquet, streamed in batches so a full day never sits in memory.

Why Parquet: it is columnar (we read only the columns we need), typed (int64
ticks stay int64, no re-parsing strings), compressed, and can be read back in
chunks (row groups). Sides are stored as small integers.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lobsim.marketdata.instrument import Instrument
from lobsim.marketdata.tardis import L2Row, TradeRow, read_l2, read_trades
from lobsim.types import Side

BATCH_ROWS = 500_000

L2_SCHEMA = pa.schema(
    [
        ("ts_exchange", pa.int64()),
        ("ts_local", pa.int64()),
        ("is_snapshot", pa.bool_()),
        ("side", pa.int8()),  # 0 bid, 1 ask
        ("price", pa.int64()),
        ("qty", pa.int64()),
    ]
)

TRADE_SCHEMA = pa.schema(
    [
        ("ts_exchange", pa.int64()),
        ("ts_local", pa.int64()),
        ("side", pa.int8()),  # 1 buy, -1 sell, 0 unknown
        ("price", pa.int64()),
        ("qty", pa.int64()),
        ("trade_id", pa.string()),
    ]
)

_BOOK_SIDE_CODE = {Side.BUY: 0, Side.SELL: 1}
_BOOK_SIDE_FROM_CODE = {0: Side.BUY, 1: Side.SELL}
_TRADE_SIDE_CODE = {Side.BUY: 1, Side.SELL: -1, None: 0}
_TRADE_SIDE_FROM_CODE = {1: Side.BUY, -1: Side.SELL, 0: None}


def normalize_l2(src: Path, dst: Path, inst: Instrument, batch_rows: int = BATCH_ROWS) -> int:
    def encode(r: L2Row) -> tuple:
        return (r.ts_exchange, r.ts_local, r.is_snapshot, _BOOK_SIDE_CODE[r.side], r.price, r.qty)

    return _write(read_l2(src, inst), encode, L2_SCHEMA, dst, batch_rows)


def normalize_trades(src: Path, dst: Path, inst: Instrument, batch_rows: int = BATCH_ROWS) -> int:
    def encode(r: TradeRow) -> tuple:
        return (r.ts_exchange, r.ts_local, _TRADE_SIDE_CODE[r.side], r.price, r.qty, r.trade_id)

    return _write(read_trades(src, inst), encode, TRADE_SCHEMA, dst, batch_rows)


def read_l2_parquet(path: Path) -> Iterator[L2Row]:
    for cols in _read_batches(path):
        for ts_e, ts_l, snap, side, price, qty in zip(*cols):
            yield L2Row(ts_e, ts_l, snap, _BOOK_SIDE_FROM_CODE[side], price, qty)


def read_trades_parquet(path: Path) -> Iterator[TradeRow]:
    for cols in _read_batches(path):
        for ts_e, ts_l, side, price, qty, tid in zip(*cols):
            yield TradeRow(ts_e, ts_l, _TRADE_SIDE_FROM_CODE[side], price, qty, tid)


def _write(rows: Iterable, encode, schema: pa.Schema, dst: Path, batch_rows: int) -> int:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")  # never leave a half-written file at dst
    count = 0
    buffer: list[tuple] = []
    with pq.ParquetWriter(tmp, schema) as writer:
        for row in rows:
            buffer.append(encode(row))
            if len(buffer) >= batch_rows:
                writer.write_table(_to_table(buffer, schema))
                count += len(buffer)
                buffer.clear()
        if buffer:
            writer.write_table(_to_table(buffer, schema))
            count += len(buffer)
    tmp.replace(dst)
    return count


def _to_table(rows: list[tuple], schema: pa.Schema) -> pa.Table:
    columns = list(zip(*rows))
    return pa.table([pa.array(c, type=f.type) for c, f in zip(columns, schema)], schema=schema)


def _read_batches(path: Path) -> Iterator[list[list]]:
    """Yield each row group as a list of Python lists, one per column."""
    pf = pq.ParquetFile(path)
    for i in range(pf.num_row_groups):
        table = pf.read_row_group(i)
        yield [col.to_pylist() for col in table.columns]
