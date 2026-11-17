# M4 Study Note: The Backtester

Read this with [docs/benchmarks/M4-backtest.md](../benchmarks/M4-backtest.md) and the README's
"Fill model and known limits" open.

## 1. The event loop

`Backtest.run` (`src/lobsim/backtest/engine.py:128`) is a classic discrete-event simulation:
it repeatedly takes the earliest pending event and processes it.

There are two sources of events:
- **Market data** (book batches and trades from M3). This is pulled **lazily** from the
  sorted stream, so 2 million events never sit in memory.
- **Our own events**, on a heap: an order *arriving* at the exchange, and a notice or
  market event being *delivered* to the strategy.

Each step compares the next market event with the heap top (`engine.py:174`) and handles
whichever comes first. At the same µs the priorities are:

| Priority | What | Why this order |
|---|---|---|
| 0 | market data hits the exchange | The market moves before our order lands. This is pessimistic, and we prefer that. |
| 1 | our order arrives at the exchange | |
| 2 | delivery to the strategy | The strategy hears about things last. |

A sequence counter breaks any remaining ties, so the result is fully deterministic. Two
runs of the full day produce byte-identical JSON.

## 2. Two clocks, and why look-ahead is impossible

- **Exchange time.** The `SimExchange` applies market data at `ts_local`. Our orders reach
  it `order_latency_us` after we send them.
- **Strategy time** (`ctx.now`). Every market event and exchange notice reaches the strategy
  `feed_latency_us` after it happened at the exchange.

The strategy only ever gets a **`Context`** (`engine.py:77`), which holds:
- `now`
- `book`: a read-only `BookView` (`engine.py:48`) onto the strategy's **own, delayed copy**
  of the book
- `position`, `cash` and `fees`, updated only when a fill notice is *delivered*
- `open_orders`, as the strategy knows them
- an **outbox** for order requests

The outbox matters. The context doesn't hold a callback into the engine, because a
callback could be followed back to the exchange. Requests are left in a list that the
engine collects.

So look-ahead isn't prevented by a rule the strategy must obey. It's prevented by
**construction**: there is no reference path from the strategy to future data. Two tests
check this:
- `test_strategy_never_sees_future_data` asserts, inside every callback, that every
  delivered event satisfies `ts_local + feed_latency == ctx.now` and that the strategy's
  book equals a replay of only the delivered events.
- `test_context_has_no_path_to_exchange_or_market` walks every object reachable from the
  context and asserts that no `SimExchange` or `Backtest` is among them.

## 3. The queue model, with a worked example

L2 data says "price 100 has 5 BTC", but not who is in line. So for each of our resting
orders we keep **`ahead`**, an estimate of the quantity in front of us (`_SimOrder` in
`src/lobsim/backtest/exchange.py`).

We join a bid at 100 for 3 lots, when 5 lots are visible:

| Event | `ahead` | Our fill | Rule |
|---|---:|---:|---|
| join | 5 | 0 | ahead = visible quantity |
| sell trade 4 @ 100 | 1 | 0 | a trade at our price consumes the queue |
| book update: level 100 → 1 | 1 | 0 | `min(1, 1)`: the same trade seen again doesn't double-count |
| sell trade 3 @ 100 | −2 → 0 | **2** (maker) | the excess beyond the queue fills us |
| sell trade 1 @ 99 | 0 | **1** (maker) | a trade *through* our price fills us fully |

Two further rules:
- If the level shrinks (cancels), `ahead = min(ahead, new size)`. We assume the cancelled
  size was ahead of us, which is **optimistic**.
- If the opposite best price reaches ours, we fill fully.

**The same trade shows up twice in the data**, once as a trade print and once as the
level shrinking. We must not subtract it twice. It depends on which arrives first:
- **Trade first, then book.** The trade reduces `ahead`. Then the book update does
  `ahead = min(ahead, new size)`, which changes nothing. That is why we use `min` and not
  subtraction.
- **Book first, then trade, at the same µs.** This is the order our replay uses on a tie.
  The book update lowers `ahead` and *remembers* how much it dropped (`drop_qty`). The
  trade is matched against that remembered drop first, and only the rest reduces `ahead`.

  An earlier version missed this case and produced phantom fills. Review caught it, and
  the test now asserts no fill. It affected only 3 fills on our day.
- **Book first, trade at a different µs.** These are counted as two events. That
  over-counts and is an optimistic, documented limit. The depth and trade feeds are
  separate streams, so this can happen.

## 4. Aggressive orders: IOC because of no market impact

When our order crosses on arrival, `_take` (`exchange.py:156`) walks the visible opposite
levels, best first, and fills as **taker**. Then it **cancels the rest**.

