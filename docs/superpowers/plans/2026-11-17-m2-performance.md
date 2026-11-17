# M2: Performance (profile → optimize → C++) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the M1 engine, fix what the profile shows in Python, then port the engine
to C++ behind pybind11. Both engines must pass the identical test suite, and the speedup is
published with a reproducible method.

**Architecture:**
- **Benchmark.** A seeded synthetic order-flow generator feeds a harness that times every
  operation. The harness reports throughput and p50/p99 latency per operation type.
- **Python optimization.** A level becomes an `OrderedDict` with a cached total. That makes
  cancel O(1) and `depth` O(m).
- **C++ engine** (`lobsim._core`). It mirrors the same structures: `std::map` of levels,
  `std::list` per level, and a hash index of list iterators. A thin Python wrapper,
  `FastOrderBook`, turns C++ result tuples into the same event dataclasses, so the API is
  identical.
- **Two numbers.** We publish both the raw C++ speed and the speed through the wrapper,
  to show what the Python boundary costs.

**Tech Stack:**
- Python ≥ 3.11 and uv
- C++17 and pybind11
- scikit-build-core, which fetches CMake from PyPI if needed
- pytest and Hypothesis
- cProfile

**Spec:** `docs/superpowers/specs/2026-11-17-orderbook-backtester-design.md` (M2 section and "Who builds it")

## Global Constraints

- **Readable over clever.** Every optimization must be explainable in one or two sentences.
  The C++ is plain modern C++17, with no template tricks.
- **Only the order book engine is ported to C++.** `LevelBook` and `PriceLevels` stay in Python.
- **Both implementations pass the identical M1 suite.** That includes the property tests
  against `NaiveOrderBook`. Tests are parametrized, never copied.
- **Every published number states its method.** Machine and OS, Python version, build type,
  dataset (generator + seed + N), warm-up, number of runs, and how it was timed.
- **One command each.** Tests: `uv run pytest`. Benchmarks: `uv run python bench/run.py`.
- **CI** builds the extension and runs tests on `ubuntu-latest` and `macos-latest`.
- Small commits whose messages say *why*.
- Code and docs in English.

## Review Focus

1. **Cached level totals.** They must stay right after partial fills, cancels from the
   middle of a level, and a level emptying and reappearing. A stale cache shows wrong
   depth. (Task 2)
2. **Large ids and prices.** The C++ engine must accept ids and prices up to 2⁶³−1 with
   the same results as Python. Values ≥ 2⁶³ are rejected by pybind11 with `TypeError`,
   documented and tested. A silent overflow would be worse. (Task 4)
3. **Matching inside C++ when a level empties.** A cancel that empties a level, or a
   market order that empties the last level, must remove the level, with no dangling
   iterator. The property tests drive this. (Task 4)
4. **A stale extension.** After editing the C++, `uv run pytest` must test the new code,
   not an old build. Documented, and rebuilt via `uv sync --reinstall-package lobsim`. (Task 3)
5. **Benchmark determinism.** The same seed must give the identical op sequence, so
   before/after comparisons are fair. (Task 1)

---

## File Structure

| File | Responsibility |
|---|---|
| `bench/orderflow.py` | `generate(n, seed) -> list[Op]`: realistic synthetic order flow |
| `bench/run.py` | CLI harness: time each op, print a table, write JSON |
| `tests/test_orderflow.py` | Determinism and validity of the generated flow |
| `src/lobsim/order_book.py` | Python engine (modified: `OrderedDict` levels + cached totals) |
| `CMakeLists.txt` | Build `lobsim._core` with pybind11 |
| `src/cpp/order_book.hpp`, `src/cpp/order_book.cpp` | C++ engine |
| `src/cpp/bindings.cpp` | pybind11 module `_core` |
| `src/lobsim/fast.py` | `FastOrderBook`: Python wrapper converting tuples → events |
| `tests/conftest.py` | `make_book` fixture parametrized over `["python", "cpp"]` |
| `docs/benchmarks/M2.md` | Results table + method |
| `docs/notes/M2-performance.md` | Study note |

