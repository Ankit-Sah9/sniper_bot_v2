"""
mt5_export.py — Pull 5-minute bars from a running MetaTrader 5 terminal.

Attaches to an ALREADY-RUNNING, logged-in MT5 terminal (no credentials needed),
pulls M5 bars for a symbol over an explicit date range, auto-detects the broker's
server-time offset, converts timestamps to New York time, and writes a CSV in the
exact format the backtest engine expects:

    time,open,high,low,close
    2024-01-02 00:00:00,16800.25,16805.50,16798.00,16803.75

-------------------------------------------------------------------------------
IMPORTANT — read before trusting results
-------------------------------------------------------------------------------
* Requires Windows + the `MetaTrader5` Python package (`pip install MetaTrader5`)
  + an open, logged-in MT5 terminal. It CANNOT run on Linux/Mac.
* MT5 does NOT expose the broker's timezone via API. We infer the offset by
  comparing the latest tick's server time to UTC and rounding to the nearest
  hour. This catches the CURRENT offset only. Many brokers shift +1h with
  European DST, so for long historical ranges that cross a DST boundary the
  detected offset may be slightly wrong on some dates. The tool PRINTS the
  detected offset and also writes a `time_broker` column so you can verify.
* The Nasdaq symbol name varies by broker: NAS100, USTEC, US100, NDX100, etc.
  Pass the exact name shown in your Market Watch via --symbol.
-------------------------------------------------------------------------------

Usage (run on your Windows machine, MT5 open):

    python -m engine.mt5_export --symbol NAS100 --start 2024-01-01 --end 2024-06-30
    python -m engine.mt5_export --symbol USTEC  --start 2023-01-01 --end 2024-12-31 --out data/history/NQ_5min.csv

Then backtest the NY-converted file directly (already in NY time):

    python run.py backtest --data data/history/NQ_5min.csv --source-tz America/New_York
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "history" / "NQ_5min.csv"
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _import_mt5():
    """Import MetaTrader5 with a friendly message if it's missing."""
    try:
        import MetaTrader5 as mt5  # type: ignore
        return mt5
    except ImportError:
        raise SystemExit(
            "The 'MetaTrader5' package is not installed (or you're not on Windows).\n"
            "Install it on a Windows machine with an MT5 terminal:\n"
            "    pip install MetaTrader5\n"
            "Then make sure MT5 is open and logged in before running this."
        )


