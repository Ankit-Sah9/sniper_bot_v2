"""
data_loader.py — Load 5-minute NQ history into Candle objects.

Expected CSV format (header required):
    time,open,high,low,close
    2024-01-02 00:00:00,16800.25,16805.50,16798.00,16803.75
    ...

`time` may be either:
  - NY local time already (set source_tz="America/New_York"), or
  - UTC (set source_tz="UTC") -> converted to NY with DST handling.

All Candle.time values returned are timezone-aware America/New_York datetimes,
so every downstream session/window/bias check is DST-correct.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import NY_TZ
from .models import Candle

NY = ZoneInfo(NY_TZ)
UTC = ZoneInfo("UTC")


def _parse_dt(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # ISO fallback
    return datetime.fromisoformat(value)


# Column-name aliases: the time column may be called "time" or "timestamp".
_TIME_KEYS = ("time", "timestamp")


def _row_time_to_ny(row: dict, src: ZoneInfo) -> datetime:
    """
    Return an NY-aware datetime for a row, handling two source formats:
      - Unix-millisecond epoch (e.g. Dukascopy "timestamp" = 1641164400000):
        an absolute UTC instant -> convert straight to NY (source_tz ignored).
      - Date/time string (e.g. MT5 "time" = "2022-01-02 23:05:00"): a naive
        local time in `src` -> attach src, then convert to NY.
    """
    val = None
    key = None
    for k in _TIME_KEYS:
        if k in row and row[k] not in (None, ""):
            val = row[k]
            key = k
            break
    if val is None:
        raise KeyError("time")

    s = str(val).strip()
    # Pure integer (optionally with a trailing .0) -> epoch milliseconds (UTC).
    if s.replace(".", "", 1).isdigit() and key == "timestamp":
        ms = float(s)
        # Heuristic: 13-digit values are ms, 10-digit are seconds.
        secs = ms / 1000.0 if ms > 1e11 else ms
        aware_utc = datetime.fromtimestamp(secs, tz=ZoneInfo("UTC"))
        return aware_utc.astimezone(NY)

    # Otherwise: naive local string in source_tz.
    raw = _parse_dt(s)
    aware = raw.replace(tzinfo=src)
    return aware.astimezone(NY)


def load_candles(csv_path: str | Path, source_tz: str = "America/New_York") -> list[Candle]:
    """
    Load and return candles sorted by time, as NY-aware datetimes.

    source_tz: timezone the CSV timestamps are in ("America/New_York" or "UTC").
               (Ignored for Unix-epoch timestamps, which are absolute UTC.)
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"History file not found: {path}")

    src = ZoneInfo(source_tz)
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                ny_time = _row_time_to_ny(row, src)
                candles.append(Candle(
                    time=ny_time,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                ))
            except (KeyError, ValueError, TypeError):
                continue

    candles.sort(key=lambda c: c.time)
    return candles