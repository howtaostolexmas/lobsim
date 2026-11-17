# M1 Study Note: Order Book and Matching Engine

Read this alongside the code. The goal is to explain every line in an interview.

> **Updated in M2:** a price level is now an `OrderedDict` with a cached total (O(1)
> cancel), and line numbers have moved. This note describes the M1 design (`git show
> f38b36a:src/lobsim/order_book.py`); see [M2-performance.md](M2-performance.md) for what
> changed and why.

## 1. Walkthrough: what happens in `add_limit`

Example: the book has a SELL of 5 @ 101 (order 1). We call
`add_limit(3, Side.BUY, 102, 7)`.

1. **Validate** (`src/lobsim/order_book.py:98`, `_validate`). Is qty > 0? Is price > 0?
   Is id 3 already resting? If any check fails, return `[Rejected(3, reason)]` and
   change nothing.
2. **Accept.** Start the event list with `Accepted(3)`.
3. **Match** (`order_book.py:112`, `_match`). Loop while there is quantity left:
   - Find the best price on the opposite side (lowest ask = 101).
   - Ask `_crosses` (`order_book.py:151`) whether a BUY at 102 can trade at 101.
     It can, because 102 ≥ 101.
   - Take the **first** order in that price's deque (oldest = time priority).
   - Fill `min(remaining, maker.qty)` = 5. Emit `Trade(1, 3, 101, 5, BUY, 0, 2)`.
   - The maker is now 0, so pop it, delete it from the id index, and remove the
     price level if it is empty.
   - Loop again. The opposite side is now empty, so stop. 2 remains.
4. **Rest** (`order_book.py:141`, `_rest`). Put the remaining 2 at price 102 on the
   BUY side: append it to the deque (back of the queue) and add it to the id index.

`add_market` is the same, except `limit_price=None` (it crosses at any price), and the
remainder becomes `Cancelled` instead of resting.

`cancel` looks the order up in the dict (O(1)), removes it from its deque (O(k) scan),
and removes the level if it is now empty.

## 2. Design decisions and the alternatives that lost

| Decision | Alternative | Why the alternative lost |
|---|---|---|
| Integer ticks and lots | `float` prices | Floating-point rounding: `0.1 + 0.2 != 0.3`. Two equal prices might not compare equal. |
| `SortedDict` of price levels | `heapq` | A heap gives the best price fast, but cannot remove an arbitrary empty level cheaply, and cannot iterate levels in order for `depth()`. |
| `SortedDict` | A plain dict + `sorted()` on every query | Re-sorting costs O(n log n) per query. |
| Bids iterate in reverse | Store bids as negative prices | This works, but `-price` in the code is a classic source of sign bugs, and harder to explain. |
| `deque` per level | `list` per level | `list.pop(0)` is O(n). `deque.popleft()` is O(1). |
| Dict index from id to order | Search every level to cancel | The index finds the order in O(1). |
| `eq=False` on `_Resting` | Default dataclass equality | `deque.remove` must remove *this exact object*, not one that happens to have equal fields. Identity comparison is also faster. |
| Events as frozen dataclasses | Callbacks or printing | Tests can compare whole event lists with `==`, and the M2 C++ engine must produce the same lists. |

## 3. Complexity

n = number of price levels on a side, k = number of orders at one price.

| Operation | Cost |
|---|---|
| Best bid / ask | O(1) in practice (see note below) |
| Add limit, no trade, existing level | O(1): one best-price check, a dict lookup, an append |
| Add limit, no trade, new level | O(log n) to insert the price into the sorted keys |
| Match, per fill | O(1) for the best-price check and popping the order, plus O(log n) when a level empties and is removed |
| Cancel | O(1) lookup + **O(k)** to remove from the deque |
| `depth(side, m)` | O(m · k), because it sums each level's orders |

