# M3: Market Data Replay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download one day of real Binance BTCUSDT L2 order-book updates and trades, normalize
them to integer ticks and lots in Parquet, and replay them through `LevelBook` on a single
time-ordered event clock. The milestone also proves the rebuilt book matches the source's own
top-25 snapshots.

**Architecture:** The pipeline has four stages:
1. **Download.** A downloader fetches Tardis.dev CSVs into `data/raw/`. Data for the first day
   of each month is free with no API key.
2. **Normalize.** A normalizer parses decimal strings *exactly* into integer ticks and lots and
   writes typed Parquet to `data/normalized/`.
3. **Replay.** A replayer merges book batches and trades into one stream ordered by local
   arrival time, applying book batches to a `LevelBook`.
4. **Validate.** A validator replays up to each source snapshot's timestamp and compares the
   rebuilt top 25 levels.

Everything is pure Python. Tests run offline on small hand-written fixtures.

**Tech Stack:**
- Python ≥ 3.11 and uv
- `pyarrow` for Parquet
- `matplotlib`, in an optional dependency group, for one chart
- pytest and the standard library: `urllib`, `gzip`, `csv`, `decimal`

**Spec:** `docs/superpowers/specs/2026-11-17-orderbook-backtester-design.md` (M3 section)

## Data source facts (verified 2026-11-18)

- **URL:** `https://datasets.tardis.dev/v1/{exchange}/{data_type}/{YYYY}/{MM}/{DD}/{SYMBOL}.csv.gz`
- **Access:** day 1 of a month downloads without a key. Day 2 returned HTTP 401.
- **Chosen day:** `binance` `BTCUSDT` `2026-11-01`. Compressed sizes:
  - `incremental_book_L2`: 59.9 MB
  - `trades`: 8.6 MB
  - `book_snapshot_25`: 17.2 MB
- **`incremental_book_L2` columns:** `exchange, symbol, timestamp, local_timestamp, is_snapshot, side, price, amount`
  - Timestamps are µs since the epoch. `timestamp` is the exchange's time; `local_timestamp` is when the message arrived.
  - `amount` is the level's absolute size; 0 means remove the level.
  - Snapshot rule: a snapshot row that follows non-snapshot rows means "discard the book".
    Non-snapshot rows before the first snapshot are skipped.
- **`trades` columns:** `exchange, symbol, timestamp, local_timestamp, id, side, price, amount`.
  `side` is the taker's side: `buy`, `sell` or `unknown`.
- **`book_snapshot_25` columns:** `exchange, symbol, timestamp, local_timestamp`, then for
  i = 0..24: `asks[i].price, asks[i].amount, bids[i].price, bids[i].amount`.
  - Empty fields mean fewer than 25 levels.
  - Crossed levels are removed by the source.
- **Licence (Tardis ToS §9):** no redistribution of raw data or derived *datasets*. Charts and
  statistics are allowed. So `data/` is git-ignored, and only charts and summary numbers are committed.

## Global Constraints

- **Never commit market data.** `data/` is in `.gitignore`. Test fixtures are **hand-written
  synthetic rows** in Tardis format, never copied from real files.
- **Tests never touch the network.** The downloader takes an injectable `fetch` function.
- **Exact decimal parsing.** No `float` anywhere between CSV and ticks. A value that isn't a
  whole number of ticks or lots raises `ValueError` naming the value.
- **Instrument spec for BTCUSDT:** `price_decimals=2` (tick 0.01) and `qty_decimals=5` (lot
  0.00001). If the real data contradicts this, stop and record a ruling. Do not round.
- **One time axis.** Replay is ordered by `ts_local` (arrival time, what a strategy would see).
  When a book batch and a trade share a `ts_local`, the book batch comes first. Within a source,
  file order is preserved.
- Python only in M3. No C++ changes.
- Readable over clever. Small commits that say why. English.
- One command per stage, through `uv run python -m lobsim.marketdata <subcommand>`.

