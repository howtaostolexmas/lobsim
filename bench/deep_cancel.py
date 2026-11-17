"""Micro-benchmark: cancel cost when one price level is deep.

The main order flow keeps levels shallow, so cancel cost barely shows there.
This script puts K orders on a single price and cancels them in random order,
which is where an O(k) cancel (M1 deque) and an O(1) cancel (M2 OrderedDict)
differ.

Usage: uv run python bench/deep_cancel.py --k 20000 [--impl python|cpp]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from time import perf_counter_ns

from lobsim import OrderBook, Side


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--impl", choices=["python", "cpp"], default="python")
    ap.add_argument("--json", type=Path, help="append the result as one JSON line")
    args = ap.parse_args()

    if args.impl == "cpp":
        from lobsim.fast import FastOrderBook  # imported lazily: absent on M1 checkouts

        book = FastOrderBook()
    else:
        book = OrderBook()
    for oid in range(1, args.k + 1):
        book.add_limit(oid, Side.SELL, 100, 1)
    ids = list(range(1, args.k + 1))
    random.Random(args.seed).shuffle(ids)

    t0 = perf_counter_ns()
    for oid in ids:
        book.cancel(oid)
    elapsed = perf_counter_ns() - t0
    ns = elapsed / args.k
    print(f"{args.impl} k={args.k:,}: {ns:,.0f} ns per cancel (mean)")
    if args.json:
        with args.json.open("a") as f:
            f.write(json.dumps({"impl": args.impl, "k": args.k, "ns_per_cancel": ns}) + "\n")


if __name__ == "__main__":
    main()
