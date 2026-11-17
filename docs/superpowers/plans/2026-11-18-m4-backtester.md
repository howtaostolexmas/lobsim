# M4: Backtester with Realistic Fills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An event-driven backtester that replays the M3 market data. It simulates our own
orders with latency, queue position and fees, and runs a naive market maker on a full real
day, reproducibly.

**Architecture:**
- **One event loop** (`Backtest.run`) pulls market events lazily from the M3 `events()`
  stream. It interleaves them with its own scheduled events (order arrivals at the exchange,
  and notices to the strategy) on one heap keyed by time.
- **Two views of the market.** The **exchange side** (`SimExchange`) sees market data at
  `ts_local`. The **strategy** sees it `feed_latency_us` later, through its own delayed
  replica of the book, which it can only read. So the strategy cannot look ahead, *by
  construction*.
- **No market impact.** Our orders never change the replayed book.
- **Queue model for passive orders.** Our resting orders carry a `queue_ahead` estimate.
  Trades at our price shrink it, and drops in the level's size cap it. We fill only after
  `queue_ahead` is used up.

**Tech Stack:** Python ≥ 3.11 and uv; `heapq`; matplotlib (`viz` group) for one chart; pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-orderbook-backtester-design.md` (M4 section)

## Global Constraints

- **Time:** integer µs on the M3 `ts_local` axis. **Money:** cash is kept exactly as an
  integer in units of `tick × lot`. Fees are floats in quote currency (USDT). PnL is reported
  in USDT.
- **Same-µs tie order:**
  1. market data is applied to the exchange first
  2. then our orders arrive at the exchange
  3. then notices are delivered to the strategy

  This is pessimistic: the market moves before our order lands.
- **The strategy reads only what has been delivered to it.** It gets `ctx.now` (strategy
  clock), `ctx.book` (a read-only view of its delayed replica), and its own notices. It never
  gets a reference to `SimExchange` or the live replay.
- **Fee defaults:** `maker_fee_bps=10`, `taker_fee_bps=10` (Binance spot base tier, 0.1%).
  Rebates are allowed as negative values.
- **Latency defaults:** `order_latency_us=1_000`, `feed_latency_us=1_000`.
- Pure Python; no C++ changes. Readable over clever. Small commits that say why. English.
- Tests are offline and use small hand-built event sequences. The real data is used only in
  Task 5.

## Fill model (binding; the README "known limits" section must restate it)

1. **Aggressive orders** are market orders, or limit orders that cross the exchange-side best
   price when they arrive.
   - They fill at once against the **visible** levels of the replayed book. They walk from
     the best price, never take more than each level's visible quantity, and stop at the
     limit price.
   - They pay the **taker** fee.
   - Any remainder is **cancelled (IOC behaviour)**. Because there is no market impact, a
     resting remainder would keep "refilling" against the same visible liquidity.
2. **Passive limit orders** rest at their price with `queue_ahead` set as follows:
   - if the price is inside the spread (a new level): `queue_ahead = 0`
   - otherwise: the visible level quantity when the order arrives
3. **Market trade at our price.** A trade print at our price with the taker on the opposite
   side to us (a sell taker for our bid, a buy taker for our ask):
   - `queue_ahead -= trade.qty`
   - If the result goes below zero, the excess fills us, up to our remaining quantity, at our
     price, as **maker**. Then `queue_ahead` is reset to 0.
4. **Trade through our price.** If a trade with an opposite-side taker prints at a price
   *better than ours* (a sell below our bid, a buy above our ask), we would have been hit
   first. **Fully fill** the remainder at our price, as maker.
5. **Book update at our level.** `queue_ahead = min(queue_ahead, new_level_qty)`. Shrinking
   size is assumed to come from orders ahead of us, which is optimistic. A trade followed by
   its matching book update never double-counts, because of the `min`.
6. **Market moves through us.** If the exchange-side opposite best crosses our resting price
   after a book update, we fully fill at our price as maker.
7. Trades with unknown taker side are ignored by the queue model.
8. **Cancels** take effect only when they *arrive* at the exchange. If the order filled first,
   the cancel is rejected with `"unknown_order"`.

## Review Focus

1. **Latency boundaries.** The strategy must never see an event whose `ts_local +
   feed_latency_us > ctx.now`. An order sent at `t` must not interact with any market event
   at `ts_local < t + order_latency_us`. (Task 3)
2. **Queue accounting with a trade and the matching book update in the same µs.** No double
   fill and no double queue reduction, in either arrival order. (Task 1)
3. **A cancel racing a fill.** A fill that lands while a cancel is in flight must be reported,
   and the cancel must be rejected, not "succeed" on a filled order. Position must match the
   fills. (Task 3)
4. **Snapshot resets in the middle of a run.** When the replayed book is cleared and rebuilt,
   our resting orders keep their price. `queue_ahead` is capped by the new level size via
   rule 5. Nothing crashes on a level that has vanished. (Task 1)
5. **Money is exact.** Cash plus position marked at a price must equal the sum over fills,
   exactly, in integer units. The fee total must equal the sum of per-fill fees. (Task 4)

---

## File Structure

| File | Responsibility |
|---|---|
| `src/lobsim/backtest/__init__.py` | Public API |
| `src/lobsim/backtest/types.py` | `SimConfig`, `Liquidity`, notices: `OrderAccepted`, `OrderFilled`, `OrderCancelled`, `OrderRejected` |
| `src/lobsim/backtest/exchange.py` | `SimExchange`: replayed market book + our orders + the fill model |
| `src/lobsim/backtest/engine.py` | `Backtest`, `Context`, `BookView`: the event loop with latencies |
| `src/lobsim/backtest/strategy.py` | `Strategy` base class, `NaiveMarketMaker` |
| `src/lobsim/backtest/result.py` | `Result`: fills log, PnL, fees, inventory, fill ratio, equity samples, chart |
| `src/lobsim/backtest/__main__.py` | CLI for the real-data run |
| `tests/backtest/test_*.py` | Tests per module |
| `docs/benchmarks/M4-backtest.md`, `docs/notes/M4-backtester.md` | Results and study note |

---

### Task 1: Types and `SimExchange` passive fills (queue model)

**Files:**
- Create: `src/lobsim/backtest/__init__.py`, `types.py`, `exchange.py`
- Test: `tests/backtest/test_exchange_passive.py`

**Interfaces:**
- Consumes: `BookBatch`, `TradePrint`, `Replayer` (M3); `Side`.
- Produces:
  - `@dataclass(frozen=True) SimConfig(order_latency_us=1_000, feed_latency_us=1_000, maker_fee_bps=10.0, taker_fee_bps=10.0)`
  - `class Liquidity(Enum)`: `MAKER`, `TAKER`
  - Notices are frozen dataclasses that all carry `ts` (exchange time):
    - `OrderAccepted(ts, order_id)`
    - `OrderFilled(ts, order_id, side, price, qty, liquidity, remaining)`
    - `OrderCancelled(ts, order_id, remaining)`
    - `OrderRejected(ts, order_id, reason)`
  - `class SimExchange`:
    - `__init__(self)`, which owns a `Replayer` (from M3)
    - `on_book(batch) -> list[Notice]` applies the batch, then rules 5 and 6
    - `on_trade(trade) -> list[Notice]` applies rules 3, 4 and 7
    - `submit_limit(ts, order_id, side, price, qty) -> list[Notice]`
    - `submit_market(ts, order_id, side, qty) -> list[Notice]`
    - `cancel(ts, order_id) -> list[Notice]`
    - `queue_ahead(order_id) -> int | None`, a read-only helper for tests
  - Internal index: `dict[(Side, price), list[order_id]]`, so a book or trade event touches
    only our orders at the affected price.
  - Reasons: `"non_positive_qty"`, `"non_positive_price"`, `"duplicate_id"`, `"unknown_order"`.

- [ ] **Step 1: Write failing tests.** Each test builds a book with `BookBatch` snapshots, then
  submits a passive order:

```python
def test_rest_behind_visible_qty():           # bid level 100 has 5 → queue_ahead 5
def test_inside_spread_rests_with_zero_ahead():
def test_trade_at_price_reduces_queue_then_fills_excess():
    # ahead 5, our qty 3; sell trade 4 @100 → ahead 1, no fill; sell trade 3 @100 → fill 2 (maker), remaining 1