## Review Focus

1. A snapshot block that arrives after normal updates resets the book, and non-snapshot rows
   before the first snapshot are ignored. (Task 3)
2. Price and qty strings in any form the CSV may contain must parse exactly or fail loudly:
   `"67000"`, `"67000.10"`, `"0.00001"`, `"1e-05"`. (Task 1)
3. An update with amount 0 for a level that doesn't exist is a no-op, not an error. (Task 3)
4. A book batch and a trade with the same `ts_local` follow the documented tie-break, and so do
   two trades with the same timestamp. (Task 3)
5. When the replayed book is momentarily crossed, the validator handles it the same way the
   source does: crossed levels are excluded on both sides. It is never counted as a silent pass
   or a false mismatch. (Task 4)

---

## File Structure

| File | Responsibility |
|---|---|
| `src/lobsim/marketdata/__init__.py` | Public names of the package |
| `src/lobsim/marketdata/instrument.py` | `Instrument`, `to_units(text, decimals) -> int` |
| `src/lobsim/marketdata/tardis.py` | URL builder, downloader, CSV readers yielding typed rows |
| `src/lobsim/marketdata/normalize.py` | CSV.gz → Parquet (book updates, trades) |
| `src/lobsim/marketdata/replay.py` | `BookBatch`, `TradePrint`, `events(...)`, `Replayer` |
| `src/lobsim/marketdata/validate.py` | Compare the replayed book with `book_snapshot_25` |
| `src/lobsim/marketdata/__main__.py` | CLI: `download`, `normalize`, `validate`, `stats` |
| `tests/data/*.csv` | Tiny synthetic fixtures in Tardis format |
| `tests/marketdata/test_*.py` | Tests per module |
| `docs/notes/M3-market-data.md` | Study note |
| `docs/benchmarks/M3-replay.md` | Real-data run: row counts, validation result, replay speed, chart |

---

### Task 1: Instrument spec and exact decimal → integer parsing

**Files:**
- Create: `src/lobsim/marketdata/__init__.py`, `src/lobsim/marketdata/instrument.py`
- Test: `tests/marketdata/test_instrument.py`

**Interfaces:**
- Produces:
  - `to_units(text: str, decimals: int) -> int`. Examples: `to_units("67000.1", 2) == 6700010`
    and `to_units("0.00001", 5) == 1`. It raises `ValueError` if `text` has more precision than
    `decimals` allows, or if it is not a number.
  - `@dataclass(frozen=True) Instrument(symbol: str, price_decimals: int, qty_decimals: int)`
    with methods `price(text) -> int` and `qty(text) -> int`.
  - `BTCUSDT = Instrument("BTCUSDT", 2, 5)`

  Fast path: plain `digits[.digits]` strings are scaled by string manipulation. Anything else,
  such as exponents, goes through `decimal.Decimal` and must still be exact.

- [ ] **Step 1: Write failing tests:**

```python
@pytest.mark.parametrize("text,dec,expected", [
    ("67000", 2, 6700000), ("67000.1", 2, 6700010), ("67000.10", 2, 6700010),
    ("0.00001", 5, 1), ("1e-05", 5, 1), ("0", 5, 0), ("12.50000", 2, 1250)])
def test_to_units_exact(text, dec, expected):
    assert to_units(text, dec) == expected

@pytest.mark.parametrize("text", ["67000.001", "1e-06", "abc", "", "-"])
def test_to_units_rejects_off_tick_or_garbage(text):
    with pytest.raises(ValueError):
        to_units(text, 2 if text != "1e-06" else 5)

def test_instrument_helpers():
    assert BTCUSDT.price("67000.12") == 6700012 and BTCUSDT.qty("0.5") == 50000
```

