"""Micro-benchmark: cancel cost when one price level is deep.

The main order flow keeps levels shallow, so cancel cost barely shows there.
This script puts K orders on a single price and cancels them in random order,
which is where an O(k) cancel (M1 deque) and an O(1) cancel (M2 OrderedDict)
differ.

Usage: uv run python bench/deep_cancel.py --k 20000
"""

from __future__ import annotations

import argparse
import random
from time import perf_counter_ns

from lobsim import OrderBook, Side


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    book = OrderBook()
    for oid in range(1, args.k + 1):
        book.add_limit(oid, Side.SELL, 100, 1)
    ids = list(range(1, args.k + 1))
    random.Random(args.seed).shuffle(ids)

    t0 = perf_counter_ns()
    for oid in ids:
        book.cancel(oid)
    elapsed = perf_counter_ns() - t0
    print(f"k={args.k:,}: {elapsed / args.k:,.0f} ns per cancel (mean)")


if __name__ == "__main__":
    main()