def test_buy_taker_trade_does_not_touch_our_bid():
def test_trade_through_fills_fully_as_maker():  # sell trade @99 while we bid 100 → fill all at 100
def test_level_shrink_caps_queue():             # level 5→2 → ahead 2
def test_trade_then_book_update_same_us_no_double_count():   # Review Focus 2
    # ahead 5, qty 3: trade 4 → ahead 1; book update level 5→1 → ahead stays 1 (min), no fill
def test_book_update_then_trade_same_us_no_double_count():   # Review Focus 2, other order
    # ahead 5, qty 3: level 5→1 → ahead 1; sell trade 4 @100 → ahead -3 → fill 3 (all), ahead 0
def test_market_moves_through_resting_bid():    # ask drops to 100 while we bid 100 → full maker fill
def test_snapshot_reset_keeps_order_and_caps_queue():        # Review Focus 4
def test_vanished_level_caps_queue_to_zero():
def test_unknown_side_trade_ignored():
def test_cancel_resting_and_cancel_unknown():
def test_validation_rejections():
```

  The executor writes every expected number into these tests from the fill model above.
- [ ] **Step 2: Run** `uv run pytest tests/backtest -q`. Expected: FAIL (import error).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(backtest): SimExchange with a queue-position model for passive orders`.

---

