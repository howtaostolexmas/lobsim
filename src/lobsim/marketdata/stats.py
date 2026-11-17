"""Replay a day of data and summarize it: counts, speed, spread, crossings."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from lobsim.marketdata.replay import BookBatch, Replayer, events
from lobsim.marketdata.tardis import L2Row, TradeRow

TEN_MINUTES_US = 600_000_000


@dataclass
class ReplayStats:
    l2_rows: int = 0
    batches: int = 0
    trades: int = 0
    crossed_batches: int = 0  # batches after which best bid >= best ask
    seconds: float = 0.0  # wall-clock replay time
    # Time-weighted spread: spread in ticks -> microseconds spent at that spread.
    spread_time_us: dict[int, int] = field(default_factory=dict)
    # Per 10-minute bucket with any two-sided time: (bucket start us, last two-sided
    # mid seen by the bucket's end, time-weighted mean spread). Buckets with no
    # two-sided, uncrossed time are left out.
    buckets: list[tuple[int, float, float]] = field(default_factory=list)


def replay_stats(
    l2_rows: Iterable[L2Row], trade_rows: Iterable[TradeRow], bucket_us: int = TEN_MINUTES_US
) -> ReplayStats:
    stats = ReplayStats()
    spread_time: Counter[int] = Counter()
    replayer = Replayer()

    # The spread in force since `since` (None until the book is two-sided).
    current_spread: int | None = None
    since = 0
    bucket_start: int | None = None
    bucket_weighted = 0  # sum of spread * duration within the bucket
    bucket_time = 0
    last_mid: float | None = None

    def close_bucket(start: int) -> None:
        nonlocal bucket_weighted, bucket_time
        if bucket_time and last_mid is not None:
            stats.buckets.append((start, last_mid, bucket_weighted / bucket_time))
        bucket_weighted = bucket_time = 0

    def account(until: int) -> None:
        """Advance the clock from `since` to `until`.

        Walks every 10-minute boundary on the way (closing finished buckets)
        whether or not a valid spread is in force, but only credits time to
        the spread statistics while the book is two-sided and uncrossed.
        """
        nonlocal since, bucket_start, bucket_weighted, bucket_time
        while since < until:
            boundary = bucket_start + bucket_us
            end = min(until, boundary)
            if current_spread is not None:
                spread_time[current_spread] += end - since
                bucket_weighted += current_spread * (end - since)
                bucket_time += end - since
            since = end
            if since == boundary:
                close_bucket(bucket_start)  # skipped inside if no two-sided time
                bucket_start = boundary

    started = time.perf_counter()
    counting_rows = _Counting(l2_rows)
    for event in events(counting_rows, trade_rows):
        if not isinstance(event, BookBatch):
            stats.trades += 1
            continue
        stats.batches += 1
        if bucket_start is None:
            # Align buckets to whole multiples of bucket_us since the epoch
            # (whole 10-minute marks in UTC) and start the clock here.
            bucket_start = event.ts_local - event.ts_local % bucket_us
            since = event.ts_local
        account(event.ts_local)
        replayer.apply(event)
        bid, ask = replayer.book.best_bid(), replayer.book.best_ask()
        if bid is None or ask is None:
            current_spread = None
            continue
        if bid >= ask:
            stats.crossed_batches += 1
            current_spread = None  # don't count crossed time as a (negative) spread
            continue
        current_spread = ask - bid
        last_mid = (bid + ask) / 2
    if bucket_start is not None:
        close_bucket(bucket_start)
    stats.seconds = time.perf_counter() - started
    stats.l2_rows = counting_rows.count
    stats.spread_time_us = dict(sorted(spread_time.items()))
    return stats


def weighted_percentile(weights: dict[int, int], q: float) -> int:
    """Smallest value v such that at least q of the total weight is <= v."""
    total = sum(weights.values())
    running = 0
    for value, w in sorted(weights.items()):
        running += w
        if running >= q * total:
            return value
    raise ValueError("no data")


class _Counting:
    """Wraps an iterable and counts how many items were pulled from it."""

    def __init__(self, items: Iterable) -> None:
        self._it = iter(items)
        self.count = 0

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self._it)
        self.count += 1
        return item
