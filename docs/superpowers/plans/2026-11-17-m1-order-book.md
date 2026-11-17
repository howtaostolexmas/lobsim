# M1: Correct Order Book and Matching Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure-Python limit order book and price-time-priority matching engine, proven
correct by property tests against a naive reference engine. The milestone also adds an L2
level book that shares the same price-level core.

**Architecture:** A sorted price-level container, `PriceLevels`, is the shared core. Two
things are built on it:
- `OrderBook`: order-level matching with a FIFO queue per price, emitting an event stream.
- `LevelBook`: aggregate quantity per price, for L2 data replay later in M3/M4.

Every price and quantity is an integer number of ticks or lots.

**Tech Stack:** Python ≥ 3.11, `uv`, `sortedcontainers`, `pytest`, `hypothesis`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-17-orderbook-backtester-design.md`

## Global Constraints

- Package name `lobsim`, src layout (`src/lobsim/`). Python `>=3.11`.
- Prices and quantities are `int` (ticks/lots). No floats anywhere in the engine.
- Matching rule: price-time priority. Trades execute at the **maker's** (resting order's) price.
- Order types: limit, market, cancel only.
- Code, comments, docs in English. **Readable over clever.** Every non-obvious choice gets
  one comment explaining *why*.
- Small commits whose messages say why.
- One documented command runs all tests: `uv run pytest`.
- CI runs on Linux (ubuntu-latest) and macOS (macos-latest).

## Review Focus

1. Cancelling an order that was already fully filled → `Rejected(order_id, "unknown_id")`, book unchanged. (Task 2)
2. Cancelling a partially filled resting order → `Cancelled` carries only the remaining qty. (Task 2)
3. Market order into an empty or thin book → `Accepted`, any trades, then `Cancelled` with the unfilled remainder. It never rests. (Task 3)
4. Several makers at one price are filled strictly in arrival order, and an incoming limit at exactly the opposite best price trades rather than rests. (Task 2)
5. An ID may be reused once its order no longer rests (filled or cancelled). An ID that is currently resting is rejected with `"duplicate_id"`. (Task 3)

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest config |
| `.github/workflows/ci.yml` | Run `uv run pytest` on Linux and macOS |
| `src/lobsim/__init__.py` | Re-export the public API |
| `src/lobsim/types.py` | `Side`, event dataclasses, `Event` union |
| `src/lobsim/levels.py` | `PriceLevels`: sorted price → value map with best-first iteration |
| `src/lobsim/order_book.py` | `OrderBook`: matching engine |
| `src/lobsim/level_book.py` | `LevelBook`: L2 aggregate book |
| `tests/reference.py` | `NaiveOrderBook`: deliberately simple, slow reference engine |
| `tests/test_levels.py`, `tests/test_order_book.py`, `tests/test_market_and_rejects.py`, `tests/test_properties.py`, `tests/test_level_book.py` | Tests |
| `README.md` | Project overview, how to run, design decisions |
| `docs/notes/M1-order-book.md` | Study note for the owner |

---

### Task 1: Project scaffold, types, and `PriceLevels`

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`, `src/lobsim/__init__.py`, `src/lobsim/types.py`, `src/lobsim/levels.py`
- Test: `tests/test_levels.py`

**Interfaces:**
- Produces:
  - `class Side(Enum)`: `BUY = "buy"`, `SELL = "sell"`; property `opposite -> Side`.
  - Frozen, slotted dataclasses:
    - `Accepted(order_id: int)`
    - `Rejected(order_id: int, reason: str)`
    - `Trade(maker_id: int, taker_id: int, price: int, qty: int, aggressor: Side, maker_remaining: int, taker_remaining: int)`. A partial fill is a `Trade` with a remaining > 0; there is no separate partial-fill event.
    - `Cancelled(order_id: int, remaining_qty: int)`
  - `Event = Accepted | Rejected | Trade | Cancelled`
  - `class PriceLevels(Generic[V])`:
    - `__init__(self, side: Side)`. For BUY, best = highest price; for SELL, best = lowest.
    - `get(price: int) -> V | None`
    - `set(price: int, value: V) -> None`
    - `remove(price: int) -> None` (KeyError if absent)
    - `best() -> int | None`
    - `items_from_best() -> Iterator[tuple[int, V]]`
    - `__len__`, `__contains__`

- [ ] **Step 1: Scaffold.** `uv init --lib --name lobsim`, then edit `pyproject.toml`:
  `requires-python = ">=3.11"`, dependency `sortedcontainers`, dev group `pytest`, `hypothesis`,
  and `[tool.pytest.ini_options] testpaths = ["tests"]`, `pythonpath = ["tests"]`.
  Add the CI workflow (matrix `ubuntu-latest`, `macos-latest`; `astral-sh/setup-uv`, then `uv run pytest`).

