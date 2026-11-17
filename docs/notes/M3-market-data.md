# M3 Study Note: Real Market Data Replay

Read this with [docs/benchmarks/M3-replay.md](../benchmarks/M3-replay.md) open.

## 1. What kind of data this is: L1, L2, L3 and trades

| Level | What you get | Example |
|---|---|---|
| L1 | best bid and best ask only | "best bid 77,582.02 × 1.7 BTC" |
| **L2** | total size at **each price level** | "77,582.02 now has 6.72517 BTC" |
| L3 | every individual order (id, size, arrival) | "order 123 for 0.5 BTC joined 77,582.02" |
| Trades | each execution: price, size, taker side | "sell 0.00105 BTC at 77,582.02" |

We have **L2 and trades**. L2 tells us how much is resting at a price, but **not in what
order**. So when our own simulated order joins a level, we can't know exactly how many
orders are ahead of it as the level changes. M4 has to *model* our queue position, and
that is the core difficulty of realistic backtesting.

That's also why the design reuses `LevelBook` here and not `OrderBook`. L2 has no orders
to match, only sizes per price.

## 2. Two clocks: exchange time vs local time

Every row has two timestamps, both in µs since the epoch:
- `timestamp` is when the **exchange** says the event happened.
- `local_timestamp` is when the message **arrived** at the data collector.

We order everything by **`local_timestamp`** (`replay.py:71`, `events`). A trading strategy
can only react to data once it has arrived. Ordering by exchange time would let a
backtest act on information before it could have been received. That is **look-ahead
bias**, the classic way backtests lie.

Two more details:
- **Tie-break.** When a book message and a trade arrive at the same µs, the book goes
  first. Within one file, the original order is kept.
- **Sortedness is checked.** `heapq.merge` silently gives a wrong order if an input
  isn't sorted. So `_keyed` (`replay.py:81`) raises an error if a timestamp goes
  backwards instead of trusting the file.

## 3. Snapshots and the reset rule

An L2 feed starts with a **snapshot**: the whole book at that moment, many rows sharing
one timestamp and `is_snapshot=true`. After that it sends **updates**: "this price now has
this size", where 0 means the level is gone.

`Replayer.apply` (`replay.py:111`) follows these rules:
1. **Updates before the first snapshot are ignored.** Without a starting picture they
   mean nothing.
2. **A new snapshot after updates means "start over".** This happens after a
   reconnection. We clear the book first.
3. **A snapshot can span several messages.** So we clear only on the *transition* into a
   snapshot, not on every snapshot message.

A **batch** is the rows that share one `local_timestamp` and one snapshot flag
(`replay.py:46`, `batches`). That is one exchange message, applied all at once.

## 4. Why exact decimal parsing

Prices arrive as text like `"77582.03"`. Converting to `float` first would be a bug
waiting to happen:

```python
>>> 0.1 + 0.2
0.30000000000000004
>>> int(float("4.35") * 100)
434          # not 435: the float is 4.3499999...
```

A price could land on the wrong tick, and then two equal prices would stop comparing
equal. `to_units` (`instrument.py:15`) moves the decimal point in the string itself:
`"77582.03"` with 2 decimals becomes `7758203`. Anything that isn't exactly on the tick
grid raises an error instead of being rounded.

On the real data, all 10.2M L2 rows and 1.1M trades parsed exactly, which also confirms
the tick (0.01) and lot (0.00001) sizes.

## 5. Why Parquet

| | CSV.gz | Parquet |
|---|---|---|
| Types | everything is text and must be re-parsed on every read | stored as int64 / bool / int8 |
| Layout | row by row | **columnar**: read only the columns you need |
| Size | gzip | per-column compression (similar or smaller) |
| Streaming | yes | yes, by row group |

We normalize once (28 s for the day) and replay from Parquet many times. The writer
streams in batches of 500k rows through a `.part` file, so it never holds a whole day
in memory and never leaves a half-written file.

## 6. How we know the replay is right

Tardis also publishes `book_snapshot_25`, the top 25 levels each time they change, built
from the same feed. For each of those snapshots, `validate` (`validate.py:78`) does two
things:
- applies every L2 batch with `ts_local ≤` the snapshot's time
- compares our top 25 levels on each side with the snapshot

**Result: 463,716 of 463,716 matched exactly.** That one number covers the parsing, the
ordering, batching, the snapshot rules, and `LevelBook`.

**Crossed books.** Sometimes a rebuilt L2 book shows best bid ≥ best ask for a moment,
when the two sides' updates arrive in different messages. Tardis drops crossed levels
from its snapshots, and `top_uncrossed` (`validate.py:62`) does the same. On this day the
book was **never** crossed, so this code is tested only by unit tests. That's worth
saying honestly if asked.

## 7. Data licence: what we may publish

Tardis's terms forbid redistributing raw data or derived datasets, and allow charts and
statistics. So:
- `data/` is git-ignored (`/data/`). Careful: a plain `data/` pattern also matched
  `tests/data/`, which we caught and fixed.
- The test fixtures are **hand-written synthetic rows**.
- The chart shows 10-minute aggregates.

## 8. Likely interview questions

1. **What's the difference between L2 and L3 data, and why does it matter for
   backtesting?**
   L2 gives size per price. L3 gives every order. With L2 you can't see the queue order,
   so your own order's position has to be modeled. That's M4.
2. **Which timestamp do you sort by, and why?**
   Arrival (local) time. It is when a strategy could have known about the event. Using
   exchange time causes look-ahead bias.
3. **How do you handle snapshots in an incremental feed?**
   Ignore updates until the first snapshot. Reset the book when a new snapshot follows
   updates. Group rows by timestamp into messages and apply each message atomically.
4. **How did you verify your order book reconstruction?**
   Against the provider's own top-25 snapshots at every change: 463,716 of 463,716
   matched exactly for a whole day.
5. **Why not parse prices as floats?**
   Binary floats can't represent most decimal prices exactly, so prices could land on the
   wrong tick. We parse strings directly to integer ticks and reject anything off-grid.
6. **What is a crossed book, and why can a rebuilt one be crossed?**
   Best bid ≥ best ask. In a rebuilt book it's an artifact of the two sides' updates
   arriving in separate messages. We remove crossed levels pairwise when comparing, like
   the source does. It didn't occur on our day.
7. **Why Parquet over CSV?**
   It is typed, columnar, compressed and streamable. We parse the text once, then replay
   fast.
8. **How fast is your replay?**
   About 800k L2 rows per second in pure Python, with a full day in 12.7 s. M2 showed C++
   would be about 20× faster for the engine itself, but only if we batch across the
   boundary.
9. **What did you learn about the market from the data?**
   BTCUSDT on Binance is a one-tick market: the spread was 0.01 USDT 99.99% of the time.
   So passive fills depend on queue position, not price.
10. **What can go wrong with timestamps?**
    - Ties between sources: we use a fixed book-first rule.
    - Out-of-order rows: we detect them and fail loudly.
    - Clock skew between exchange and collector: that's why we keep both timestamps.
11. **How do you keep the pipeline reproducible?**
    One CLI command per stage, a fixed day and symbol, deterministic parsing, and
    `.part` files with atomic renames. Tests use offline fixtures, and the downloader's
    network call is injectable.
12. **What are you not allowed to publish, and how did you make sure?**
    Raw data and derived datasets. `data/` is git-ignored, the fixtures are synthetic,
    and only aggregate charts and stats are committed.