- [ ] **Step 2: Run** `uv run pytest tests/marketdata/test_instrument.py -q`. Expected: FAIL (import error).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(marketdata): exact decimal-to-tick parsing and instrument spec`.

---

### Task 2: Tardis CSV readers and the Parquet normalizer

**Files:**
- Create: `src/lobsim/marketdata/tardis.py`, `src/lobsim/marketdata/normalize.py`,
  `tests/data/l2_sample.csv`, `tests/data/trades_sample.csv`
- Modify: `pyproject.toml` (dependency `pyarrow>=15`), `.gitignore` (`data/`)
- Test: `tests/marketdata/test_normalize.py`

**Interfaces:**
- Consumes: `Instrument` (Task 1).
- Produces:
  - `read_l2(path, inst) -> Iterator[L2Row]`, where
    `L2Row = NamedTuple(ts_exchange: int, ts_local: int, is_snapshot: bool, side: Side, price: int, qty: int)`.
    It reads `.csv` or `.csv.gz` (chosen by suffix) with `csv.DictReader`.
  - `read_trades(path, inst) -> Iterator[TradeRow]`, where
    `TradeRow = NamedTuple(ts_exchange: int, ts_local: int, side: Side | None, price: int, qty: int, trade_id: str)`.
    Side `"unknown"` maps to `None`.
  - `normalize_l2(src, dst, inst) -> int` and `normalize_trades(src, dst, inst) -> int` return the
    number of rows written. The Parquet columns are:
    - L2: `ts_exchange int64, ts_local int64, is_snapshot bool, side int8 (0 bid, 1 ask), price int64, qty int64`
    - Trades: `ts_exchange int64, ts_local int64, side int8 (1 buy, -1 sell, 0 unknown), price int64, qty int64, trade_id string`
    - Both are written with `pyarrow.parquet.ParquetWriter` in batches of 500_000 rows, so a
      day never has to fit in memory.
  - `read_l2_parquet(path) -> Iterator[L2Row]` and `read_trades_parquet(path) -> Iterator[TradeRow]`
    stream the Parquet back in row groups.
- **Fixtures** are hand-written, about 12 rows each, in the exact Tardis header. Prices are
  around `100.00` and timestamps are small round numbers. `l2_sample.csv` contains:
  - an initial snapshot block
  - updates, including one amount `0` removal and one `0` for an absent level
  - a second snapshot block after updates (the reset case)
  - one non-snapshot row *before* the first snapshot

  `trades_sample.csv` contains `buy`, `sell` and `unknown` sides, and two trades sharing a
  `local_timestamp` with an L2 row.

- [ ] **Step 1: Write failing tests:**

```python
def test_read_l2_parses_types(fixture_l2):
    rows = list(read_l2(fixture_l2, TEST_INST))
    assert rows[0] == L2Row(..., is_snapshot=False, side=Side.BUY, price=10000, qty=...)  # first file row
    assert all(isinstance(r.price, int) and isinstance(r.qty, int) for r in rows)

def test_read_trades_unknown_side_is_none(fixture_trades):
    assert any(r.side is None for r in read_trades(fixture_trades, TEST_INST))

def test_parquet_roundtrip_identical(tmp_path, fixture_l2, fixture_trades):
    n = normalize_l2(fixture_l2, tmp_path / "l2.parquet", TEST_INST)
    assert n == len(list(read_l2(fixture_l2, TEST_INST)))
    assert list(read_l2_parquet(tmp_path / "l2.parquet")) == list(read_l2(fixture_l2, TEST_INST))
    normalize_trades(fixture_trades, tmp_path / "t.parquet", TEST_INST)
    assert list(read_trades_parquet(tmp_path / "t.parquet")) == list(read_trades(fixture_trades, TEST_INST))

def test_gz_and_plain_csv_read_the_same(tmp_path, fixture_l2):
    gz = tmp_path / "l2.csv.gz"; gz.write_bytes(gzip.compress(fixture_l2.read_bytes()))
    assert list(read_l2(gz, TEST_INST)) == list(read_l2(fixture_l2, TEST_INST))