---

### Task 1: Order-flow generator and benchmark harness (baseline)

**Files:**
- Create: `bench/__init__.py`, `bench/orderflow.py`, `bench/run.py`
- Test: `tests/test_orderflow.py`

**Interfaces:**
- Produces:
  - `Op = tuple`, using the same shapes as the property tests:
    - `("limit", id, side, price, qty)`
    - `("market", id, side, qty)`
    - `("cancel", id)`
  - `generate(n: int, seed: int = 42) -> list[Op]`
  - `apply(book, op) -> list`, which dispatches like `tests/test_properties.py::apply`.
    Move that function here and import it in the property tests (DRY).
  - `bench/run.py` CLI flags:
    - `--impl python|cpp|cpp-raw` (default `python`; cpp variants come in Task 4)
    - `--n` (default 1_000_000)
    - `--seed` (default 42)
    - `--runs` (default 5)
    - `--json PATH`

    It prints a table of ops/sec plus p50/p99 ns for each op kind, taking the median
    across runs.

**Order-flow model:**
- **Mid price.** Starts at 10_000 ticks and random-walks ±1 tick with probability 0.1 per op.
- **Mix:** 60% limit, 30% cancel, 10% market.
- **Limit orders.** The side is a fair coin. The price is mid ± `geometric(p=0.3)` ticks
  on the passive side, and 10% of limits cross by 1 tick. Qty is uniform in 1..100.
- **Cancels.** Pick a uniformly random id from the live set the generator tracks.
  Cancelling an id that already filled is fine and realistic: it is rejected with `unknown_id`.
- **Market orders.** Qty is uniform in 1..200.
- **Ids.** Monotonically increasing from 1, never reused.
- **Randomness.** Use `random.Random(seed)` only, never global random.

**Timing:**
- `time.perf_counter_ns()` around each call. Build the op list before timing starts.
- One warm-up pass of 10% of N is discarded.
- Each run uses a fresh book.

- [ ] **Step 1: Write failing tests:**

```python
def test_same_seed_same_ops():
    assert generate(10_000, seed=7) == generate(10_000, seed=7)

def test_different_seed_differs():
    assert generate(1_000, seed=1) != generate(1_000, seed=2)

def test_mix_and_validity():
    ops = generate(20_000, seed=3)
    kinds = Counter(o[0] for o in ops)
    assert 0.55 < kinds["limit"] / len(ops) < 0.65
    assert 0.25 < kinds["cancel"] / len(ops) < 0.35
    assert all(o[-1] > 0 for o in ops if o[0] in ("limit", "market"))

def test_flow_runs_on_engine_without_rejects_except_unknown_cancel():
    book = OrderBook()
    for op in generate(5_000, seed=4):
        for e in apply(book, op):
            assert not isinstance(e, Rejected) or e.reason == "unknown_id"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_orderflow.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `orderflow.py` and `run.py`. Make `tests/test_properties.py`
  import `apply` from `bench.orderflow`. Add `"."` to pytest `pythonpath` so `bench` is importable.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 5: Baseline.** Run `uv run python bench/run.py --impl python --json docs/benchmarks/m2-baseline.json`.
  Expected: a table prints, and the JSON contains `ops_per_sec` and `p50_ns`/`p99_ns` per kind.
- [ ] **Step 6: Commit** `bench: seeded order-flow generator and timing harness, baseline numbers`.

---

### Task 2: Profile, then O(1) cancel and cached level totals in Python

**Files:**
- Modify: `src/lobsim/order_book.py`
- Create: `docs/benchmarks/m2-profile-before.txt`, `docs/benchmarks/m2-profile-after.txt`
- Test: `tests/test_order_book.py` (add cache tests)

**Interfaces:**
- Consumes: the harness from Task 1.
- Produces: the same public API as M1 (unchanged). Internally:
  - Each level is a private `_Level` with `orders: OrderedDict[int, _Resting]` and `total: int`.
  - `PriceLevels[_Level]` per side.
  - The front order is `next(iter(level.orders.values()))`.
  - Removing the front is `popitem(last=False)`. Cancel is `del level.orders[id]`.
  - `total` is updated on every fill, rest and cancel.

  Why `OrderedDict` and not a plain `dict`: deleting from the front of a plain dict leaves
  dummy slots, and `next(iter(d))` scans past them. That can degrade badly. `OrderedDict`
  is a hash table plus a doubly-linked list, so both operations are O(1). Put this
  reasoning in a comment.

- [ ] **Step 1: Profile before.** Run
  `uv run python -m cProfile -s cumtime bench/run.py --impl python --n 200000 --runs 1 | head -40 > docs/benchmarks/m2-profile-before.txt`.
  Expected: the file lists the top functions. Note the top 3 in the commit message.
- [ ] **Step 2: Write failing tests**, which pin the cache:

```python
def test_depth_total_after_partial_fill_and_middle_cancel():   # Review Focus 1
    b = OrderBook()
    for oid in (1, 2, 3): b.add_limit(oid, Side.SELL, 100, 10)
    b.add_market(9, Side.BUY, 4)          # 1 -> 6 left
    b.cancel(2)                           # remove from the middle
    assert b.depth(Side.SELL, 1) == [(100, 16)]