### Task 2: Aggressive orders and fees

**Files:**
- Modify: `src/lobsim/backtest/exchange.py`
- Test: `tests/backtest/test_exchange_aggressive.py`

**Interfaces:**
- Produces:
  - Aggressive fills, per rule 1. `submit_limit` returns, in order:
    1. `OrderAccepted`
    2. one `OrderFilled(liquidity=TAKER)` per level walked
    3. `OrderCancelled(remaining)` if anything is left
  - `fee_quote(price, qty, liquidity, inst, cfg) -> float` = `price*tick * qty*lot * bps / 10_000`.
    It lives in `exchange.py` and is used by the engine.
  - `submit_market` behaves the same with no price limit. On an empty opposite book it returns
    Accepted, then Cancelled(all).

- [ ] **Step 1: Write failing tests:**

```python
def test_marketable_limit_walks_visible_levels_up_to_limit():
    # asks 101x2, 102x3, 103x5; buy limit 102 qty 10 → fills 2@101, 3@102 taker; Cancelled(5)
def test_market_order_walks_all_levels_and_cancels_rest():
def test_aggressive_does_not_modify_replayed_book():   # no market impact
def test_aggressive_never_rests():                     # IOC: resting_qty is None afterwards
def test_fee_quote_maker_taker_and_rebate():
    # BTCUSDT tick 0.01, lot 1e-5: price 7_758_203 (77,582.03), qty 100_000 (1 BTC), 10 bps → 77.58203
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(backtest): aggressive IOC fills against visible depth, and maker/taker fees`.

---

### Task 3: Event loop with latency, strategy context and look-ahead guarantees

**Files:**
- Create: `src/lobsim/backtest/engine.py`, `src/lobsim/backtest/strategy.py` (base class only)
- Test: `tests/backtest/test_engine.py`

**Interfaces:**
- Consumes: `SimExchange`, notices, `SimConfig`; `events()` from M3.
- Produces:
  - `class Strategy`. Every hook is a no-op by default:
    - `on_start(ctx)`
    - `on_book(ctx)`, called after the strategy's replica has applied a delivered batch
    - `on_trade(ctx, trade: TradePrint)`
    - `on_notice(ctx, notice)`
    - `on_end(ctx)`
  - `class Context`, the only thing the strategy touches:
    - `now: int`
    - `book: BookView`, read-only: `best_bid()`, `best_ask()`, `depth(side, n)`
    - `position: int`, in lots
    - `cash: int`, in tick·lot units
    - `open_orders: dict[int, OpenOrder]`, as the strategy knows them from delivered notices
    - `place_limit(side, price, qty) -> int`, which returns a new order id
    - `place_market(side, qty) -> int`
    - `cancel(order_id) -> None`

    Orders take effect at the exchange at `now + order_latency_us`. `position` and `cash`
    update only when an `OrderFilled` notice is **delivered**.
  - `class Backtest(strategy, config=SimConfig(), inst=BTCUSDT)` with
    `run(market_events: Iterable[BookBatch | TradePrint]) -> Result`. The loop:
    - **Heap entries** are `(time, priority, seq, kind, payload)`. Priority 0 = market data
      applied to the exchange, 1 = order arriving at the exchange, 2 = delivery to the strategy.
    - **Market events** are pulled lazily. Each one is applied to `SimExchange` at `ts_local`.
      Its delivery to the strategy (a replica `Replayer` update plus the callback) is
      scheduled at `ts_local + feed_latency_us`.
    - **Exchange notices** are scheduled for delivery at `exchange_ts + feed_latency_us`.
    - The loop ends when the market stream and the heap are both empty. Then `on_end` is
      called with `now` = the last delivery time.
  - `Result` (Task 4) is built from the delivered fills. For Task 3 a minimal `Result` holding
    `fills: list[OrderFilled]` is enough; Task 4 extends it.

- [ ] **Step 1: Write failing tests:**

```python
def test_strategy_never_sees_future_data():              # Review Focus 1
    # Recorder strategy asserts, in every callback, that each delivered event has
    # ts_local + feed_latency <= ctx.now, and that ctx.book equals a Replayer fed only those events.
def test_cheating_strategy_cannot_reach_exchange():
    # ctx has no attribute that leads to SimExchange or the market iterator
    # (check vars(ctx) and BookView's attributes)
def test_order_latency_order_misses_earlier_trade():      # Review Focus 1
    # strategy places a bid at t0. A trade at t0 + latency - 1 must not fill it;
    # a trade at t0 + latency must be able to.
def test_fill_notice_delayed_and_position_updates_on_delivery():
def test_cancel_racing_fill_is_rejected_and_fill_reported():   # Review Focus 3
def test_same_us_priority_market_before_order_arrival():
def test_deterministic_two_runs_identical():
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(backtest): event loop with order and feed latency; strategy sees only delivered data`.

