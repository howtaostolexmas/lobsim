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

`uv sync` also compiles the C++ engine (`lobsim._core`) with your system compiler;
CMake is fetched automatically if you don't have it. `pyproject.toml` lists the C++
sources as uv cache keys, so after you edit any file in `src/cpp/`, the next
`uv run ...` rebuilds the extension. To force a rebuild:
`uv sync --reinstall-package lobsim`.

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

## Performance (M2)

Apple M3, 1M-op seeded workload, median of 5 runs. Full method and tables are in
[docs/benchmarks/M2.md](docs/benchmarks/M2.md).

| Engine | Throughput |
|---|---:|
| Python (M1 baseline) | 0.79M ops/sec |
| C++ called per op from Python | 1.32M ops/sec |
| C++ with the whole op list inside C++ | **16.2M ops/sec (20×)** |

The headline lesson: per-op calls from Python spend about 92% of their time on the
Python↔C++ boundary and harness, not in the engine. Also, making cancel O(1) in Python cut a deep-level cancel
from 29 µs to 0.4 µs, but did nothing on the shallow main workload. Measure before
optimizing.

## Real market data (M3)

The project replays a full day of Binance BTCUSDT order-book data from
[Tardis.dev](https://tardis.dev). That day has 10.2M L2 updates and 1.1M trades, and the
replay keeps them in exact integer ticks on one arrival-time clock. The rebuilt book was
checked against the source's own top-25 snapshots: **463,716 of 463,716 matched exactly.**
Details and the chart are in [docs/benchmarks/M3-replay.md](docs/benchmarks/M3-replay.md).

```bash
uv run python -m lobsim.marketdata download  --day 2026-11-01 --symbol BTCUSDT   # ~86 MB
uv run python -m lobsim.marketdata normalize --day 2026-11-01 --symbol BTCUSDT
uv run python -m lobsim.marketdata validate  --day 2026-11-01 --symbol BTCUSDT
uv run --group viz python -m lobsim.marketdata stats --day 2026-11-01 --symbol BTCUSDT --chart out.png
```

Without an API key, Tardis serves only day 1 of each month. Its terms forbid
redistributing the raw data, so `data/` is git-ignored, the test fixtures are synthetic,
and only aggregate charts and statistics are committed.

## What it does (M1)

- **`OrderBook`**: limit orders, market orders and cancels with **price-time
  priority**. Every command returns a list of events: `Accepted`, `Rejected`,
  `Trade`, `Cancelled`.
- **`LevelBook`**: an L2 book holding only total quantity per price, for replaying
  market data later.
- **`FastOrderBook`**: the same engine in C++ (`src/cpp/`), with an identical API.
- Both Python books are built on **`PriceLevels`**, a sorted map from price to value that
  iterates best price first.

How this carries into the backtester (M4): real L2 data has no individual orders, so
the replayed market lives in a `LevelBook`. The order-level matching logic is reused
only for *our own* orders, together with a queue-position model. The shared core is
`PriceLevels`.

## Design decisions

- **Integer prices and quantities.** Prices are in ticks and quantities are in lots.
  Floats would make `0.1 + 0.2 != 0.3` a matching bug.
- **Data structures.** Each side is a `SortedDict` of price → level (an `OrderedDict` of
  orders plus a cached total), and there is a dict from order id to order:
  - Best price: O(1) in practice (sortedcontainers reads the first/last key directly).
  - Adding a new price level: O(log n).
  - Matching at a level: takes the oldest order, O(1).
  - Cancel: O(1) to find and O(1) to remove.
- **Trades happen at the maker's price.** The resting order set the price; the
  incoming order accepted it. This is how real exchanges work.
- **No separate partial-fill event.** A `Trade` carries `maker_remaining` and
  `taker_remaining`, so a partial fill is any trade where those are above zero.
- **Market orders never rest.** Any unfilled remainder is reported as `Cancelled`.
- **Fixed rejection reasons.** New orders are checked in a fixed order: `non_positive_qty`,
  then `non_positive_price` (limit orders only), then `duplicate_id`. A cancel can only
  fail with `unknown_id`. An id is only reserved while its order rests in the book.
- **How correctness is checked.** `tests/reference.py` is a deliberately naive engine:
  one list, scanned in full for every match. It is too simple to be subtly wrong.
  [Hypothesis](https://hypothesis.readthedocs.io/) generates hundreds of random
  order sequences per run (500 for the reference comparison, up to 200 operations
  each) and requires both engines to emit identical events and depth.
  Other properties check that the book never stays crossed and that every order's
  quantity is conserved.
- **Cancel is O(1)** (since M2). Each level is an `OrderedDict`, which is a hash
  table plus a linked list, keyed by order id, with a cached total quantity. In M1 it
  was a `deque`, where cancel had to scan the level.
- **Two engines, one test suite.** `OrderBook` (Python) and `FastOrderBook` (C++ via
  pybind11) both run every test, including the property tests, through a parametrized
  `make_book` fixture.

## Roadmap

- **M1** ✅ Correct order book and matching engine (this)
- **M2** ✅ Profile, optimize, and port the engine to C++ via pybind11, with benchmarks
- **M3** ✅ Download and replay real L2 crypto market data
- **M4** Backtester with latency, queue position and fees

See `docs/superpowers/specs/` for the full design and `docs/notes/` for study notes.
