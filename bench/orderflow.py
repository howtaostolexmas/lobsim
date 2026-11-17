"""Seeded synthetic order flow for benchmarks.

The goal is a flow that looks roughly like a real book: most limit orders
rest a few ticks away from the mid, some cross, many get cancelled, and a few
market orders sweep the top. The same seed always gives the same ops, so
before/after timings compare like with like.
"""

from __future__ import annotations

import random

from lobsim import Side

Op = tuple

START_MID = 10_000
P_MID_MOVE = 0.1  # chance per op that the mid moves by one tick
P_LIMIT, P_CANCEL = 0.60, 0.30  # market orders take the remaining 10%
P_CROSS = 0.10  # share of limit orders priced to cross by one tick
GEOMETRIC_P = 0.3  # distance from mid in ticks ~ geometric(0.3), so mostly 1-3


def generate(n: int, seed: int = 42) -> list[Op]:
    rng = random.Random(seed)  # private generator: never touches global random
    mid = START_MID
    next_id = 1
    live: list[int] = []  # ids we may cancel (list + swap-remove = O(1) random pick)
    ops: list[Op] = []

    for _ in range(n):
        if rng.random() < P_MID_MOVE:
            mid += rng.choice((-1, 1))

        r = rng.random()
        if r < P_LIMIT or (r < P_LIMIT + P_CANCEL and not live):
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            if rng.random() < P_CROSS:
                price = mid + 1 if side is Side.BUY else mid - 1
            else:
                offset = _geometric(rng, GEOMETRIC_P)
                price = mid - offset if side is Side.BUY else mid + offset
            ops.append(("limit", next_id, side, price, rng.randint(1, 100)))
            live.append(next_id)
            next_id += 1
        elif r < P_LIMIT + P_CANCEL:
            i = rng.randrange(len(live))
            live[i], live[-1] = live[-1], live[i]
            ops.append(("cancel", live.pop()))
        else:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            ops.append(("market", next_id, side, rng.randint(1, 200)))
            next_id += 1
    return ops


def _geometric(rng: random.Random, p: float) -> int:
    """Number of trials until the first success: 1, 2, 3, ..."""
    k = 1
    while rng.random() > p:
        k += 1
    return k


def apply(book, op: Op) -> list:
    """Send one op to any engine with the OrderBook API."""
    kind = op[0]
    if kind == "limit":
        return book.add_limit(op[1], op[2], op[3], op[4])
    if kind == "market":
        return book.add_market(op[1], op[2], op[3])
    return book.cancel(op[1])
