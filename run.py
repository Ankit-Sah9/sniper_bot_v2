"""
run.py — Command-line entry point for the NQ ICT 2022 backtester.

Usage:
    python run.py gen-data                generate synthetic 5-min sample data
    python run.py backtest                run the full backtest -> writes outputs
    python run.py walk-forward            run walk-forward validation
    python run.py all                     gen-data (if missing) + backtest + WF
    python run.py config                  print the active strategy parameters

Options:
    --source-tz America/New_York|UTC      timezone of the input CSV (default NY)
    --data PATH                           path to 5-min CSV (default data/history/NQ_5min.csv)
    --start YYYY-MM-DD --end YYYY-MM-DD   restrict backtest date range
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from engine.config import CONFIG
from engine.data_loader import load_candles
from engine.models import Candle
from engine.backtest import (
    run_backtest, compute_metrics, write_outputs, walk_forward,
    TRADE_LOG, METRICS_JSON, EQUITY_CSV, FOLD_CSV,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "history" / "NQ_master.csv"


def _parse_date(s: str | None):
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def cmd_gen_data(_args) -> int:
    from engine.generate_sample_data import generate
    generate()
    return 0


def cmd_config(_args) -> int:
    print("=== Active Strategy Configuration (v1) ===")
    for k, v in asdict(CONFIG).items():
        print(f"  {k}: {v}")
    return 0


def cmd_pull_data(args) -> int:
    """Pull real M5 bars from a running MT5 terminal (Windows + MetaTrader5)."""
    if not args.symbol or not args.start or not args.end:
        print("pull-data requires --symbol, --start and --end.\n"
              "Example: python run.py pull-data --symbol NAS100 "
              "--start 2024-01-01 --end 2024-06-30")
        return 1
    from engine.mt5_export import export
    out = Path(args.data) if args.data else DEFAULT_DATA
    export(args.symbol, args.start, args.end, out, args.assume_offset)
    return 0


def _load(args):
    path = Path(args.data) if args.data else DEFAULT_DATA
    if not path.exists():
        print(f"No data at {path}.\n"
              f"  - Put your full history CSV there (e.g. all Dukascopy years in one file), or\n"
              f"  - pass --data PATH, or\n"
              f"  - run: python run.py gen-data  (synthetic demo only)")
        return None
    candles = load_candles(path, source_tz=args.source_tz)
    if not candles:
        print(f"Loaded 0 candles from {path} — check the file format / columns.")
        return None
    print(f"Loaded {len(candles):,} candles "
          f"({candles[0].time.date()} -> {candles[-1].time.date()})")
    # Report the slice actually being tested, if a range is set.
    if args.start or args.end:
        lo = args.start or str(candles[0].time.date())
        hi = args.end or str(candles[-1].time.date())
        print(f"  Backtest range: {lo} -> {hi}")
    return candles


def _print_metrics(title: str, m: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"  Trades:        {m['total_trades']}")
    print(f"  Wins/Losses:   {m['wins']} / {m['losses']}")
    if m.get('flats'):
        print(f"  Flats (16:00): {m['flats']}  (closed at session flat)")
    print(f"  Win rate:      {m['win_rate_pct']}%")
    print(f"  Total R:       {m['total_r']}")
    print(f"  Avg R/trade:   {m['avg_r_per_trade']}")
    print(f"  Profit factor: {m['profit_factor']}")
    print(f"  Max DD (R):    {m['max_drawdown_r']}")
    print(f"  Total P&L:     ${m['total_pnl']:,.2f}")


def cmd_backtest(args) -> int:
    candles = _load(args)
    if candles is None:
        return 1
    trades = run_backtest(candles, start=_parse_date(args.start), end=_parse_date(args.end))
    metrics = compute_metrics(trades)
    write_outputs(trades, metrics)
    _print_metrics("Full Backtest", metrics)
    print(f"\n  Trade log:    {TRADE_LOG}")
    print(f"  Equity curve: {EQUITY_CSV}")
    print(f"  Metrics:      {METRICS_JSON}")
    print("\n  Open dashboard.html in a browser to view results.")
    return 0


def cmd_walk_forward(args) -> int:
    candles = _load(args)
    if candles is None:
        return 1
    rows = walk_forward(candles)
    if not rows:
        print("Not enough data for walk-forward.")
        return 1
    print("\n=== Walk-Forward Validation ===")
    print(f"  {'Fold':<5}{'Period':<26}{'Trades':>7}{'WR%':>7}{'PF':>7}{'TotR':>8}{'DD(R)':>8}")
    print("  " + "-" * 66)
    for r in rows:
        period = f"{r['start']} -> {r['end']}"
        print(f"  {r['fold']:<5}{period:<26}{r['trades']:>7}{r['win_rate_pct']:>7}"
              f"{r['profit_factor']:>7}{r['total_r']:>8}{r['max_drawdown_r']:>8}")
    print(f"\n  Summary: {FOLD_CSV}")
    return 0


def cmd_import_data(args) -> int:
    """
    Normalize one or more source CSVs (Dukascopy epoch-ms, or MT5/string time)
    into the single master history file used for backtests. Merges, dedupes by
    timestamp, and sorts. Existing master data is preserved and merged in.

    Usage:
      python run.py import-data --in download/usatechidxusd-m5-bid-2022*.csv --source-tz UTC
      python run.py import-data --in fileA.csv --in fileB.csv --source-tz UTC
    """
    import glob as _glob

    if not args.inputs:
        print("import-data requires --in PATH (one or more; globs allowed).\n"
              "Example:\n"
              "  python run.py import-data --in \"download/usatechidxusd-m5-*.csv\" --source-tz UTC")
        return 1

    out = Path(args.data) if args.data else DEFAULT_DATA
    out.parent.mkdir(parents=True, exist_ok=True)

    # Collect all candles keyed by their NY timestamp (dedupe = last wins).
    merged: dict[str, Candle] = {}

    # Seed with existing master file if present (so imports accumulate).
    # The master is always stored in NY-local time, so read it as such
    # regardless of --source-tz (which applies to the NEW inputs).
    if out.exists():
        try:
            for c in load_candles(out, source_tz="America/New_York"):
                merged[c.time.isoformat()] = c
            print(f"  Existing master: {len(merged):,} candles loaded from {out.name}")
        except Exception as e:
            print(f"  (could not read existing master, starting fresh: {e})")

    # Expand globs and load each input.
    files: list[str] = []
    for pattern in args.inputs:
        hits = _glob.glob(pattern)
        if not hits:
            print(f"  WARNING: no files match {pattern}")
        files.extend(sorted(hits))

    if not files:
        print("No input files found. Nothing imported.")
        return 1

    for f in files:
        before = len(merged)
        try:
            cands = load_candles(f, source_tz=args.source_tz)
        except Exception as e:
            print(f"  ERROR reading {f}: {e}")
            continue
        for c in cands:
            merged[c.time.isoformat()] = c
        added = len(merged) - before
        print(f"  + {Path(f).name}: {len(cands):,} rows ({added:,} new)")

    if not merged:
        print("No candles after import. Check file formats.")
        return 1

    # Write the master file sorted by time, in the canonical 'time,o,h,l,c' format.
    all_candles = sorted(merged.values(), key=lambda c: c.time)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close"])
        for c in all_candles:
            # store NY-local naive string; reload with --source-tz America/New_York
            w.writerow([c.time.strftime("%Y-%m-%d %H:%M:%S"),
                        c.open, c.high, c.low, c.close])

    print(f"\n  Master history written: {out}")
    print(f"  Total candles: {len(all_candles):,} "
          f"({all_candles[0].time.date()} -> {all_candles[-1].time.date()})")
    print(f"\n  Backtest a year with, e.g.:")
    print(f"  python run.py backtest --start 2022-01-01 --end 2022-12-31")
    print(f"  (master is the default --data; it is stored in NY time, so no --source-tz needed)")
    return 0


def cmd_all(args) -> int:
    if not (Path(args.data) if args.data else DEFAULT_DATA).exists():
        cmd_gen_data(args)
    rc = cmd_backtest(args)
    if rc == 0:
        cmd_walk_forward(args)
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="NQ Modified ICT 2022 backtester")
    p.add_argument("command", nargs="?", default="all",
                   choices=["gen-data", "backtest", "walk-forward", "all", "config",
                            "pull-data", "import-data"])
    p.add_argument("--data", default=None)
    p.add_argument("--source-tz", default="America/New_York")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--in", dest="inputs", action="append", default=None,
                   help="Source CSV(s) to import into the master file (globs allowed; repeatable)")
    # pull-data options
    p.add_argument("--symbol", default=None, help="MT5 Nasdaq symbol (e.g. NAS100)")
    p.add_argument("--assume-offset", type=int, default=None,
                   help="Override broker server offset (hours) for pull-data")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return {
        "gen-data": cmd_gen_data,
        "backtest": cmd_backtest,
        "walk-forward": cmd_walk_forward,
        "all": cmd_all,
        "config": cmd_config,
        "pull-data": cmd_pull_data,
        "import-data": cmd_import_data,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())