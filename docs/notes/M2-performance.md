# M2 Study Note: Performance (profile → optimize → C++)

Read this with [docs/benchmarks/M2.md](../benchmarks/M2.md) open. Every number below
comes from that file.

## 1. How the benchmark works, and why it is fair

- **Same input every time.** `bench/orderflow.py` builds the op list from
  `random.Random(seed)`. Same seed, same ops. That is why "before" and "after" are
  comparable. A test (`tests/test_orderflow.py`) checks this.
- **Realistic-ish flow.**
  - Mix: 60% limit, 30% cancel, 10% market.
  - Limit prices sit a few ticks from a drifting mid (geometric distance), and 10% cross.
  - Cancels pick a random order that is **still resting**. The generator replays its
    own ops on a private book and forgets ids once they are fully filled. An early
    version didn't do this, and review caught that about 89% of "cancels" were actually
    rejects of already-filled orders. That would have made the cancel numbers meaningless.
- **No setup inside the timer.** The ops are generated before timing starts.
- **Warm-up.** We first run 10% of the ops on a throwaway book. This mostly warms
  Python's code paths and memory allocator. There is no JIT on either side, so its
  effect is small, but it does keep one-time setup out of the timed runs.
- **Fresh book per run and 5 runs, take the median.** The median ignores one unlucky
  run, for example the OS scheduling something else.
- **Per-op timing** with `perf_counter_ns` gives p50 and p99 per op type. The timed
  region also includes the harness's `apply()` dispatch, so it adds tens of ns per call. That is fine for comparing engines measured the same way, but it is
  not a "true" latency.
- **Noise.** Running the same engine twice differs by about ±5%. Any difference smaller
  than that is not a result.

## 2. What the profiler said, and what we did

`cProfile` sorted by own time (`docs/benchmarks/m2-profile-before.txt`). The top
engine costs were:
- `_match` itself, the Python loop
- `SortedDict.peekitem`, called by `best()` on every loop iteration
- `Enum.__hash__`, because `self._levels[side]` hashed the `Side` enum on every lookup

Cancel was cheap (about 500 ns at p50, about the same as a rejected op). **The profile
did not point at cancel**, because levels in this workload are shallow.

Changes:
1. `_match` gets best price and level in **one** lookup (`PriceLevels.best_item`)
   instead of `best()` then `get()` (`src/lobsim/order_book.py:144`).
2. Two attributes `_bids` / `_asks` instead of a dict keyed by `Side`, so no enum
   hashing.
3. Each level became `_Level` (`order_book.py:23`): an `OrderedDict` of orders plus a
   cached `total`. This makes cancel O(1) and `depth()` O(m).

Result: **no speedup on the main workload** (793k → 776k ops/sec, inside the ±5%
noise band). But on a deep level (20,000 orders at one price), cancel went from
29,293 ns to 387 ns, about 76× faster.

The lesson to say out loud in an interview: *we improved the complexity of an operation
that wasn't the bottleneck for this workload. The profile told us so, and the deep-level
benchmark shows when it does matter.* Interviewers like hearing that you measured and
reported a non-result honestly.

## 3. `deque` vs `dict` vs `OrderedDict` for a price level

| | oldest order | pop oldest | delete by id (cancel) |
|---|---|---|---|
| `deque` (M1) | O(1) `q[0]` | O(1) `popleft()` | **O(k)** `remove()` scans |
| `dict` (insertion-ordered) | `next(iter(d))`, which can slow down after many front deletes (it skips dummy slots) | same issue | O(1) |
| `OrderedDict` (M2) | O(1) | O(1) `popitem(last=False)` | **O(1)** `del` |

`OrderedDict` is a hash table plus a doubly-linked list through its entries. The hash
finds an entry and the list keeps the order. Its constant cost is higher than a
deque's, which is why it didn't speed up the shallow workload.

## 4. The C++ engine, side by side with Python

| Concept | Python | C++ |
|---|---|---|
| Sorted price levels | `SortedDict` (via `PriceLevels`) | `std::map<int64_t, Level>`, a red-black tree |
| Best ask / best bid | `peekitem(0)` / `peekitem(-1)` | `begin()` / `std::prev(end())` |
| Orders at one price | `OrderedDict[int, _Resting]` | `std::list<Order>` (`order_book.hpp:30`) |
| Find an order by id | `dict[int, _Resting]` | `unordered_map<int64_t, Locator>`, where `Locator` holds a **list iterator** (`order_book.hpp:67`) |
| Matching loop | `_match` (`order_book.py:131`) | `OrderBook::match` (`order_book.cpp:129`) |

Read the two `match` functions side by side. They make the same steps in the same order:
1. Find the best opposite level.
2. Check that it crosses.
3. Fill the oldest order.
4. Emit a trade.
5. Remove the order, and the level if it is now empty.

**Why `std::list` and not `std::vector`?** Cancel needs to erase from the middle. In a
`vector`, that shifts every later element (O(k)), and it **invalidates iterators** to
them, so the index would hold dangling pointers. A `list` erases in O(1), and every other
element's iterator stays valid. That is exactly what lets the `unordered_map` store
iterators safely.

