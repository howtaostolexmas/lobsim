"""Run the naive market maker on a normalized day of market data.

    uv run python -m lobsim.backtest --day 2026-11-01 --symbol BTCUSDT
    uv run --group viz python -m lobsim.backtest --day 2026-11-01 --symbol BTCUSDT --chart out.png

Needs the Parquet files from `python -m lobsim.marketdata normalize`.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
from datetime import date
from pathlib import Path

from lobsim.backtest.engine import Backtest
from lobsim.backtest.strategy import NaiveMarketMaker
from lobsim.backtest.types import SimConfig
from lobsim.marketdata.__main__ import INSTRUMENTS
from lobsim.marketdata.normalize import read_l2_parquet, read_trades_parquet
from lobsim.marketdata.replay import events


def main(argv: list[str] | None = None) -> int:
    d = SimConfig()
    ap = argparse.ArgumentParser(prog="python -m lobsim.backtest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", type=date.fromisoformat, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--hours", type=float, help="only replay the first H hours")
    ap.add_argument("--order-latency-us", type=int, default=d.order_latency_us)
    ap.add_argument("--feed-latency-us", type=int, default=d.feed_latency_us)
    ap.add_argument("--maker-fee-bps", type=float, default=d.maker_fee_bps)
    ap.add_argument("--taker-fee-bps", type=float, default=d.taker_fee_bps)
    ap.add_argument("--size-lots", type=int, default=100)
    ap.add_argument("--max-position-lots", type=int, default=1_000)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--chart", type=Path)
    args = ap.parse_args(argv)

    inst = INSTRUMENTS.get(args.symbol)
    if inst is None:
        ap.error(f"no instrument spec for {args.symbol!r}; known: {sorted(INSTRUMENTS)}")
    cfg = SimConfig(args.order_latency_us, args.feed_latency_us, args.maker_fee_bps, args.taker_fee_bps)

    tag = f"{args.day:%Y-%m-%d}_{args.symbol}"
    norm = args.data_dir / "normalized"
    stream = events(read_l2_parquet(norm / f"l2_{tag}.parquet"),
                    read_trades_parquet(norm / f"trades_{tag}.parquet"))
    if args.hours is not None:
        stream = _first_hours(stream, args.hours)

    strategy = NaiveMarketMaker(args.size_lots, args.max_position_lots)
    result = Backtest(strategy, cfg, inst).run(stream)

    summary = result.summary(inst)
    summary["config"] = dataclasses.asdict(cfg) | {
        "size_lots": args.size_lots, "max_position_lots": args.max_position_lots,
        "hours": args.hours, "day": str(args.day), "symbol": args.symbol,
    }
    for key, value in summary.items():
        if key != "config":
            print(f"{key:>18}: {value:,.6g}" if isinstance(value, float) else f"{key:>18}: {value:,}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        # runtime varies between runs; keep it out of the file so reruns compare equal
        stable = {k: v for k, v in summary.items() if k != "runtime_s"}
        args.json.write_text(json.dumps(stable, indent=2, sort_keys=True) + "\n")
    if args.chart:
        result.chart(args.chart, f"NaiveMarketMaker on {args.symbol} {args.day} (10-minute samples)", inst)
    return 0


def _first_hours(stream, hours: float):
    stream = iter(stream)
    first = next(stream, None)
    if first is None:
        return iter(())
    end = first.ts_local + int(hours * 3_600_000_000)
    return itertools.chain([first], itertools.takewhile(lambda e: e.ts_local < end, stream))


if __name__ == "__main__":
    raise SystemExit(main())