def detect_server_offset_hours(mt5, symbol: str) -> int:
    """
    Infer the broker server-time offset from UTC, in whole hours.

    Method: take the most recent tick's `time` (server epoch seconds) and compare
    to the real current UTC time. Round the gap to the nearest hour.

    Returns an integer hour offset (e.g. +2 means server is UTC+2). Returns 0 if
    it cannot be determined (and warns), so timestamps fall back to "treat server
    time as UTC".
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not getattr(tick, "time", 0):
        print("  [warn] Could not read a tick to detect server offset; assuming UTC+0.")
        return 0
    server_dt = datetime.fromtimestamp(tick.time, tz=UTC)  # naive server epoch read as UTC
    now_utc = datetime.now(tz=UTC)
    gap_hours = (server_dt - now_utc).total_seconds() / 3600.0
    offset = int(round(gap_hours))
    print(f"  Detected broker server offset: UTC{offset:+d} "
          f"(server tick {server_dt:%Y-%m-%d %H:%M} vs UTC {now_utc:%Y-%m-%d %H:%M})")
    return offset


def server_to_ny(server_epoch: int, offset_hours: int) -> datetime:
    """
    Convert a server epoch timestamp to a NY-aware datetime.

    MT5 returns bar times as epoch seconds in SERVER time. We treat that epoch as
    'UTC + offset', recover true UTC by subtracting the offset, then convert to NY
    (which applies US DST correctly).
    """
    # The epoch MT5 gives is server-wall-clock interpreted as if UTC; subtract the
    # broker offset to get true UTC, then localize to NY.
    as_if_utc = datetime.fromtimestamp(server_epoch, tz=UTC)
    true_utc = as_if_utc - timedelta(hours=offset_hours)
    return true_utc.astimezone(NY)


def export(symbol: str, start: str, end: str, out: Path,
           assume_offset: int | None = None) -> Path:
    mt5 = _import_mt5()

    print(f"Connecting to running MT5 terminal…")
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize() failed: {mt5.last_error()}\n"
                         f"Is the terminal open and logged in?")

    try:
        # Ensure the symbol is available / selected in Market Watch.
        info = mt5.symbol_info(symbol)
        if info is None:
            available = [s.name for s in (mt5.symbols_get() or [])][:40]
            raise SystemExit(
                f"Symbol '{symbol}' not found. Check the exact name in Market Watch.\n"
                f"Some available symbols: {', '.join(available)}")
        if not info.visible:
            mt5.symbol_select(symbol, True)

        offset = assume_offset if assume_offset is not None \
            else detect_server_offset_hours(mt5, symbol)

        # Build server-time bounds. We pass naive datetimes to copy_rates_range;
        # MT5 interprets them in server time, so we shift our NY-intended bounds
        # by the offset to capture the right window (with a 1-day pad each side).
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
        # Pad so we don't clip edge days after tz conversion.
        start_server = start_dt - timedelta(days=1)
        end_server = end_dt + timedelta(days=1)

        print(f"Pulling {symbol} M5 bars {start} → {end} …")
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_server, end_server)
        if rates is None or len(rates) == 0:
            raise SystemExit(f"No bars returned for {symbol}. "
                             f"Try loading more history on an M5 chart first, "
                             f"or widen the date range.")

        # Convert + filter to the requested NY date range.
        ny_start = start_dt.date()
        ny_end = datetime.strptime(end, "%Y-%m-%d").date()
        rows = []
        for r in rates:
            ny_time = server_to_ny(int(r["time"]), offset)
            if not (ny_start <= ny_time.date() <= ny_end):
                continue
            broker_time = datetime.fromtimestamp(int(r["time"]), tz=UTC)
            rows.append([
                ny_time.strftime("%Y-%m-%d %H:%M:%S"),
                round(float(r["open"]), 2),
                round(float(r["high"]), 2),
                round(float(r["low"]), 2),
                round(float(r["close"]), 2),
                broker_time.strftime("%Y-%m-%d %H:%M:%S"),  # audit column
            ])

        rows.sort(key=lambda x: x[0])

        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "open", "high", "low", "close", "time_broker"])
            w.writerows(rows)

        print(f"\nWrote {len(rows):,} M5 bars to {out}")
        print(f"  NY range: {rows[0][0]} → {rows[-1][0]}")
        print(f"  Timezone: converted to America/New_York (server offset UTC{offset:+d}).")
        print(f"  Audit:    'time_broker' column kept for verification — the engine "
              f"ignores extra columns.")
        print(f"\nVERIFY the offset looks right (check a known session against the "
              f"chart). If it's off, re-run with --assume-offset N.")
        print(f"\nBacktest it with:")
        print(f"    python run.py backtest --data {out} --source-tz America/New_York")
        return out

    finally:
        mt5.shutdown()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export M5 bars from a running MT5 terminal to the engine's CSV format.")
    p.add_argument("--symbol", required=True,
                   help="Exact MT5 symbol for the Nasdaq (e.g. NAS100, USTEC, US100).")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD (NY date).")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (NY date).")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"Output CSV path (default: {DEFAULT_OUT}).")
    p.add_argument("--assume-offset", type=int, default=None,
                   help="Override auto-detection: broker server offset in hours "
                        "(e.g. 2 for UTC+2). Use if auto-detect looks wrong.")
    return p


def main() -> int:
    args = build_parser().parse_args()
    export(args.symbol, args.start, args.end, Path(args.out), args.assume_offset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
