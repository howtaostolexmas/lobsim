"""Check the replayed book against the source's own top-25 snapshots.

Tardis also publishes `book_snapshot_25`: the top 25 bid and ask levels,
recorded every time any of them changes, built from the same feed. If our
replay is right, then after applying every L2 batch that arrived up to a
snapshot's local timestamp, our top 25 must equal that snapshot exactly.
This is the strongest check we can make without another data source.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from lobsim.level_book import LevelBook
from lobsim.marketdata.instrument import Instrument
from lobsim.marketdata.replay import Replayer, batches
from lobsim.marketdata.tardis import L2Row, open_text
from lobsim.types import Side

Levels = list[tuple[int, int]]  # (price, qty), best first


class Snapshot(NamedTuple):
    ts_local: int
    bids: Levels
    asks: Levels


@dataclass
class ValidationReport:
    checked: int = 0
    matched: int = 0
    crossed_moments: int = 0  # snapshots at which our replayed book was crossed
    first_mismatches: list[dict] = field(default_factory=list)


def read_snapshots(path: Path, inst: Instrument, depth: int = 25) -> Iterator[Snapshot]:
    import csv

    with open_text(path) as f:
        for rec in csv.DictReader(f):
            yield Snapshot(
                int(rec["local_timestamp"]),
                _levels(rec, "bids", inst, depth),
                _levels(rec, "asks", inst, depth),
            )


def _levels(rec: dict, side: str, inst: Instrument, depth: int) -> Levels:
    out: Levels = []
    for i in range(depth):
        price = rec[f"{side}[{i}].price"]
        if not price:  # fewer than `depth` levels on this side
            break
        out.append((inst.price(price), inst.qty(rec[f"{side}[{i}].amount"])))
    return out


def top_uncrossed(book: LevelBook, n: int) -> tuple[Levels, Levels]:
    """Top n levels per side, after removing crossed levels.

    A replayed L2 book can be briefly crossed (best bid >= best ask) when
    updates for the two sides arrive in separate messages. The source drops
    crossed levels from its snapshots; as an approximation of its (undocumented)
    rule we remove the best bid and best ask together while they cross. Not yet
    checked against real data: the 2026-11-01 replay was never crossed.
    """
    bids = book.depth(Side.BUY, n + 50)  # extra room: crossed levels may be dropped
    asks = book.depth(Side.SELL, n + 50)
    while bids and asks and bids[0][0] >= asks[0][0]:
        bids.pop(0)
        asks.pop(0)
    return bids[:n], asks[:n]


def validate(
    l2_rows: Iterable[L2Row], snapshots: Iterable[Snapshot], n: int = 25, keep: int = 5
) -> ValidationReport:
    report = ValidationReport()
    replayer = Replayer()
    pending = batches(l2_rows)
    next_batch = next(pending, None)

    for snap in snapshots:
        # Apply everything that arrived up to (and including) this snapshot's time.
        while next_batch is not None and next_batch.ts_local <= snap.ts_local:
            replayer.apply(next_batch)
            next_batch = next(pending, None)

        book = replayer.book
        bid, ask = book.best_bid(), book.best_ask()
        if bid is not None and ask is not None and bid >= ask:
            report.crossed_moments += 1

        bids, asks = top_uncrossed(book, n)
        report.checked += 1
        if bids == snap.bids[:n] and asks == snap.asks[:n]:
            report.matched += 1
        elif len(report.first_mismatches) < keep:
            report.first_mismatches.append(
                {
                    "ts_local": snap.ts_local,
                    "expected_bids": snap.bids[:n],
                    "actual_bids": bids,
                    "expected_asks": snap.asks[:n],
                    "actual_asks": asks,
                }
            )
    return report