def test_off_tick_price_in_file_raises_with_context(tmp_path): ...
    # write a 2-row CSV with price "100.001" → ValueError whose message contains "100.001" and the line number
```

  `TEST_INST = Instrument("TEST", 2, 3)`. Put the fixture paths in `tests/marketdata/conftest.py`.
- [ ] **Step 2: Run** `uv run pytest tests/marketdata -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(marketdata): Tardis CSV readers and streaming Parquet normalizer`.

---

### Task 3: Replay on one event clock

**Files:**
- Create: `src/lobsim/marketdata/replay.py`
- Test: `tests/marketdata/test_replay.py`

**Interfaces:**
- Consumes: `L2Row`, `TradeRow` iterators (Task 2), and `LevelBook` (M1).
- Produces:
  - `@dataclass(frozen=True) BookBatch(ts_local: int, ts_exchange: int, is_snapshot: bool, levels: tuple[tuple[Side, int, int], ...])`:
    consecutive L2 rows with the same `ts_local` **and** the same `is_snapshot` form one batch.
    That is one exchange message, applied atomically.
  - `@dataclass(frozen=True) TradePrint(ts_local, ts_exchange, side: Side | None, price, qty, trade_id)`
  - `batches(l2_rows) -> Iterator[BookBatch]`
  - `events(l2_rows, trade_rows) -> Iterator[BookBatch | TradePrint]`, a `heapq.merge` keyed on
    `(ts_local, source_rank, seq)` with book = 0, trade = 1 and `seq` the per-source position.
    Both inputs must already be sorted by `ts_local`; if one goes backwards, raise `ValueError`
    rather than silently reordering.
  - `class Replayer` with:
    - `__init__(self)`, which creates `self.book = LevelBook()`
    - `apply(self, batch: BookBatch) -> None`, implementing the snapshot rule below
    - `ready -> bool`, true once the first snapshot has been applied
    - `last_ts_local: int`

  **Snapshot rule.** `apply` remembers whether the previous batch was a snapshot. When a
  snapshot batch arrives after a non-snapshot batch (or as the first batch), it calls
  `book.clear()` first. A snapshot can span several batches with the same flag, so it clears
  only on the transition. Non-snapshot batches before the first snapshot are skipped.

- [ ] **Step 1: Write failing tests** (on the fixtures from Task 2):

```python
def test_batches_group_by_ts_local_and_flag(): ...      # exact batch count and levels for fixture
def test_updates_before_first_snapshot_are_skipped():   # Review Focus 1
    r = Replayer(); r.apply(BookBatch(1, 1, False, ((Side.BUY, 100, 5),)))
    assert not r.ready and r.book.best_bid() is None
def test_snapshot_after_updates_resets_book():          # Review Focus 1
    r = Replayer()
    r.apply(BookBatch(1, 1, True, ((Side.BUY, 100, 5), (Side.SELL, 101, 5))))
    r.apply(BookBatch(2, 2, False, ((Side.BUY, 99, 3),)))
    r.apply(BookBatch(3, 3, True, ((Side.BUY, 98, 1),)))
    assert r.book.depth(Side.BUY, 5) == [(98, 1)] and r.book.best_ask() is None
def test_multi_batch_snapshot_clears_only_once(): ...    # two consecutive snapshot batches → union kept
def test_zero_for_absent_level_is_noop():               # Review Focus 3
    r = Replayer(); r.apply(BookBatch(1, 1, True, ((Side.BUY, 100, 5),)))
    r.apply(BookBatch(2, 2, False, ((Side.SELL, 200, 0),)))
    assert r.book.depth(Side.BUY, 5) == [(100, 5)]
def test_merge_tie_book_before_trade_and_trades_keep_file_order():   # Review Focus 4
    ...  # same ts_local → BookBatch first; two trades with same ts → file order
def test_events_rejects_unsorted_input():
    with pytest.raises(ValueError): list(events(iter([l2(ts=5), l2(ts=3)]), iter([])))