**Note on best price.** `SortedDict.peekitem` is documented as O(log n), which is a safe
upper bound to quote. But `sortedcontainers` has a fast path for index `0` and `-1`
(see `SortedList.__getitem__`): the first and last items are read directly, in O(1). We
only ever ask for the first or last, so best-price lookups are effectively O(1). If an
interviewer asks, say "O(log n) worst case by the docs, O(1) in practice because of the
first/last fast path" and explain why.

Two costs to fix in M2: `cancel` and `depth` both scan a whole level. Typical fixes are
lazy deletion, an intrusive linked list for O(1) cancel, and a cached total per level for
`depth`.

## 4. How we know it is correct

- **Unit tests** cover hand-checked scenarios: FIFO at one price, sweeping several levels,
  partial fills, every rejection reason, and id reuse.
- **Property tests** (`tests/test_properties.py`): Hypothesis makes random sequences of up
  to 200 operations. Small ranges for ids and prices make collisions and crossings
  common. Three properties are checked:
  1. The real engine and `NaiveOrderBook` produce **identical** events and depth after
     every operation.
  2. The book is never left crossed (best bid < best ask).
  3. For every order: original qty = filled + cancelled + still resting.
- **Do the tests catch bugs?** Yes. We broke the engine on purpose ("mutation testing"),
  and each of these four bugs made the property suite fail:
  - `>=` changed to `>` in `_crosses`
  - LIFO instead of FIFO (`queue[-1]` instead of `queue[0]` in `_match`)
  - `Cancelled` reporting qty + 1
  - not deleting filled makers from `_orders`

  To demo it yourself:
  ```bash
  sed -i '' 's/limit_price >= opposite_best/limit_price > opposite_best/' src/lobsim/order_book.py
  uv run pytest tests/test_properties.py -q     # expect a failure + minimal example
  git checkout src/lobsim/order_book.py          # undo the bug
  ```
  (On Linux, use `sed -i` without the `''`.)

## 5. Likely interview questions

1. **Two orders at the same price: which fills first?**
   The one that arrived first. Each price level is a FIFO deque: new orders append to the
   back, and matching pops from the front.
2. **At what price does a trade happen?**
   The maker's price, meaning the order that was already resting. A buyer willing to pay
   102 who hits an ask at 101 pays 101.
3. **How do you know your engine is correct?**
   Property-based tests against a simple reference engine, plus invariants (never
   crossed, quantity conserved). Also mention the mutation check in section 4.
4. **Why not floats?**
   Rounding error. Exchanges define a tick size, so integer ticks are exact.
5. **What is the complexity of cancel, and how would you make it O(1)?**
   Today it is O(1) lookup but O(k) removal. There are two fixes:
   - An intrusive doubly-linked list per level: each order holds prev/next pointers,
     so unlinking is O(1).
   - Lazy deletion: mark the order as dead and skip it when matching.
6. **Why a sorted dict and not a heap?**
   We need to remove empty levels and iterate levels in order for depth. A heap does
   neither well.
7. **What happens to a market order when there isn't enough liquidity?**
   It fills what it can. The rest is cancelled and reported in a `Cancelled` event. It
   never rests.
8. **Can an id be reused?**
   Yes, once the old order has stopped resting (filled or cancelled). Reusing an id
   that is still resting is rejected with `duplicate_id`.
9. **Why return events instead of mutating and returning nothing?**
   The event stream is the engine's output contract. It is easy to test, easy to log,
   and the C++ version in M2 must produce exactly the same events.
10. **What's the difference between `OrderBook` and `LevelBook`?**
    `OrderBook` tracks individual orders and matches them. `LevelBook` stores only the
    total per price, which is all an L2 market data feed gives you. Both share
    `PriceLevels`.
11. **Why does `LevelBook` allow a crossed book?**
    Real feeds can cross briefly between updates. A replay should reflect the data, not
    hide it.
12. **What would you optimize first?**
    Measure first with a profiler. Our guess is the per-level scans in `cancel` and
    `depth`, and Python object overhead per order. That is M2.