**The one dangerous spot.** In `match`, when the last order at a level is filled, we
`erase` the map entry (`order_book.cpp:160`). After that, `level_it` and `level`
are dangling. So the code uses `maker` *before* popping it, erases the level *last*, and
the next loop iteration looks the best level up again. The property tests hit this
path on every run.

**Boundary types.** C++ uses `int64_t` and Python ints are unbounded. pybind11 raises
`TypeError` for 2⁶³ or more instead of silently wrapping (`tests/test_fast_bounds.py`).

A level total that would pass int64 raises `OverflowError` before any change is made.
The check is exact: if our side already has orders at that price, the book isn't
crossed there, so the order can't match and will rest with its full quantity
(`tests/test_fast_bounds_extra.py`).

## 5. The boundary cost

| | ns per op |
|---|---:|
| C++ engine alone (`cpp-raw`) | 61.6 |
| Same engine, called once per op from Python (`cpp`) | ~757 |

So about 92% of a per-op call is overhead:
- Python → C++ argument conversion
- building a result tuple per event
- `FastOrderBook` turning each tuple into a dataclass
- the harness's own dispatch and timer calls

For cancel, the C++ path's median is no better than pure Python's (542 vs 541 ns),
because there's so little real work to amortize the overhead.

How real systems avoid this:
- **Batch.** Hand C++ a whole list of messages, like `run_ops` does.
- **Keep the hot loop entirely in C++**, and cross into Python only for slow decisions.
- **Return compact results** (arrays, not objects).

M4's replay should batch for the same reason.

## 6. Building and testing two engines

- `pyproject.toml` uses **scikit-build-core**, which runs **CMake**, which builds the
  pybind11 module `lobsim._core`. CMake comes from PyPI if you don't have it installed.
- `[tool.uv] cache-keys` includes `src/cpp/**`, so editing C++ makes the next `uv run`
  rebuild automatically. You never test a stale `.so`. We verified this by breaking the
  C++ crossing check on purpose: 7 `[cpp]` tests failed on the next `uv run pytest`.
- `tests/conftest.py` has a `make_book` fixture parametrized over `["python", "cpp"]`.
  Every engine test, including the Hypothesis tests against `NaiveOrderBook`, runs twice.

## 7. Likely interview questions

1. **What did profiling show, and did your optimization work?**
   The time went to the matching loop and the sorted-dict lookups, not to cancel. Our O(1)
   cancel didn't help the main workload (inside noise), but it cut deep-level cancel
   about 76×. We reported both.
2. **Why `std::list` and not `std::vector` for a price level?**
   O(1) erase from the middle, and stable iterators, so the id → iterator index stays
   valid.
3. **Why `std::map` and not `std::unordered_map` for price levels?**
   We need sorted order for the best price and for depth. A hash map has no order.
4. **What does pybind11 do?**
   It generates the CPython glue: it converts arguments and return values between
   Python and C++ types and exposes C++ classes as Python classes.
5. **Your C++ is 20× faster, but calling it from Python is only 1.67× faster. Why?**
   About 92% of each call is boundary and harness overhead. The fix is batching or staying in C++.
6. **Why look at p99 and not just the average?**
   In trading, the slow tail is what loses money. A rare 10 µs stall during a burst
   matters more than a good mean.
7. **How do you know the C++ and Python engines agree?**
   They run the identical suite through a parametrized fixture, including property tests
   against a naive reference. Breaking the C++ on purpose made the `[cpp]` tests fail.
8. **How do you make the benchmark fair?**
   Seeded input, generated before timing, a warm-up, a fresh book per run, a median of
   5 runs, the same timing method for both engines, and a known ±5% noise band.
9. **What happens if someone passes 2⁶³ to the C++ engine, or if a level's total gets
   too big?**
   - An argument of 2⁶³ or more: pybind11 raises `TypeError`.
   - A level whose total quantity would pass 2⁶³−1: `add_limit` checks with
     `__builtin_add_overflow` *before* changing anything, and raises `OverflowError`.
     Review caught that an earlier version wrapped around silently.

   Both cases are tested. Python ints don't overflow, so this is one place where the two
   engines can legitimately differ, and it is documented.
10. **Where could memory or correctness go wrong in the C++?**
    A dangling iterator after erasing an empty level inside the match loop. The code
    uses the maker before popping it, erases the level last, and re-looks-up each
    iteration.
11. **What would you do next for speed?**
    - Batch the API, or return arrays instead of tuples.
    - Use a flat array indexed by tick instead of `std::map` when the price range is
      bounded.
    - Use an object pool to avoid allocating list nodes.
    - Measure each change with the same harness.
12. **Why does `run_ops` time inside C++ instead of from Python?**
    To exclude the cost of decoding the op list and crossing the boundary, so the number
    is the engine alone. The trade-off: no per-op percentiles in that mode.