```

- [ ] **Step 2: Run** `uv run pytest tests/marketdata/test_replay.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(marketdata): single-clock replay with Tardis snapshot semantics`.

---

### Task 4: Validate the rebuilt book against `book_snapshot_25`

**Files:**
- Create: `src/lobsim/marketdata/validate.py`, `tests/data/snap25_sample.csv`
- Test: `tests/marketdata/test_validate.py`

**Interfaces:**
- Consumes: `batches`, `Replayer` (Task 3), and `Instrument`.
- Produces:
  - `read_snapshots(path, inst) -> Iterator[Snapshot]`, where
    `Snapshot = NamedTuple(ts_local: int, bids: list[tuple[int, int]], asks: list[tuple[int, int]])`.
    Empty fields end a side's list.
  - `top_uncrossed(book: LevelBook, n: int) -> tuple[bids, asks]`: the top n levels per side
    after dropping crossed levels by the source's rule. While best bid ≥ best ask, drop the
    level whose update came *earlier*. If that can't be known from `LevelBook`, drop both
    crossing levels and record a ruling. The executor confirms the exact rule against real data
    in Task 6 and records it.
  - `validate(l2_rows, snapshots, n=25) -> ValidationReport(checked: int, matched: int, crossed_moments: int, first_mismatches: list[dict])`:
    - For each snapshot, apply every batch with `ts_local <= snapshot.ts_local`.
    - Compare `top_uncrossed(book, n)` with the snapshot, and keep the first 5 mismatch details.
- **Fixture** `snap25_sample.csv` uses the full 104-column Tardis header, with only the first 2
  levels filled. Its rows are consistent with `l2_sample.csv` at three timestamps, plus one row
  deliberately wrong.

- [ ] **Step 1: Write failing tests:**

```python
def test_validate_counts_matches_and_reports_mismatch():
    report = validate(read_l2(L2), read_snapshots(SNAP, TEST_INST), n=2)
    assert report.checked == 4 and report.matched == 3
    assert report.first_mismatches[0]["ts_local"] == <the wrong row's ts>

def test_crossed_book_is_compared_after_uncrossing():          # Review Focus 5
    ...  # LevelBook with bid 101 / ask 100 → top_uncrossed excludes the crossing levels
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(marketdata): validate replayed book against source top-25 snapshots`.

---

### Task 5: Downloader and CLI

**Files:**
- Modify: `src/lobsim/marketdata/tardis.py` (download)
- Create: `src/lobsim/marketdata/__main__.py`
- Test: `tests/marketdata/test_download_cli.py`

**Interfaces:**
- Produces:
  - `tardis_url(exchange, data_type, day: date, symbol) -> str`, using the URL format above.
  - `download(exchange, data_type, day, symbol, dest_dir: Path, fetch=_urllib_fetch) -> Path`.
    It returns `dest_dir / f"{exchange}_{data_type}_{day:%Y-%m-%d}_{symbol}.csv.gz"`.
    - If the file exists, it skips the download.
    - It writes to a `.part` file, then renames, so an interrupted download never looks complete.
    - It checks the gzip magic bytes.
    - On HTTP 401 it raises `RuntimeError` saying that only day 1 of each month is free without an API key.
    - `fetch(url, out_file) -> None` is injectable, so tests never hit the network.
  - CLI, run as `uv run python -m lobsim.marketdata ...`:
    - `download --day 2026-11-01 --symbol BTCUSDT` fetches the three data types into `data/raw/`.
    - `normalize --day ... --symbol ...` writes `data/normalized/{l2,trades}_{day}_{symbol}.parquet`.
    - `validate --day ... --symbol ... [--limit N]` validates against the first N snapshots (default: all) and prints the report.
    - `stats --day ... --symbol ... [--chart PATH]` replays the full day and prints:
      - row and batch counts
      - replay throughput
      - spread distribution in ticks: p50, p99, % of time at 1 tick
      - crossed moments

      It optionally saves a chart of mid price and spread over the day. The chart is
      aggregated per 10-minute bucket (ToS §9.3(2)) and needs `matplotlib` from the `viz` group.

- [ ] **Step 1: Write failing tests:**

```python
def test_tardis_url():
    assert tardis_url("binance", "trades", date(2026, 11, 1), "BTCUSDT") == \
        "https://datasets.tardis.dev/v1/binance/trades/2026/11/01/BTCUSDT.csv.gz"