- [ ] **Step 2: Write failing tests** in `tests/test_levels.py`:

```python
def test_buy_side_best_is_highest():
    lv = PriceLevels(Side.BUY)
    for p in (100, 105, 99): lv.set(p, p)
    assert lv.best() == 105
    assert [p for p, _ in lv.items_from_best()] == [105, 100, 99]

def test_sell_side_best_is_lowest():
    lv = PriceLevels(Side.SELL)
    for p in (100, 105, 99): lv.set(p, p)
    assert lv.best() == 99
    assert [p for p, _ in lv.items_from_best()] == [99, 100, 105]

def test_empty_best_is_none_and_remove_missing_raises():
    lv = PriceLevels(Side.BUY)
    assert lv.best() is None and len(lv) == 0
    with pytest.raises(KeyError): lv.remove(1)

def test_side_opposite():
    assert Side.BUY.opposite is Side.SELL and Side.SELL.opposite is Side.BUY
```

- [ ] **Step 3: Run** `uv run pytest tests/test_levels.py -v`. Expected: FAIL (import error).
- [ ] **Step 4: Implement** `types.py` and `levels.py`. Back `PriceLevels` with `sortedcontainers.SortedDict`. For BUY, iterate `reversed(keys)`. Do *not* negate prices, because negated keys are harder to explain in an interview.
- [ ] **Step 5: Run** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 6: Commit** `feat: scaffold lobsim with event types and sorted PriceLevels`.

---

### Task 2: `OrderBook` limit orders, matching, cancel

**Files:**
- Create: `src/lobsim/order_book.py`
- Test: `tests/test_order_book.py`

**Interfaces:**
- Consumes: `Side`, events, `PriceLevels` (Task 1).
- Produces: `class OrderBook` with
  - `add_limit(order_id: int, side: Side, price: int, qty: int) -> list[Event]`
  - `cancel(order_id: int) -> list[Event]`
  - `best_bid() -> int | None`, `best_ask() -> int | None`
  - `depth(side: Side, n: int) -> list[tuple[int, int]]`: (price, total qty), best first
  - `resting_qty(order_id: int) -> int | None`

**Event order for `add_limit`:** `Accepted`, then a `Trade` for each fill in matching order. Any remainder rests silently.

**Internal structure:** `PriceLevels[deque[_Resting]]` per side, plus `dict[int, _Resting]` keyed by ID for O(1) lookup. `_Resting` is a mutable slotted dataclass `(order_id, side, price, qty)`. Cancel removes the order from its deque by scanning that one level, which is O(level size). Write a comment saying this is acceptable for M1 and is the first profiling target in M2. Remove a price level as soon as its deque empties.

- [ ] **Step 1: Write failing tests:**

```python
def test_non_crossing_limit_rests():
    b = OrderBook()
    assert b.add_limit(1, Side.BUY, 100, 5) == [Accepted(1)]
    assert b.best_bid() == 100 and b.best_ask() is None
    assert b.depth(Side.BUY, 5) == [(100, 5)]

def test_crossing_limit_trades_at_maker_price():
    b = OrderBook(); b.add_limit(1, Side.SELL, 101, 5)
    ev = b.add_limit(2, Side.BUY, 103, 3)
    assert ev == [Accepted(2), Trade(1, 2, 101, 3, Side.BUY, 2, 0)]
    assert b.depth(Side.SELL, 5) == [(101, 2)] and b.best_bid() is None

def test_equal_price_crosses_and_fifo_within_level():          # Review Focus 4
    b = OrderBook()
    b.add_limit(1, Side.SELL, 100, 2); b.add_limit(2, Side.SELL, 100, 2)
    ev = b.add_limit(3, Side.BUY, 100, 3)
    assert ev == [Accepted(3), Trade(1, 3, 100, 2, Side.BUY, 0, 1),
                  Trade(2, 3, 100, 1, Side.BUY, 1, 0)]

def test_sweeps_multiple_levels_then_rests_remainder():
    b = OrderBook(); b.add_limit(1, Side.SELL, 100, 1); b.add_limit(2, Side.SELL, 101, 1)
    ev = b.add_limit(3, Side.BUY, 101, 5)
    assert [e.price for e in ev if isinstance(e, Trade)] == [100, 101]
    assert b.depth(Side.BUY, 5) == [(101, 3)] and b.best_ask() is None

def test_cancel_partially_filled_reports_remaining():           # Review Focus 2
    b = OrderBook(); b.add_limit(1, Side.SELL, 100, 5); b.add_limit(2, Side.BUY, 100, 2)
    assert b.cancel(1) == [Cancelled(1, 3)]
    assert b.best_ask() is None and b.resting_qty(1) is None

def test_cancel_fully_filled_is_unknown():                      # Review Focus 1
    b = OrderBook(); b.add_limit(1, Side.SELL, 100, 2); b.add_limit(2, Side.BUY, 100, 2)
    assert b.cancel(1) == [Rejected(1, "unknown_id")]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_order_book.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `OrderBook` as described above.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat: OrderBook with price-time matching and cancel`.