def test_level_empties_and_reappears_with_fresh_total():
    b = OrderBook()
    b.add_limit(1, Side.SELL, 100, 5); b.cancel(1)
    b.add_limit(2, Side.SELL, 100, 3)
    assert b.depth(Side.SELL, 1) == [(100, 3)]
```

  These pass on the M1 code, since it re-sums each level, so they are **regression guards**
  for the refactor. Run them now to confirm they pass. Then temporarily break the new code
  in Step 4 (skip the `total` update in cancel) to watch them fail. Task 3 later switches
  them to the `make_book` fixture like every other test.
- [ ] **Step 3: Implement** `_Level` and rewrite `_match`, `_rest`, `cancel` and `depth` to use it.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS. Then the mutation check: remove the
  `total` decrement in `cancel` → `uv run pytest -q` FAILS → restore.
- [ ] **Step 5: Profile after.** Same command, written to `m2-profile-after.txt`. Run
  `uv run python bench/run.py --impl python --json docs/benchmarks/m2-python-optimized.json`.
  Expected: cancel p99 drops, or stays flat while total throughput rises. Report the actual
  numbers in the commit message. If a number got *worse*, say so; do not hide it.
- [ ] **Step 6: Commit** `perf: O(1) cancel and cached level totals via OrderedDict levels`.

---

### Task 3: C++ build toolchain and the shared, parametrized test suite

**Files:**
- Modify: `pyproject.toml` (build backend → scikit-build-core), `.github/workflows/ci.yml` if needed
- Create: `CMakeLists.txt`, `src/cpp/bindings.cpp` (stub), `src/lobsim/fast.py`, `tests/conftest.py`
- Modify: every test in `tests/test_order_book.py`, `tests/test_market_and_rejects.py` and
  `tests/test_properties.py` to build books through the fixture

**Interfaces:**
- Produces:
  - `pyproject.toml`:
    - `[build-system] requires = ["scikit-build-core>=0.10", "pybind11>=2.13"]`
    - `build-backend = "scikit_build_core.build"`
    - `[tool.scikit-build] wheel.packages = ["src/lobsim"]`
    - `cmake.build-type = "Release"`
  - `CMakeLists.txt`:
    - `pybind11_add_module(_core src/cpp/bindings.cpp src/cpp/order_book.cpp)`
    - `CXX_STANDARD 17`
    - `install(TARGETS _core DESTINATION lobsim)`
  - `lobsim._core.CoreOrderBook`. Its methods return plain tuples (shapes defined in Task 4).
  - `lobsim.fast.FastOrderBook` has the same public methods and return types as `OrderBook`.
  - `tests/conftest.py`: a fixture `make_book` parametrized `["python", "cpp"]`, returning a
    zero-arg factory. Test functions take `make_book` and call `make_book()` instead of
    `OrderBook()`.
  - The property test compares **each** implementation against `NaiveOrderBook`. Use the
    fixture; Hypothesis works with function-scoped fixtures when
    `suppress_health_check=[HealthCheck.function_scoped_fixture]` is set, because the
    factory is stateless.

- [ ] **Step 1: Switch the build backend** and add `CMakeLists.txt`. Add a stub `bindings.cpp`
  exposing an empty `CoreOrderBook` class, a stub `order_book.cpp`, and a `fast.py` that
  wraps it with methods raising `NotImplementedError`.
- [ ] **Step 2: Run** `uv sync --reinstall-package lobsim && uv run python -c "import lobsim._core"`.
  Expected: imports without error. This proves the toolchain works on macOS.
- [ ] **Step 3: Parametrize the tests.** Run `uv run pytest -q`.
  Expected: every `[python]` case PASSES, and every `[cpp]` case FAILS with `NotImplementedError`.
  This is the RED for Task 4.
- [ ] **Step 4: Document the rebuild rule in the README** (Review Focus 4): after editing C++,
  run `uv sync --reinstall-package lobsim`.
- [ ] **Step 5: Commit** `build: scikit-build-core + pybind11 toolchain; tests parametrized over engines`.
  The commit has red `[cpp]` tests. That is intentional and is stated in the commit message;
  Task 4 turns them green.

---

### Task 4: C++ engine

**Files:**
- Create/Modify: `src/cpp/order_book.hpp`, `src/cpp/order_book.cpp`, `src/cpp/bindings.cpp`, `src/lobsim/fast.py`
- Test: existing parametrized suite + `tests/test_fast_bounds.py`

**Interfaces:**
- Consumes: the fixture and stub from Task 3.
- Produces:
  - **C++ types.**
    - `enum class Side : uint8_t { Buy, Sell }`
    - `struct Order { int64_t id; Side side; int64_t price; int64_t qty; }`
    - Each level is `struct Level { std::list<Order> orders; int64_t total; }`
    - Per side: `std::map<int64_t, Level>`. Bids are iterated with `rbegin()`. No custom
      comparator, mirroring the Python decision.
    - Index: `std::unordered_map<int64_t, Locator>` where
      `Locator { Side side; int64_t price; std::list<Order>::iterator it; }`.
  - **C++ result type.** Each event is `struct Ev { char kind; int64_t a, b, c, d, e, f; Side side; }`,
    where `kind` ∈ `'A','R','T','C'`. Reasons are an `enum` mapped to the exact M1 strings in `fast.py`.
  - **Bindings.** `CoreOrderBook.add_limit(id, side_is_buy: bool, price, qty) -> list[tuple]`,
    and the same for `add_market` and `cancel`. Tuple shapes:
    - `("A", id)`
    - `("R", id, reason_code)`
    - `("T", maker, taker, price, qty, aggressor_is_buy, maker_rem, taker_rem)`
    - `("C", id, remaining)`

    Plus `best_bid() -> int | None`, `best_ask()`, `depth(is_buy, n) -> list[tuple[int, int]]`
    and `resting_qty(id) -> int | None`.
  - **Wrapper.** `FastOrderBook` converts the tuples into `Accepted`/`Rejected`/`Trade`/`Cancelled`
    and `Side`.
  - **Raw benchmark mode.** `CoreOrderBook.run_ops(ops) -> int` applies a whole list of op tuples
    inside C++ and returns the event count. This lets `bench/run.py --impl cpp-raw` time the
    engine without the per-call boundary. Times are taken in Python around the single call,
    and the result is reported as per-op average only, with no per-op percentiles. Document that.

- [ ] **Step 1: Write the bounds test** (Review Focus 2) in `tests/test_fast_bounds.py`:

```python
def test_int64_max_roundtrip():
    b = FastOrderBook(); big = 2**63 - 1
    assert b.add_limit(big, Side.BUY, big, 1) == [Accepted(big)]
    assert b.best_bid() == big and b.cancel(big) == [Cancelled(big, 1)]

