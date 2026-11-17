"""Command line for the market data pipeline.

    uv run python -m lobsim.marketdata download  --day 2026-11-01 --symbol BTCUSDT
    uv run python -m lobsim.marketdata normalize --day 2026-11-01 --symbol BTCUSDT
    uv run python -m lobsim.marketdata validate  --day 2026-11-01 --symbol BTCUSDT [--limit N]
    uv run python -m lobsim.marketdata stats     --day 2026-11-01 --symbol BTCUSDT [--chart out.png]

Files live under --data-dir (default ./data, git-ignored):
    raw/{exchange}_{data_type}_{day}_{symbol}.csv.gz   downloaded from Tardis
    normalized/{l2,trades}_{day}_{symbol}.parquet      integer ticks/lots
"""

from __future__ import annotations

import argparse
import itertools
from datetime import date
from pathlib import Path

from lobsim.marketdata.instrument import BTCUSDT, Instrument
from lobsim.marketdata.normalize import (
    normalize_l2,
    normalize_trades,
    read_l2_parquet,
    read_trades_parquet,
)
from lobsim.marketdata.stats import replay_stats, weighted_percentile
from lobsim.marketdata.tardis import download
from lobsim.marketdata.validate import read_snapshots, validate

INSTRUMENTS: dict[str, Instrument] = {"BTCUSDT": BTCUSDT}
DATA_TYPES = ("incremental_book_L2", "trades", "book_snapshot_25")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lobsim.marketdata", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["download", "normalize", "validate", "stats"])
    ap.add_argument("--day", type=date.fromisoformat, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--limit", type=int, help="validate: only the first N snapshots")
    ap.add_argument("--chart", type=Path, help="stats: save a mid/spread chart here")
    args = ap.parse_args(argv)

    inst = INSTRUMENTS.get(args.symbol)
    if inst is None:
        ap.error(f"no instrument spec for {args.symbol!r}; known: {sorted(INSTRUMENTS)}")

    raw = args.data_dir / "raw"
    norm = args.data_dir / "normalized"
    tag = f"{args.day:%Y-%m-%d}_{args.symbol}"

    def raw_file(data_type: str) -> Path:
        return raw / f"{args.exchange}_{data_type}_{tag}.csv.gz"

    if args.command == "download":
        for data_type in DATA_TYPES:
            path = download(args.exchange, data_type, args.day, args.symbol, raw)
            print(f"{path}  {path.stat().st_size / 1e6:,.1f} MB")
        return 0

    if args.command == "normalize":
        n = normalize_l2(raw_file("incremental_book_L2"), norm / f"l2_{tag}.parquet", inst)
        print(f"L2 rows:    {n:,}")
        m = normalize_trades(raw_file("trades"), norm / f"trades_{tag}.parquet", inst)
        print(f"trade rows: {m:,}")
        return 0

    if args.command == "validate":
        snapshots = read_snapshots(raw_file("book_snapshot_25"), inst)
        if args.limit:
            snapshots = itertools.islice(snapshots, args.limit)
        report = validate(read_l2_parquet(norm / f"l2_{tag}.parquet"), snapshots)
        print(f"checked {report.checked:,} snapshots, matched {report.matched:,}, "
              f"crossed moments {report.crossed_moments:,}")
        for m in report.first_mismatches:
            print("mismatch:", m)
        return 0 if report.matched == report.checked else 1

    # stats
    s = replay_stats(read_l2_parquet(norm / f"l2_{tag}.parquet"),
                     read_trades_parquet(norm / f"trades_{tag}.parquet"))
    total_us = sum(s.spread_time_us.values())
    print(f"L2 rows {s.l2_rows:,}, batches {s.batches:,}, trades {s.trades:,}")
    print(f"replay: {s.seconds:,.1f} s, {s.l2_rows / s.seconds:,.0f} L2 rows/s, "
          f"{s.batches / s.seconds:,.0f} batches/s")
    if total_us:
        one_tick = s.spread_time_us.get(1, 0) / total_us
        print(f"spread (ticks, time-weighted): p50 {weighted_percentile(s.spread_time_us, 0.5)}, "
              f"p99 {weighted_percentile(s.spread_time_us, 0.99)}, at 1 tick {one_tick:.2%} of the time")
    print(f"crossed batches: {s.crossed_batches:,}")
    if args.chart:
        _chart(s, inst, args.chart, f"{args.exchange} {args.symbol} {args.day}")
        print(f"chart: {args.chart}")
    return 0


def _chart(s, inst: Instrument, path: Path, title: str) -> None:
    """Mid price and mean spread per 10-minute bucket (aggregates only)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hours = [(start - s.buckets[0][0]) / 3.6e9 for start, _, _ in s.buckets]
    tick = 10 ** -inst.price_decimals
    mids = [mid * tick for _, mid, _ in s.buckets]
    spreads = [spread for _, _, spread in s.buckets]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 5), sharex=True)
    ax1.plot(hours, mids, color="#2563eb")
    ax1.set_ylabel("mid price")
    ax1.set_title(f"{title}: 10-minute aggregates")
    ax2.plot(hours, spreads, color="#ea580c")
    ax2.set_ylabel("mean spread (ticks)")
    ax2.set_xlabel("hours since first bucket (UTC)")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)


if __name__ == "__main__":
    raise SystemExit(main())