---

### Task 3: Market orders and validation

**Files:**
- Modify: `src/lobsim/order_book.py`
- Test: `tests/test_market_and_rejects.py`

**Interfaces:**
- Produces: `OrderBook.add_market(order_id: int, side: Side, qty: int) -> list[Event]`.
  Events: `Accepted`, then `Trade`s, then `Cancelled(order_id, unfilled)` if `unfilled > 0`. Market orders never rest.
- Rejection reasons are exact strings:
  - `"non_positive_qty"` (qty ≤ 0), for limit and market
  - `"non_positive_price"` (price ≤ 0), for limit
  - `"duplicate_id"`: the ID is currently resting, for limit and market
  - `"unknown_id"`: cancel of an ID that is not resting

  Checks run in that order. A rejected order emits only `[Rejected(...)]` and leaves the book unchanged.

- [ ] **Step 1: Write failing tests:**

```python
def test_market_into_empty_book_cancels_all():                  # Review Focus 3
    assert OrderBook().add_market(1, Side.BUY, 5) == [Accepted(1), Cancelled(1, 5)]

def test_market_partial_then_cancel_remainder():
    b = OrderBook(); b.add_limit(1, Side.SELL, 100, 2)
    assert b.add_market(2, Side.BUY, 5) == [
        Accepted(2), Trade(1, 2, 100, 2, Side.BUY, 0, 3), Cancelled(2, 3)]
    assert b.best_bid() is None

@pytest.mark.parametrize("qty", [0, -1])
def test_non_positive_qty_rejected(qty):
    b = OrderBook()
    assert b.add_limit(1, Side.BUY, 100, qty) == [Rejected(1, "non_positive_qty")]
    assert b.add_market(1, Side.BUY, qty) == [Rejected(1, "non_positive_qty")]

def test_non_positive_price_rejected():
    assert OrderBook().add_limit(1, Side.BUY, 0, 1) == [Rejected(1, "non_positive_price")]

def test_duplicate_resting_id_rejected_but_reuse_after_fill_ok():   # Review Focus 5
    b = OrderBook(); b.add_limit(1, Side.BUY, 100, 1)
    assert b.add_limit(1, Side.BUY, 99, 1) == [Rejected(1, "duplicate_id")]
    b.add_limit(2, Side.SELL, 100, 1)              # fills order 1
    assert b.add_limit(1, Side.BUY, 99, 1) == [Accepted(1)]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_market_and_rejects.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Pull the matching loop out of `add_limit` into a private `_match(taker_id, side, qty, limit_price: int | None) -> tuple[list[Trade], int]`, which both order types share. `None` means no price limit.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat: market orders and input validation`.

---

### Task 4: Reference engine and property tests

**Files:**
- Create: `tests/reference.py`, `tests/test_properties.py`

**Interfaces:**
- Consumes: the `OrderBook` public API (Tasks 2–3).
- Produces: `class NaiveOrderBook` with the **identical public API and event semantics** as
  `OrderBook`. It stores all resting orders in one plain `list` with an arrival sequence number.
  Matching scans that list for the best price, breaking ties by sequence. It uses no
  `PriceLevels` and no deques. It should be obviously correct by reading, with speed ignored.
  M2 will parametrize these same tests over the C++ engine.

**Hypothesis strategy:** a list of 1–200 ops drawn from
- `("limit", id, side, price, qty)`
- `("market", id, side, qty)`
- `("cancel", id)`

where `id ∈ [1, 30]` (small, so duplicates and cancels hit real orders), `price ∈ [95, 105]`
(narrow, so crossing is frequent), and `qty ∈ [-1, 10]` (so rejects get exercised).

- [ ] **Step 1: Write failing tests** in `tests/test_properties.py`:

