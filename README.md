# lobsim

A limit order book and matching engine in Python, built step by step toward a
backtester that replays real crypto market data with realistic fills.

The focus is engineering: an engine that is **provably correct** (property-based
tests against a reference implementation), then **measurably fast** (profiling and a
C++ core in M2), then **realistic** (latency, queue position and fees in M4).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

```python
from lobsim import OrderBook, Side

book = OrderBook()
book.add_limit(1, Side.SELL, price=101, qty=5)
book.add_limit(2, Side.SELL, price=102, qty=5)
for event in book.add_limit(3, Side.BUY, price=102, qty=7):
    print(event)
print(book.depth(Side.SELL, 5))
print(book.depth(Side.BUY, 5))
```

Output:

```
Accepted(order_id=3)
Trade(maker_id=1, taker_id=3, price=101, qty=5, aggressor=<Side.BUY: 'buy'>, maker_remaining=0, taker_remaining=2)
Trade(maker_id=2, taker_id=3, price=102, qty=2, aggressor=<Side.BUY: 'buy'>, maker_remaining=3, taker_remaining=0)
[(102, 3)]
[]
```

The buy order sweeps the 101 level, then takes 2 at 102. It is fully filled, so
nothing rests on the bid side.

## What it does (M1)

- **`OrderBook`**: limit orders, market orders and cancels with **price-time
  priority**. Every command returns a list of events: `Accepted`, `Rejected`,
  `Trade`, `Cancelled`.
- **`LevelBook`**: an L2 book holding only total quantity per price, for replaying
  market data later.
- Both are built on **`PriceLevels`**, a sorted map from price to value that
  iterates best price first.

## Design decisions

- **Integer prices and quantities.** Prices are in ticks and quantities are in lots.
  Floats would make `0.1 + 0.2 != 0.3` a matching bug.
- **Data structures.** Each side is a `SortedDict` of price → `deque` of orders, plus a
  dict from order id to order:
  - Best price: O(log n).
  - Matching at a level: pops from the front of a deque, O(1).
  - Finding an order to cancel: O(1).
- **Trades happen at the maker's price.** The resting order set the price; the
  incoming order accepted it. This is how real exchanges work.
- **No separate partial-fill event.** A `Trade` carries `maker_remaining` and
  `taker_remaining`, so a partial fill is any trade where those are above zero.
- **Market orders never rest.** Any unfilled remainder is reported as `Cancelled`.
- **Fixed rejection reasons.** The reasons are `non_positive_qty`, `non_positive_price`,
  `duplicate_id` and `unknown_id`, always checked in that order. An id is only reserved
  while its order rests in the book.
- **How correctness is checked.** `tests/reference.py` is a deliberately naive engine:
  one list, scanned in full for every match. It is too simple to be subtly wrong.
  [Hypothesis](https://hypothesis.readthedocs.io/) generates thousands of random
  order sequences and requires both engines to emit identical events and depth.
  Other properties check that the book never stays crossed and that every order's
  quantity is conserved.
- **Known cost: cancel is O(orders at that price).** The order is found in O(1), but
  removing it from the middle of a deque is a scan. This is the first profiling
  target in M2.

## Roadmap

- **M1** ✅ Correct order book and matching engine (this)
- **M2** Profile, optimize, and port the hot path to C++ via pybind11, with benchmarks
- **M3** Download and replay real L2 crypto market data
- **M4** Backtester with latency, queue position and fees

See `docs/superpowers/specs/` for the full design and `docs/notes/` for study notes.