def test_out_of_range_raises_typeerror():
    with pytest.raises(TypeError):
        FastOrderBook().add_limit(2**63, Side.BUY, 100, 1)
```

- [ ] **Step 2: Run** `uv run pytest -q`. Expected: the `[cpp]` cases and the bounds tests FAIL.
- [ ] **Step 3: Implement** the C++ engine, the bindings and the wrapper. `match()` follows the
  Python `_match` line for line, so the two read side by side in the study note. When a list
  empties, erase the map entry before touching any iterator into it.
- [ ] **Step 4: Run** `uv sync --reinstall-package lobsim && uv run pytest -q`. Expected: all PASS,
  `[python]` and `[cpp]`. Mutation check: change `>=` to `>` in the C++ crossing check, rebuild,
  watch the `[cpp]` property test FAIL, then restore and rebuild.
- [ ] **Step 5: Commit** `feat: C++ matching engine behind pybind11, passing the shared suite`.

---

### Task 5: Benchmarks, CI, README and study note

**Files:**
- Modify: `bench/run.py` (cpp and cpp-raw impls), `.github/workflows/ci.yml`, `README.md`
- Create: `docs/benchmarks/M2.md`, `docs/notes/M2-performance.md`

- [ ] **Step 1: Run all three impls** with `--runs 5 --n 1000000`, writing JSON to `docs/benchmarks/`.
  Expected: three result sets. Copy the actual numbers; never invent or round them up.
- [ ] **Step 2: Write `docs/benchmarks/M2.md`.** It has:
  - A method block: machine (`Apple M3`, macOS version), Python version, Release build,
    generator + seed + N, warm-up, runs, median, and `perf_counter_ns`.
  - A table with throughput and p50/p99 for each op kind, comparing:
    - Python M1 baseline
    - Python optimized
    - C++ via wrapper
    - C++ raw

    Speedups are computed from those rows.
  - One paragraph on **where the time goes**: why "C++ via wrapper" is much slower than
    "C++ raw", meaning per-call boundary costs and Python object creation.
- [ ] **Step 3: CI.** Make sure both OS jobs build the extension. `uv run pytest` triggers the
  build, and scikit-build-core pulls CMake. Locally, run `uv build --wheel` as a smoke test.
  Expected: a wheel is built.
- [ ] **Step 4: Update the README.** Add a performance section (headline numbers linking to
  `M2.md`), the rebuild rule, and mark M2 ✅ in the roadmap.
- [ ] **Step 5: Write `docs/notes/M2-performance.md`**, the study note. It contains:
  1. How the benchmark works and why it is fair (seed, warm-up, fresh book, median).
  2. Reading the cProfile output: what it showed and what we changed.
  3. `OrderedDict` vs `dict` vs `deque`, with complexities.
  4. A C++ walkthrough of `match()` side by side with Python, covering `std::map`, `std::list`
     iterator stability, and why an `unordered_map` of iterators gives O(1) cancel.
  5. The boundary cost and how real systems avoid it (batching, staying in C++).
  6. At least 10 interview questions with answers. For example: "Why std::list and not
     std::vector?", "What does pybind11 do?", "Why are p99 numbers more important than averages
     in trading?", "How do you make sure the C++ and Python engines agree?"
- [ ] **Step 6: Verify** that `uv run pytest -q` is all PASS and that the README example still
  matches its stated output.
- [ ] **Step 7: Commit** `docs: M2 benchmark results, README performance section, study note`.