```python
@given(ops())
def test_matches_reference(ops):
    a, b = OrderBook(), NaiveOrderBook()
    for op in ops:
        assert apply(a, op) == apply(b, op)
        assert a.depth(Side.BUY, 100) == b.depth(Side.BUY, 100)
        assert a.depth(Side.SELL, 100) == b.depth(Side.SELL, 100)

@given(ops())
def test_book_never_crossed(ops):
    b = OrderBook()
    for op in ops:
        apply(b, op)
        bid, ask = b.best_bid(), b.best_ask()
        assert bid is None or ask is None or bid < ask

@given(ops())
def test_quantity_conserved(ops):
    # For every accepted order: original qty == filled + cancelled + still resting.
    ...  # track per-order filled/cancelled from the event stream and compare with resting_qty
```

  The body of `test_quantity_conserved` needs care. Reused IDs mean the tracking must reset on
  each new `Accepted` for that ID. Each order's lifetime is checked when it ends (fully filled
  or cancelled), and again at the end of the run for orders that are still resting.

- [ ] **Step 2: Run** `uv run pytest tests/test_properties.py -v`. Expected: FAIL (`NaiveOrderBook` missing).
- [ ] **Step 3: Implement** `NaiveOrderBook` in `tests/reference.py`.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS. If a property fails, Hypothesis prints a minimal failing sequence: add it as a named unit test in `test_order_book.py` before fixing.
- [ ] **Step 5: Commit** `test: property tests against a naive reference engine`.

---

### Task 5: `LevelBook` (L2 aggregate book)

**Files:**
- Create: `src/lobsim/level_book.py`; Modify: `src/lobsim/__init__.py`
- Test: `tests/test_level_book.py`

**Interfaces:**
- Consumes: `PriceLevels`, `Side`.
- Produces: `class LevelBook` with
  - `set_level(side: Side, price: int, qty: int) -> None`: qty 0 removes the level; negative qty or non-positive price raises `ValueError`
  - `qty_at(side: Side, price: int) -> int` (0 if absent)
  - `best_bid() -> int | None`, `best_ask() -> int | None`
  - `depth(side: Side, n: int) -> list[tuple[int, int]]`
  - `clear() -> None`

  It does not check for crossed books, because real feeds briefly cross. Add a comment explaining this.

- [ ] **Step 1: Write failing tests:**

```python
def test_set_update_remove_level():
    lb = LevelBook()
    lb.set_level(Side.BUY, 100, 5); lb.set_level(Side.BUY, 101, 2)
    assert lb.depth(Side.BUY, 5) == [(101, 2), (100, 5)]
    lb.set_level(Side.BUY, 101, 0)
    assert lb.best_bid() == 100 and lb.qty_at(Side.BUY, 101) == 0

def test_invalid_inputs_raise():
    lb = LevelBook()
    with pytest.raises(ValueError): lb.set_level(Side.SELL, 100, -1)
    with pytest.raises(ValueError): lb.set_level(Side.SELL, 0, 1)

def test_clear():
    lb = LevelBook(); lb.set_level(Side.SELL, 100, 1); lb.clear()
    assert lb.best_ask() is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_level_book.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with one `PriceLevels[int]` per side.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat: LevelBook for L2 replay, sharing PriceLevels`.

---

### Task 6: README and study note

**Files:**
- Create/replace: `README.md`, `docs/notes/M1-order-book.md`

**README.md** (English) contains:
- One-paragraph pitch
- Quickstart: `uv sync`, `uv run pytest`, and a 10-line usage example. The example must actually run.
- Design decisions:
  - integer ticks
  - `SortedDict` + deque per level + ID index
  - trade at the maker's price
  - why there is no partial-fill event
  - why the reference engine plus property tests
  - O(level) cancel as a known cost
- Roadmap: M2–M4, one line each.

**`docs/notes/M1-order-book.md`** is the study note for the owner. It is written in English with
short sentences, and the owner may ask for a Thai version later. It contains:
1. A walkthrough of `add_limit` from call to events, referencing file:line.
2. Each design decision with the alternative considered and why it lost: heap vs sorted dict, negated keys, float prices.
3. Complexity of each operation.
4. At least 10 likely interview questions with concise answers. For example: "What happens if two orders arrive at the same price?", "How do you know your engine is correct?", "What's the complexity of cancel and how would you fix it?"

- [ ] **Step 1:** Write both files.
- [ ] **Step 2: Verify** the README example runs. Paste it into `uv run python -c '...'` and confirm the printed events are the ones the README shows.
- [ ] **Step 3: Verify** `uv run pytest -v`. Expected: all PASS.
- [ ] **Step 4: Commit** `docs: README and M1 study note`.
