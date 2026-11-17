"""Benchmark harness: time every operation on a seeded order flow.

Usage:
    uv run python bench/run.py --impl python --n 1000000 --runs 5 --json out.json

Impls:
  python   the pure-Python OrderBook
  cpp      the C++ engine called from Python per op, via FastOrderBook
  cpp-raw  the C++ engine running the whole op list inside C++ (run_ops);
           timed in C++ with steady_clock, so only throughput and mean ns/op
           are reported (no per-op percentiles)

Method:
  - The op list is generated before any timing starts.
  - One warm-up pass over the first 10% of ops runs on a throwaway book.
  - Each run uses a fresh book and times every call with perf_counter_ns.
  - Throughput = ops / sum of per-op times. Reported numbers are the median
    across runs. Per-op timing adds roughly tens of ns of overhead per call.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
from pathlib import Path
from time import perf_counter_ns

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.orderflow import apply, generate  # noqa: E402


def make_engine(impl: str):
    if impl == "python":
        from lobsim import OrderBook

        return OrderBook
    if impl == "cpp":
        from lobsim.fast import FastOrderBook

        return FastOrderBook
    raise SystemExit(f"unknown --impl {impl!r}")


def percentile(sorted_values: list[int], q: float) -> int:
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def one_run(factory, ops) -> dict:
    book = factory()
    times: dict[str, list[int]] = {"limit": [], "cancel": [], "market": []}
    for op in ops:
        t0 = perf_counter_ns()
        apply(book, op)
        times[op[0]].append(perf_counter_ns() - t0)
    total_ns = sum(sum(v) for v in times.values())
    result = {"ops_per_sec": len(ops) / (total_ns / 1e9), "kinds": {}}
    for kind, values in times.items():
        values.sort()
        result["kinds"][kind] = {
            "count": len(values),
            "p50_ns": percentile(values, 0.50),
            "p99_ns": percentile(values, 0.99),
        }
    return result


def raw_run(ops_encoded) -> dict:
    from lobsim._core import CoreOrderBook

    _, elapsed_ns = CoreOrderBook().run_ops(ops_encoded)
    return {"ops_per_sec": len(ops_encoded) / (elapsed_ns / 1e9), "mean_ns": elapsed_ns / len(ops_encoded)}


def summarize(runs: list[dict]) -> dict:
    out = {"ops_per_sec": statistics.median(r["ops_per_sec"] for r in runs), "kinds": {}}
    for kind in runs[0]["kinds"]:
        out["kinds"][kind] = {
            "count": runs[0]["kinds"][kind]["count"],
            "p50_ns": statistics.median(r["kinds"][kind]["p50_ns"] for r in runs),
            "p99_ns": statistics.median(r["kinds"][kind]["p99_ns"] for r in runs),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--impl", default="python")
    ap.add_argument("--n", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    ops = generate(args.n, args.seed)
    meta = dict(
        impl=args.impl,
        n=args.n,
        seed=args.seed,
        runs=args.runs,
        python=platform.python_version(),
        machine=f"{platform.machine()} / {platform.platform()}",
    )
    header = f"impl={args.impl} n={args.n:,} seed={args.seed} runs={args.runs} (medians)"

    if args.impl == "cpp-raw":
        from lobsim.fast import encode_ops

        encoded = encode_ops(ops)
        raw_run(encoded[: max(1, args.n // 10)])  # warm-up
        runs = [raw_run(encoded) for _ in range(args.runs)]
        summary = {
            "ops_per_sec": statistics.median(r["ops_per_sec"] for r in runs),
            "mean_ns": statistics.median(r["mean_ns"] for r in runs),
            **meta,
        }
        print(header)
        print(f"throughput: {summary['ops_per_sec']:,.0f} ops/sec")
        print(f"mean: {summary['mean_ns']:,.1f} ns/op (no per-op percentiles in raw mode)")
        write_json(args.json, summary)
        return

    factory = make_engine(args.impl)

    warm = factory()
    for op in ops[: max(1, args.n // 10)]:
        apply(warm, op)

    summary = summarize([one_run(factory, ops) for _ in range(args.runs)])
    summary.update(meta)

    print(header)
    print(f"throughput: {summary['ops_per_sec']:,.0f} ops/sec")
    print(f"{'op':<8}{'count':>10}{'p50 ns':>10}{'p99 ns':>10}")
    for kind, k in summary["kinds"].items():
        print(f"{kind:<8}{k['count']:>10,}{k['p50_ns']:>10,.0f}{k['p99_ns']:>10,.0f}")

    write_json(args.json, summary)


def write_json(path: Path | None, summary: dict) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