---

### Task 4: Results, accounting and the example market maker

**Files:**
- Create: `src/lobsim/backtest/result.py`
- Modify: `src/lobsim/backtest/strategy.py`
- Test: `tests/backtest/test_result.py`, `tests/backtest/test_market_maker.py`

**Interfaces:**
- Produces:
  - `Result` fields:
    - `fills`
    - `orders_placed: int`, `qty_placed: int`, `qty_filled: int`, and the derived `fill_ratio`
    - `maker_qty`, `taker_qty`
    - `fees_quote: float`
    - `final_position: int`, `final_cash: int`
    - `equity: list[(ts, position, mark_to_mid_pnl_quote)]`, sampled every 10 minutes
      (10-minute aggregates, per the ToS)
  - Methods:
    - `pnl_quote(mark_price: int) -> float` = `(cash + position*mark_price) * tick*lot - fees`
    - `summary() -> dict`
    - `chart(path)`
  - `class NaiveMarketMaker(Strategy)`, with parameters `size_lots=100` (0.001 BTC) and
    `max_position_lots=1_000`. It keeps one bid at the replica's best bid and one ask at its
    best ask:
    - When a best price moves, it cancels and re-quotes that side.
    - It doesn't quote a side that would push |position| over the limit.
    - It is simple on purpose, to exercise the system, not to make money.

- [ ] **Step 1: Write failing tests:**

```python
def test_cash_and_position_exact_from_fills():           # Review Focus 5
    # three hand fills → cash == -Σ(signed qty*price) exactly; position == Σ signed qty
def test_fees_total_equals_sum_of_fill_fees():
def test_pnl_mark_to_mid():
def test_fill_ratio():
def test_market_maker_quotes_both_sides_and_respects_limit():  # scripted market events
def test_market_maker_requotes_on_best_change():
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(backtest): exact accounting, results summary, naive market maker`.

---

### Task 5: CLI, real-data run and documentation

**Files:**
- Create: `src/lobsim/backtest/__main__.py`, `docs/benchmarks/M4-backtest.md`,
  `docs/notes/M4-backtester.md`
- Modify: `README.md`
- Test: `tests/backtest/test_cli.py`, which uses the M3 fixtures with `TEST` instrument registration

**CLI:** `uv run python -m lobsim.backtest --day 2026-11-01 --symbol BTCUSDT [--hours H] [--order-latency-us N] [--feed-latency-us N] [--maker-fee-bps X] [--taker-fee-bps X] [--size-lots N] [--max-position-lots N] [--json PATH] [--chart PATH] [--data-dir data]`.
It reads the M3 Parquet files, runs `NaiveMarketMaker`, and prints the summary.

- [ ] **Step 1:** CLI test on the fixtures: exit code 0, and the summary keys are present.
  Implement, run, then commit `feat(backtest): CLI`.
- [ ] **Step 2: Real runs on the full day.** Use the same seedless and deterministic inputs.
  Run four configurations:
  1. defaults (10/10 bps fees, 1 ms / 1 ms latency)
  2. zero fees, to show gross edge vs fees
  3. `order_latency_us=0, feed_latency_us=0`, to show what latency costs
  4. a 10 ms latency

  Save JSON to `docs/benchmarks/m4-*.json` and the default chart to `docs/benchmarks/m4-equity.png`.
  Then **run the default twice and confirm identical JSON**, for reproducibility.
- [ ] **Step 3: Write `docs/benchmarks/M4-backtest.md`.** It covers:
  - the method
  - the results table: PnL, fees, fills (maker/taker), fill ratio, max |position|, runtime
  - the chart
  - an honest interpretation: what the fees and latency do to a 1-tick market
  - the known limits of the fill model (rules 1–8 restated as limits)
- [ ] **Step 4: README.** Add an M4 section: what the backtester does, the fill model and its
  known limits, the reproduce commands, and mark M4 ✅.
- [ ] **Step 5: Write `docs/notes/M4-backtester.md`**, the study note. It covers:
  - event-loop design and the tie priorities
  - the two clocks and why look-ahead is impossible by construction
  - the queue model, with a worked example
  - IOC vs no-impact
  - exact money
  - what the results mean
  - at least 10 interview questions with answers
- [ ] **Step 6: Verify** that `uv run pytest -q` is all PASS and nothing under `data/` is tracked.
  Commit `docs: M4 backtest results, README fill model and limits, study note`.