def test_download_skips_existing_and_uses_part_file(tmp_path):
    calls = []
    def fake_fetch(url, out):
        calls.append(url); out.write(gzip.compress(b"a,b\n"))
    p1 = download("binance", "trades", date(2026, 11, 1), "X", tmp_path, fetch=fake_fetch)
    p2 = download("binance", "trades", date(2026, 11, 1), "X", tmp_path, fetch=fake_fetch)
    assert p1 == p2 and p1.exists() and len(calls) == 1 and not list(tmp_path.glob("*.part"))

def test_download_rejects_non_gzip(tmp_path): ...   # fake fetch writes b"<html>" → ValueError, no final file
def test_download_401_message(tmp_path): ...        # fake fetch raises HTTPError 401 → RuntimeError mentioning "day 1"
def test_cli_normalize_and_validate_on_fixtures(tmp_path, monkeypatch): ...  # point data dir at tmp_path, run main([...])
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** `main(argv: list[str] | None = None) -> int` is testable. The data
  root is set by `--data-dir` (default `data/`).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(marketdata): downloader with atomic writes and a CLI for each stage`.

---

### Task 6: Real-data run and documentation

**Needs the owner's go-ahead to download** about 86 MB from datasets.tardis.dev. This is
asked once, with the plan handoff.

- [ ] **Step 1: Download, normalize and confirm the instrument spec.** Run the `download` and
  `normalize` subcommands for `2026-11-01 BTCUSDT`. Expected: normalize finishes without a
  `ValueError`, which confirms tick 0.01 and lot 0.00001. If it raises, stop, record a ruling,
  and fix the spec from the data, never by rounding.
- [ ] **Step 2: Validate.** Run `validate` on the full day. Expected: `matched == checked`.
  - Investigate any mismatch with superpowers:systematic-debugging before going on.
  - Confirm the crossed-level rule on real crossed moments, and fix `top_uncrossed` with a test if needed.
  - Record the final numbers.
- [ ] **Step 3: Stats.** Run `stats --chart docs/benchmarks/m3-mid-spread.png`. Record the
  counts, the replay throughput (rows/sec and batches/sec) and the spread figures.
- [ ] **Step 4: Write `docs/benchmarks/M3-replay.md`.** It covers:
  - method: machine, day, symbol, file sizes, commands
  - row and batch counts
  - the validation result
  - replay speed
  - spread stats
  - the chart
  - a "Data licence" paragraph: raw data isn't committed, and charts are 10-minute aggregates
- [ ] **Step 5: Update the README.** Add an M3 section with the reproduce commands, add the
  data licence note, and mark M3 ✅ in the roadmap.
- [ ] **Step 6: Write `docs/notes/M3-market-data.md`**, the study note. It covers:
  1. L2 vs L3 vs trades, and why L2 has no queue information. This sets up M4's queue model.
  2. Exchange time vs local time, which one we sort by, and why.
  3. Snapshot semantics and the reset rule.
  4. Why exact decimal parsing matters, with a float counter-example.
  5. Why Parquet: columnar, typed, compressed, streamable.
  6. How validation proves the replay is right, and what crossed books are.
  7. At least 10 interview questions with answers.
- [ ] **Step 7: Verify** that `uv run pytest -q` is all PASS and that `git status` shows no
  files under `data/`.
- [ ] **Step 8: Commit** `docs: M3 real-data replay results, README section, study note`.