Why not let the rest sit on the book? We never remove liquidity from the replayed book,
so the levels we "took" are still there. A resting remainder would match the same
liquidity again on the next event, and fill forever. IOC is the honest choice under a
no-impact model.

## 5. Exact money

- `cash` is an **integer** in units of one tick × one lot. For BTCUSDT that is
  0.01 × 0.00001 = 1e-7 USDT. A buy of `q` lots at price `p` ticks does `cash -= p*q`, with
  no rounding.
- `position` is an integer in lots.
- Only two things are floats: the fees (in USDT) and the final conversion of PnL to USDT.
- PnL = `(cash + position × mark) × unit − fees`.

A test checks that cash equals exactly −Σ(signed qty × price) over the fills, and that the
fee total equals the sum of the per-fill fees.

## 6. What the results mean

| | |
|---|---|
| Half-spread we earn per fill | 0.005 USDT per BTC, about 0.0006 bps |
| Gross result per BTC traded | about −12 USDT (−1.5 bps) |
| Fees | 10 bps per side |

The gross loss is **adverse selection**: our quote is filled mostly when the market is
about to move through it. Then fees multiply the loss by about 6.4×.

The latency runs show that **speed is not an edge by itself**. With zero latency we traded
about 60% more volume at the *same* per-BTC loss. At 10 ms we traded less, and some of our
quotes were already crossing by the time they arrived, which made them taker fills.

What would a real market maker do?
- Skew or widen its quotes using signals like order-book imbalance and short-term momentum.
- Cancel quickly when the book moves against it.
- Manage inventory.
- Trade at a fee tier with rebates.

This backtester is the tool you would use to test each of those ideas.

## 7. Likely interview questions

1. **How does your backtester prevent look-ahead bias?**
   The strategy receives only delayed deliveries through a Context. That context has its
   own delayed book copy and an outbox, and no reference to the exchange or the data
   stream. Tests assert delivery timing in every callback and walk the object graph to
   prove there's no path to the future.
2. **How do you model queue position with L2 data?**
   Join behind the visible quantity. Trades at our price reduce what's ahead of us, and the
   excess fills us. A shrinking level caps the queue with `min`. A trade through our price
   fills us fully. It's an estimate, and the level-shrink rule is optimistic.
3. **Why might your fill model be too optimistic or too pessimistic?**
   - Optimistic: we assume cancels come from ahead of us, and a trade-through fills our
     whole order.
   - Pessimistic: market data is processed before our order at equal timestamps.
   - Missing entirely: our own market impact.
4. **Why are aggressive orders IOC?**
   With no market impact, the liquidity we took stays in the replayed book, and a resting
   remainder would refill against it forever.
5. **How do you handle a cancel that races a fill?**
   A cancel takes effect only when it reaches the exchange. If the order filled first, the
   cancel is rejected with `unknown_order` and the fill stands. There's a test for this.
6. **Why did your market maker lose money even with zero fees?**
   Adverse selection. Passive quotes get filled when the price is about to move through
   them. The spread earned (0.0006 bps) is far smaller than that loss (about 1.5 bps).
7. **Why didn't lower latency help?**
   It increased the filled *volume* (21.2 vs 13.4 BTC) but not the per-BTC edge, which
   stayed negative at about −1.5 bps. So the total loss grew. The number of fills barely
   changed (27,111 vs 27,284). Latency amplifies an edge; it doesn't create one.
8. **How do you make results reproducible?**
   The input is deterministic, and ties are broken by fixed priorities plus a sequence
   number. There's no randomness. The JSON leaves out wall-clock time, and two full-day
   runs are byte-identical.
9. **How do you keep money exact?**
   Cash is integer tick×lot units and position is integer lots. Only fees and the final
   conversion to USDT are floats.
10. **How fast is it, and how would you make it faster?**
    About 20 s for 2M events in pure Python. M2 showed the matching core is about 20×
    faster in C++ when work is batched. The next step would be to move `SimExchange` and
    the replay loop into C++ and call strategies only at decision points.
11. **What would you add next?**
    - Latency jitter.
    - A breakdown of fills by rule (queue vs trade-through vs move-through), to see where
      the adverse selection comes from.
    - Market impact for aggressive orders.
    - A smarter strategy, for example skewing quotes on book imbalance.
12. **What does the fill ratio tell you, and what doesn't it?**
    Fill ratio = filled quantity ÷ quantity placed. It was 61% by default. A high ratio
    isn't good on its own: here the fills were adversely selected. Fill *quality* matters
    more than fill *count*.
13. **Can your strategy breach its position limit?**
    Briefly, by one quote size. The limit is checked against the position the strategy
    knows about, while another quote may still be live or its cancel may still be in
    flight. On our day max |position| equalled the limit. A real system would also check
    risk at the exchange gateway.
