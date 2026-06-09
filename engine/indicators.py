"""
indicators.py — Strategy primitives for the Modified ICT 2022 Model.

Contains the mechanical building blocks, each independently testable:
  - fractal swing detection (Williams, N each side)
  - FVG detection (3-candle imbalance)
  - liquidity level construction (prev day/week H/L, session H/L)
  - sweep detection ($ penetration through a level)

No session/window/bias logic here — that lives in strategy.py.
"""

from __future__ import annotations

from datetime import datetime, date

from .config import CONFIG
from .models import Candle, SwingPoint, FVG, LiquidityLevel


# ── Fractal swings ───────────────────────────────────────────────────────

def atr_at(candles: list[Candle], index: int, period: int | None = None) -> float:
    """
    Average True Range over the `period` candles ending at (and including)
    `index`. Uses only candles up to `index` (no look-ahead). Returns 0.0 if
    there isn't enough history.

    True range = max(high-low, |high-prev_close|, |low-prev_close|).
    """
    p = period if period is not None else CONFIG.atr_period
    if index < p:
        # Not enough history; fall back to whatever range is available.
        start = 1
    else:
        start = index - p + 1
    trs = []
    for j in range(start, index + 1):
        c = candles[j]
        prev_close = candles[j - 1].close if j > 0 else c.open
        tr = max(c.high - c.low,
                 abs(c.high - prev_close),
                 abs(c.low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def detect_fractals(candles: list[Candle], n: int | None = None) -> list[SwingPoint]:
    """
    Williams-style fractal swings.

    A swing HIGH at index i: candles[i].high is strictly greater than the highs
    of the n candles on each side. Swing LOW is the mirror with lows.

    Returns swings in chronological order. A fractal at index i can only be
    *confirmed* once n candles to its right exist, so the latest n candles
    never produce swings (correct for non-repainting live use).
    """
    if n is None:
        n = CONFIG.fractal_n_entry
    swings: list[SwingPoint] = []
    for i in range(n, len(candles) - n):
        c = candles[i]
        left = candles[i - n:i]
        right = candles[i + 1:i + 1 + n]

        is_high = all(c.high > x.high for x in left) and all(c.high > x.high for x in right)
        if is_high:
            swings.append(SwingPoint(index=i, time=c.time, price=c.high, kind="HIGH"))
            continue

        is_low = all(c.low < x.low for x in left) and all(c.low < x.low for x in right)
        if is_low:
            swings.append(SwingPoint(index=i, time=c.time, price=c.low, kind="LOW"))
    return swings


def confirmed_swings_as_of(candles: list[Candle], upto_index: int,
                           n: int | None = None,
                           from_index: int = 0) -> list[SwingPoint]:
    """
    Swings that are fully confirmed using only data up to and including
    `upto_index`. Prevents look-ahead: a swing at index i is only returned
    if i + n <= upto_index.

    `from_index` bounds the search window for performance (e.g. start of the
    current trading day). Swings before from_index are not returned. Fractal
    detection still uses the n candles to the left of from_index for context.
    """
    if n is None:
        n = CONFIG.fractal_n_entry
    # Include n candles of left context so a fractal at from_index is detectable.
    lo = max(0, from_index - n)
    visible = candles[lo:upto_index + 1]
    offset = lo
    out: list[SwingPoint] = []
    for s in detect_fractals(visible, n):
        real_index = s.index + offset
        if real_index < from_index:
            continue
        if real_index + n <= upto_index:
            out.append(SwingPoint(index=real_index, time=s.time,
                                  price=s.price, kind=s.kind))
    return out


# ── Fair Value Gaps ──────────────────────────────────────────────────────

def detect_fvgs_in_range(candles: list[Candle], start_idx: int, end_idx: int,
                         direction: str | None = None) -> list[FVG]:
    """
    Detect 3-candle FVGs whose middle candle (c2) lies within [start_idx, end_idx].

    Bullish FVG: candle1.high < candle3.low  -> gap [c1.high, c3.low]
    Bearish FVG: candle1.low  > candle3.high -> gap [c3.high, c1.low]

    `direction` (optional) filters to only "BULLISH" or "BEARISH" gaps.
    """
    fvgs: list[FVG] = []
    lo = max(1, start_idx)
    hi = min(len(candles) - 2, end_idx)
    for i in range(lo, hi + 1):          # i = middle candle index
        c1, c2, c3 = candles[i - 1], candles[i], candles[i + 1]

        # Bullish gap
        if c1.high < c3.low:
            fvg = FVG(direction="BULLISH", top=c3.low, bottom=c1.high,
                      c1_index=i - 1, c3_index=i + 1, formed_time=c3.time)
            if (direction in (None, "BULLISH")) and fvg.size >= CONFIG.min_fvg_size:
                fvgs.append(fvg)

        # Bearish gap
        if c1.low > c3.high:
            fvg = FVG(direction="BEARISH", top=c1.low, bottom=c3.high,
                      c1_index=i - 1, c3_index=i + 1, formed_time=c3.time)
            if (direction in (None, "BEARISH")) and fvg.size >= CONFIG.min_fvg_size:
                fvgs.append(fvg)
    return fvgs


# ── Liquidity levels ─────────────────────────────────────────────────────

def _session_high_low(candles: list[Candle], day: date,
                      start_hm: str, end_hm: str) -> tuple[float, float] | None:
    """
    High/low of candles falling in [start, end) ET on the given day.
    Handles sessions that cross midnight (start > end) by spanning into next day.
    """
    sh, sm = map(int, start_hm.split(":"))
    eh, em = map(int, end_hm.split(":"))
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em
    crosses_midnight = end_minutes <= start_minutes

    highs, lows = [], []
    for c in candles:
        cm = c.time.hour * 60 + c.time.minute
        if crosses_midnight:
            in_session = (c.time.date() == day and cm >= start_minutes) or \
                         (c.time.date() == _next_day(day) and cm < end_minutes)
        else:
            in_session = c.time.date() == day and start_minutes <= cm < end_minutes
        if in_session:
            highs.append(c.high)
            lows.append(c.low)
    if not highs:
        return None
    return max(highs), min(lows)


def _next_day(d: date) -> date:
    from datetime import timedelta
    return d + timedelta(days=1)


def build_liquidity_levels(candles: list[Candle], as_of_index: int) -> list[LiquidityLevel]:
    """
    Build the set of live liquidity levels visible at `as_of_index`:
      - Previous day high/low
      - Previous week high/low
      - Prior session highs/lows (Asian, London, prior NY)

    Only uses fully-closed prior periods (no look-ahead into the current day).
    """
    if as_of_index < 1:
        return []
    current = candles[as_of_index]
    today = current.time.date()
    levels: list[LiquidityLevel] = []

    # Only the previous day, previous week, and prior sessions are needed — all
    # within ~10 calendar days. Bound the lookback so this is O(window), not
    # O(year-to-date). ~10 days * ~288 5-min bars (24h) ~= 2,880; use 4,000 for
    # safety around weekends/gaps/extended data.
    lo_idx = max(0, as_of_index + 1 - 4000)
    prior = candles[lo_idx:as_of_index + 1]

    # ---- Previous day H/L ----
    prev_day_candles_by_date: dict[date, list[Candle]] = {}
    for c in prior:
        if c.time.date() < today:
            prev_day_candles_by_date.setdefault(c.time.date(), []).append(c)
    if prev_day_candles_by_date:
        last_day = max(prev_day_candles_by_date)
        day_candles = prev_day_candles_by_date[last_day]
        levels.append(LiquidityLevel(max(x.high for x in day_candles), "HIGH",
                                     "PREV_DAY", day_candles[-1].time))
        levels.append(LiquidityLevel(min(x.low for x in day_candles), "LOW",
                                     "PREV_DAY", day_candles[-1].time))

    # ---- Previous week H/L (ISO week before current) ----
    cur_year, cur_week, _ = current.time.isocalendar()
    week_candles: list[Candle] = []
    for c in prior:
        y, w, _ = c.time.isocalendar()
        if (y, w) < (cur_year, cur_week):
            week_candles.append((y, w, c))
    if week_candles:
        last_y, last_w = max((y, w) for y, w, _ in week_candles)
        last_week = [c for y, w, c in week_candles if (y, w) == (last_y, last_w)]
        levels.append(LiquidityLevel(max(x.high for x in last_week), "HIGH",
                                     "PREV_WEEK", last_week[-1].time))
        levels.append(LiquidityLevel(min(x.low for x in last_week), "LOW",
                                     "PREV_WEEK", last_week[-1].time))

    # ---- Prior session highs/lows ----
    # Asian session typically belongs to the prior calendar evening; we look at
    # today's Asian (started previous evening) plus today's London.
    for source, (start_hm, end_hm) in (
        ("ASIAN", CONFIG.session_asian),
        ("LONDON", CONFIG.session_london),
    ):
        # Asian crosses midnight: anchor on previous day.
        anchor = _prev_day(today) if source == "ASIAN" else today
        hl = _session_high_low(prior, anchor, start_hm, end_hm)
        if hl:
            hi, lo = hl
            levels.append(LiquidityLevel(hi, "HIGH", source, current.time))
            levels.append(LiquidityLevel(lo, "LOW", source, current.time))

    return levels


def _prev_day(d: date) -> date:
    from datetime import timedelta
    return d - timedelta(days=1)


# ── Sweep detection ──────────────────────────────────────────────────────

def candle_sweeps_level(candle: Candle, level: LiquidityLevel,
                        penetration: float | None = None) -> bool:
    """
    True if this candle takes out the level by >= penetration ($).
    HIGH level: candle.high must exceed level.price + penetration.
    LOW level:  candle.low must drop below level.price - penetration.
    """
    if penetration is None:
        penetration = CONFIG.sweep_penetration
    if level.kind == "HIGH":
        return candle.high >= level.price + penetration
    return candle.low <= level.price - penetration