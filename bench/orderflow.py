"""Seeded synthetic order flow for benchmarks.

The goal is a flow that looks roughly like a real book: most limit orders
rest a few ticks away from the mid, some cross, many get cancelled, and a few
market orders sweep the top. The same seed always gives the same ops, so
before/after timings compare like with like.

To make cancels realistic (they should target orders that still rest), the
generator replays its own ops on a private OrderBook and drops ids as soon as
they are fully filled. This happens before any timing, so it costs nothing in
the benchmark itself.
"""

from __future__ import annotations

import random

from lobsim import OrderBook, Side, Trade

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
    live = _LiveSet()  # ids that currently rest in the book
    sim = OrderBook()  # private book used only to learn which orders filled
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
            op = ("limit", next_id, side, price, rng.randint(1, 100))
            next_id += 1
        elif r < P_LIMIT + P_CANCEL:
            op = ("cancel", live.pop_random(rng))
        else:
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            op = ("market", next_id, side, rng.randint(1, 200))
            next_id += 1

        ops.append(op)
        events = apply(sim, op)
        for e in events:
            if isinstance(e, Trade) and e.maker_remaining == 0:
                live.discard(e.maker_id)  # fully filled: no longer cancellable
        if op[0] == "limit" and sim.resting_qty(op[1]) is not None:
            live.add(op[1])
    return ops


class _LiveSet:
    """A set that also supports picking a uniformly random member in O(1).

    A list holds the members, a dict maps member -> its index in the list.
    Removing swaps the member with the last element and pops, so both
    add/remove and random pick are O(1).
    """

    def __init__(self) -> None:
        self._items: list[int] = []
        self._pos: dict[int, int] = {}

    def __bool__(self) -> bool:
        return bool(self._items)

    def add(self, x: int) -> None:
        self._pos[x] = len(self._items)
        self._items.append(x)

    def discard(self, x: int) -> None:
        i = self._pos.pop(x, None)
        if i is None:
            return
        last = self._items.pop()
        if i < len(self._items):  # x was not the last element: move last into its slot
            self._items[i] = last
            self._pos[last] = i

    def pop_random(self, rng: random.Random) -> int:
        x = self._items[rng.randrange(len(self._items))]
        self.discard(x)
        return x


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
