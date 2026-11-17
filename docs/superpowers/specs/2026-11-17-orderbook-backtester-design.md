# Order Book Engine → Market-Replay Backtester — Design

Date: 2026-11-17
Status: Draft, awaiting approval

## Intent and the why

A portfolio project for a university student applying to **quant developer internships**.
The owner is strongest in Python.

The project exists to give interviewers something concrete to dig into on GitHub and in
interviews. Quant developer interviewers care about **systems engineering**: correct,
well-tested, well-designed code with measured performance and reasons behind each design
decision. Strategy profitability is irrelevant to them.

Time is not constrained. The real risk is the opposite: a sprawling project that never
finishes. So the work is cut into **milestones that each ship on their own**. After any
milestone the repo is complete and resume-ready: tests pass, README explains it, benchmark
numbers are published.

## Goals

1. A limit order book and matching engine that is **provably correct**: price-time priority,
   with tests that check it against a simple reference implementation.
2. **Measured performance**, with a story of finding a bottleneck and fixing it: profile
   the Python, optimize it, then move the hot path to C++ (pybind11) and show the speedup
   with a reproducible benchmark.
3. A **backtester that replays real historical market data** (crypto L2 order book updates
   plus trades) and simulates the fills our own orders would realistically get: latency,
   queue position, fees.
4. A README per milestone that explains design decisions and trade-offs in plain English.
   Interviewers will ask about these decisions.

## Non-goals

- A profitable strategy. The example strategy only exercises the system.
- Live trading or connecting to a real exchange with real money.
- A GUI or web dashboard. Plots go in the README as static images.
- Many asset classes or venues. One crypto pair on one venue's data is enough.
- Advanced order types early on (stop, iceberg, FOK/IOC). They can come later as extensions.
- Distributed or multi-process architecture.

## Anti-goals (would make it a failure even if it works)

- **It looks like a notebook project.** Analysis cells with no package, tests, or CI.
- **Look-ahead bias in the backtester.** A strategy can see data stamped after its own clock.
  This would fail any quant interviewer's first question.
- **Benchmark numbers with no method.** Every number needs hardware, dataset, how many runs,
  and how it was measured.
- **The C++ and Python engines drift apart.** Both must pass the same test suite.
- **An unfinished sprawl.** A milestone half-done with nothing shippable.

## Constraints

- Solo student; free data and free tools only.
- Python is the primary language. C++ appears only behind a pybind11 boundary.
- Develops on macOS; must also build in Linux CI (GitHub Actions).
- Code, comments, and README in English.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A** | LOB + matching engine; Python first, hot path to C++ | Strongest dev signal and a clear performance story, plus C++ practice. Little market knowledge; a common project unless done in depth. |
| **B** | Event-driven backtester replaying real L2 data with realistic fills | Systems plus microstructure, a favorite interview topic. More complex; looks like "another backtrader" if shallow. |
| **C** | Live websocket pipeline + paper trading | A real running system. Time goes mostly to infra/ops, with less of a performance story. |

**Chosen: A, then B.** The matching engine (A) ships first and is resume-worthy alone.
It then becomes the core of the backtester (B), so the finished project covers both the
performance story and the market-microstructure story. C is a possible later extension
(a live feed can be one more event source).

## Decided

- Approach: A, then B (chosen by the owner).
- Order types for the core: **limit, market, cancel** (my call; others are extensions).
- Matching rule: **price-time priority** (FIFO at each price level).
- Language split: Python package; C++ core via **pybind11** in Milestone 2 (my call).
- Data: **free historical crypto data** with L2 incremental book updates and trades, for
  one liquid pair such as BTC-USDT (my call).
- Language of code and docs: English (my call).

## Milestones

Each milestone ends with tests green in CI, an updated README, and a resume bullet the
owner can honestly write.

### M1: Correct order book and matching engine (pure Python)

- Order book with bids and asks, price levels, FIFO queues, order lookup by ID for O(1)-ish cancel.
- Operations: add limit order, add market order, cancel. Output is an event stream:
  accepted, rejected, trade (with maker/taker IDs, price, qty), cancelled, partial fill.
- Integer prices and quantities (ticks/lots) to avoid floating-point error.
- Tests: unit tests for known scenarios, plus **property-based tests** (Hypothesis) that run
  random order sequences against a deliberately naive reference engine and require identical
  event streams. Invariants: book never crossed after matching, quantity is conserved, FIFO
  respected.
- Done when: the engine passes the property suite, and the README explains the data structures chosen.

### M2: Performance (profile → optimize → C++)

- A benchmark harness: replay N million synthetic orders with a realistic mix of add, cancel
  and market orders; report throughput and latency percentiles (p50/p99) per operation.
- Profile the Python version and fix what the profile shows. Record before/after.
- Port the core to C++ behind a pybind11 module with the same Python API. **The M1 test suite
  runs against both implementations** (parametrized).
- Publish a benchmark table with method: machine, dataset, warm-up, number of runs.
- Done when: the C++ engine passes the identical suite, and the README shows the speedup and why.

### M3: Market data replay

- Download and normalize one day (or a few) of real L2 incremental updates plus trades into a
  compact local format such as Parquet.
- Rebuild the market's order book from the updates, and check consistency with the data's
  snapshots and checksums when the source provides them.
- A single event clock: every event carries exchange and local timestamps, and replay is
  strictly ordered.
- Done when: a reconstructed book at any timestamp matches the source snapshot, and the
  README documents the data pipeline.

### M4: Backtester with realistic fills

- Event-driven loop: market data events and our own order events share one time-ordered queue.
- Strategy API: callbacks on book update and trade, and the strategy can place/cancel orders.
  The API must make it impossible to read data stamped after the current simulated time.
- Fill simulation:
  - Configurable **latency** (order-to-exchange and data-to-strategy).
  - **Queue position** for passive orders: a resting order joins behind the visible quantity at
    its price, and moves up only as trades/cancels at that level happen.
  - **Fees** (maker/taker) and aggressive orders walking the book.
- Output: trades log, PnL, inventory, fill ratio; a few static plots.
- Example strategy: a naive symmetric market maker, to exercise the system only.
- Tests: hand-built scenarios with known correct fills; a look-ahead test that fails if a
  strategy can observe future data.
- Done when: the example strategy runs end to end on real data reproducibly, and the README
  explains the fill model and its known limits.

### Later (optional extensions, not committed)

- More order types (IOC, FOK, post-only).
- A live websocket feed as another event source (approach C).
- Multi-pair or multi-venue replay.

## Left to the builder

- Exact data source. Candidates: Tardis.dev free monthly samples (has L2 incremental + trades),
  Binance public data (data.binance.vision). Pick one that has incremental L2 updates and is
  free. Check its licence allows putting derived results in a public repo.
- Data structures inside the book (sorted containers, dict of deques, arrays indexed by tick, etc.),
  as long as the choice is explained in the README.
- Packaging and build: e.g. `uv` or `pip` + `pyproject.toml`, `scikit-build-core` for the C++ module.
- Benchmark tooling (e.g. `pytest-benchmark` or a custom harness), and profiler choice.
- Repo layout and CI details.
- Queue-position model details, so long as the model and its limits are documented.
