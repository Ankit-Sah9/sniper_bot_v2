"""
generate_sample_data.py — Synthetic 5-minute NQ history generator.

Produces a realistic-enough 5-min dataset so the backtest + dashboard run
end-to-end immediately. THIS IS SYNTHETIC DATA — not real NQ prices. Replace
data/history/NQ_5min.csv with your real broker export for meaningful results.

Design goals:
  - Plausible intraday structure (Asian range, London move, NY AM volatility).
  - Deliberately seeds some sweep -> reversal -> displacement (FVG) sequences
    in the 08:30-11:00 window so the engine has setups to find.
  - Full 24h of 5-min bars per weekday across the requested date range.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "history" / "NQ_5min.csv"


def _gen_day(day: datetime, prev_close: float, rng: random.Random,
             prev_day_high: float, prev_day_low: float) -> list[list]:
    """Generate one weekday of 5-min bars (288 bars, 00:00 -> 23:55 NY).

    On most days, deliberately construct a clean setup in the 08:30-10:00 window:
      sweep a prior level -> reverse -> displace (leaving an FVG) -> retrace.
    The day is made self-consistent so bias (price vs midnight open) agrees with
    the trade direction by construction.
    """
    rows = []

    make_setup = rng.random() < 0.85
    setup_long = rng.random() < 0.5
    setup_bar = rng.choice([102, 108, 114])     # 08:30, 09:00, 09:30

    # Target level to sweep: prev day low (long) or prev day high (short).
    target_level = prev_day_low if setup_long else prev_day_high

    # Midnight open: place it so that AFTER the post-sweep displacement, price is
    # clearly on the bias-correct side. For a long we want price to end up ABOVE
    # the midnight open; the sweep dips below target_level then displaces up. So
    # set midnight open a little below target_level for longs (and above for
    # shorts), guaranteeing the displaced price clears the +/- buffer.
    if make_setup:
        if setup_long:
            midnight_open = target_level - rng.uniform(20, 40)
        else:
            midnight_open = target_level + rng.uniform(20, 40)
    else:
        midnight_open = prev_close + rng.uniform(-15, 15)

    def vol_for(hour: int) -> float:
        if 20 <= hour or hour < 2:
            return 2.5
        if 2 <= hour < 5:
            return 5.0
        if 8 <= hour < 11:
            return 8.0
        if 11 <= hour < 16:
            return 5.0
        return 3.5

    t = day.replace(hour=0, minute=0, second=0, microsecond=0)
    price = midnight_open

    step = 0
    while step < 288:
        hour = t.hour
        vol = vol_for(hour)
        # On setup days, keep the early-morning quiet so incidental sweeps don't
        # mask the constructed sequence (the engine takes the most recent sweep).
        if make_setup and 96 <= step < setup_bar - 6:   # 08:00 .. just before approach
            vol = min(vol, 2.0)
        o = price

        # Smoothly steer price toward the target level in the bars just before
        # the setup, so the constructed sweep connects without a gap.
        if make_setup and setup_bar - 6 <= step < setup_bar:
            approach_to = target_level + (10 if setup_long else -10)
            o = price
            c = o + (approach_to - o) * 0.4 + rng.gauss(0, 1.5)
            hi = max(o, c) + abs(rng.gauss(0, 1.0))
            lo = min(o, c) - abs(rng.gauss(0, 1.0))
            rows.append([t.strftime("%Y-%m-%d %H:%M:%S"),
                         round(o, 2), round(hi, 2), round(lo, 2), round(c, 2)])
            price = c
            t += timedelta(minutes=5)
            step += 1
            continue

        if make_setup and step == setup_bar:
            seq = _build_setup_sequence(target_level, setup_long, rng)
            for (so, sh, sl, sc) in seq:
                rows.append([t.strftime("%Y-%m-%d %H:%M:%S"),
                             round(so, 2), round(sh, 2), round(sl, 2), round(sc, 2)])
                price = sc
                t += timedelta(minutes=5)
                step += 1
            continue

        # Default random-walk bar, with mild mean-reversion to keep price sane.
        move = rng.gauss(0, vol)
        c = o + move
        hi = max(o, c) + abs(rng.gauss(0, vol * 0.4))
        lo = min(o, c) - abs(rng.gauss(0, vol * 0.4))
        rows.append([t.strftime("%Y-%m-%d %H:%M:%S"),
                     round(o, 2), round(hi, 2), round(lo, 2), round(c, 2)])
        price = c
        t += timedelta(minutes=5)
        step += 1

    return rows


def _build_setup_sequence(level: float, is_long: bool,
                          rng: random.Random) -> list[tuple]:
    """
    Build ~9 bars forming: approach -> sweep (>$3 through level) -> reversal ->
    displacement leaving an FVG -> partial retrace into the FVG.
    Anchored at `level` so the sweep is always reachable.
    Returns list of (open, high, low, close).
    """
    bars = []

    if is_long:
        # Approach from just above the level, heading down toward it.
        p = level + rng.uniform(12, 20)
        for _ in range(2):
            o = p; c = p - rng.uniform(3, 6)
            bars.append((o, o + 1, c - 1, c)); p = c
        # Sweep low: >$3 below the level, then close back above it.
        o = p
        sweep_low = level - rng.uniform(5, 10)
        c = level + rng.uniform(1, 4)
        bars.append((o, o + 1, sweep_low, c)); p = c
        # Reversal candle (establishes a swing-low context to break).
        o = p; c = p + rng.uniform(1, 3)
        bars.append((o, c + 1, o - 2, c)); p = c
        # Displacement up (big bullish candle -> drives the MSS).
        o = p; c = p + rng.uniform(22, 35)
        bars.append((o, c + 2, o - 1, c)); p = c
        # Continuation up creating the bullish FVG: bar6.low > bar4.high.
        bar4_high = bars[3][1]
        o = p
        gap_low = bar4_high + rng.uniform(6, 12)
        c = p + rng.uniform(15, 25)
        bars.append((o, c + 2, gap_low, c)); p = c
        # Retrace down into the FVG near edge (top), then resume up.
        fvg_top = gap_low
        o = p; c = fvg_top + rng.uniform(0, 2)
        bars.append((o, o + 1, fvg_top - 1, c)); p = c
        o = p; c = p + rng.uniform(10, 16)
        bars.append((o, c + 1, o - 2, c)); p = c
        o = p; c = p + rng.uniform(10, 16)
        bars.append((o, c + 1, o - 1, c)); p = c
    else:
        # Mirror for shorts.
        p = level - rng.uniform(12, 20)
        for _ in range(2):
            o = p; c = p + rng.uniform(3, 6)
            bars.append((o, c + 1, o - 1, c)); p = c
        o = p
        sweep_high = level + rng.uniform(5, 10)
        c = level - rng.uniform(1, 4)
        bars.append((o, sweep_high, c - 1, c)); p = c
        o = p; c = p - rng.uniform(1, 3)
        bars.append((o, o + 2, c - 1, c)); p = c
        o = p; c = p - rng.uniform(22, 35)
        bars.append((o, o + 1, c - 2, c)); p = c
        bar4_low = bars[3][2]
        o = p
        gap_high = bar4_low - rng.uniform(6, 12)
        c = p - rng.uniform(15, 25)
        bars.append((o, gap_high, c - 2, c)); p = c
        fvg_bottom = gap_high
        o = p; c = fvg_bottom - rng.uniform(0, 2)
        bars.append((o, fvg_bottom + 1, o - 1, c)); p = c
        o = p; c = p - rng.uniform(10, 16)
        bars.append((o, o + 2, c - 1, c)); p = c
        o = p; c = p - rng.uniform(10, 16)
        bars.append((o, o + 1, c - 1, c)); p = c

    return bars


def generate(start: str = "2024-01-01", end: str = "2024-06-30",
             seed: int = 42, start_price: float = 16800.0) -> Path:
    rng = random.Random(seed)
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    all_rows: list[list] = []
    prev_close = start_price
    prev_day_high = start_price + 50
    prev_day_low = start_price - 50
    day = start_dt
    while day <= end_dt:
        if day.weekday() < 5:               # Mon-Fri only
            day_rows = _gen_day(day, prev_close, rng, prev_day_high, prev_day_low)
            all_rows.extend(day_rows)
            prev_close = day_rows[-1][4]
            prev_day_high = max(r[2] for r in day_rows)
            prev_day_low = min(r[3] for r in day_rows)
        day += timedelta(days=1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close"])
        w.writerows(all_rows)

    print(f"Wrote {len(all_rows):,} synthetic 5-min bars to {OUT}")
    print(f"Range: {start} -> {end} (weekdays). SYNTHETIC DATA — replace for real results.")
    return OUT


if __name__ == "__main__":
    generate()
