"""
htf_bias.py — Higher-timeframe (4H) market-structure bias.

Builds 4H candles from the 5-minute series (NY-anchored: bars start at
00:00, 04:00, 08:00, 12:00, 16:00, 20:00 ET), marks fractal swings on them,
and tracks the ICT-style trend via break of structure (BOS):

  - bullish  = last confirmed BOS was a close beyond a prior swing HIGH
  - bearish  = last confirmed BOS was a close beyond a prior swing LOW

The bias is "always one side" (no neutral): it is whatever the most recent
confirmed BOS direction is. Evaluated per trading day using only 4H candles
that CLOSED before that day's session start (no look-ahead).
"""

from __future__ import annotations

import bisect
from datetime import datetime, date, timedelta

from .config import CONFIG
from .models import Candle
from .indicators import detect_fractals


# 4H bar boundaries anchored to NY midnight.
_BLOCK_HOURS = (0, 4, 8, 12, 16, 20)


def _block_start(t: datetime) -> datetime:
    """Return the 4H block-start datetime that candle time t belongs to."""
    h = max(b for b in _BLOCK_HOURS if b <= t.hour)
    return t.replace(hour=h, minute=0, second=0, microsecond=0)


def resample_4h(candles: list[Candle]) -> list[Candle]:
    """
    Aggregate 5-min candles into 4H candles (NY-anchored). Each 4H candle's
    `time` is its block-start; open=first, high=max, low=min, close=last.
    Only blocks with at least one 5-min candle are emitted, in time order.
    """
    if not candles:
        return []
    buckets: dict[datetime, list[Candle]] = {}
    for c in candles:
        buckets.setdefault(_block_start(c.time), []).append(c)

    out: list[Candle] = []
    for start in sorted(buckets):
        grp = buckets[start]
        out.append(Candle(
            time=start,
            open=grp[0].open,
            high=max(x.high for x in grp),
            low=min(x.low for x in grp),
            close=grp[-1].close,
        ))
    return out


def _bos_timeline(h4: list[Candle], n: int, close_based: bool) -> list[tuple[datetime, str]]:
    """
    Walk the 4H candles and produce a timeline of confirmed BOS events:
    list of (confirm_time, direction) where direction is "BULLISH"/"BEARISH".

    A BOS is confirmed when a 4H candle breaks the most recent confirmed swing:
      - breaks above the last swing HIGH -> BULLISH
      - breaks below the last swing LOW  -> BEARISH
    "Break" = close beyond/at the swing level (close_based) or wick beyond it.
    Swings are confirmed fractals (need n candles to the right to exist), so a
    swing at index k is only usable from candle k+n onward (no look-ahead).
    """
    swings = detect_fractals(h4, n)
    # Index swings by the candle index at which they become CONFIRMED (k + n).
    confirmed_at: dict[int, list] = {}
    for s in swings:
        confirmed_at.setdefault(s.index + n, []).append(s)

    last_high = None   # most recent confirmed swing-high price
    last_low = None
    events: list[tuple[datetime, str]] = []
    direction = None

    for idx in range(len(h4)):
        # Register any swings that become confirmed as of this candle.
        for s in confirmed_at.get(idx, []):
            if s.kind == "HIGH":
                last_high = s.price
            else:
                last_low = s.price

        c = h4[idx]
        ref_up = c.close if close_based else c.high
        ref_dn = c.close if close_based else c.low

        # Check for BOS against the most recent confirmed swings.
        if last_high is not None and ref_up >= last_high and direction != "BULLISH":
            direction = "BULLISH"
            events.append((c.time, direction))
            # After a bullish BOS, the broken high is consumed.
            last_high = None
        elif last_low is not None and ref_dn <= last_low and direction != "BEARISH":
            direction = "BEARISH"
            events.append((c.time, direction))
            last_low = None

    return events


def _hhll_timeline(h4: list[Candle], n: int, close_based: bool) -> list[tuple[datetime, str]]:
    """
    Structure (HH/HL) bias state machine. Produces a timeline of bias changes:
    list of (time, "BULLISH"|"BEARISH"|"NEUTRAL").

    Rules:
      - Track confirmed swings (N-fractal, no look-ahead). Keep the last 3 swing
        highs and last 3 swing lows seen so far.
      - From NEUTRAL: become BULLISH if the last 3 highs are strictly ascending
        AND the last 3 lows are strictly ascending (two higher highs + two higher
        lows); become BEARISH if both are strictly descending; else stay NEUTRAL.
      - While BULLISH: if a 4H candle closes below the most recent swing low
        (the latest higher-low) -> flip BEARISH immediately. A fresh ascending
        3/3 sequence re-affirms BULLISH.
      - While BEARISH: if a 4H candle closes above the most recent swing high
        (the latest lower-high) -> flip BULLISH immediately.
    """
    swings = detect_fractals(h4, n)
    confirmed_at: dict[int, list] = {}
    for s in swings:
        confirmed_at.setdefault(s.index + n, []).append(s)

    highs: list[float] = []   # confirmed swing-high prices, in order
    lows: list[float] = []
    direction = "NEUTRAL"
    events: list[tuple[datetime, str]] = [(h4[0].time, "NEUTRAL")] if h4 else []

    def asc(xs):  # strictly ascending last 3
        return len(xs) >= 3 and xs[-3] < xs[-2] < xs[-1]

    def desc(xs):
        return len(xs) >= 3 and xs[-3] > xs[-2] > xs[-1]

    for idx in range(len(h4)):
        for s in confirmed_at.get(idx, []):
            if s.kind == "HIGH":
                highs.append(s.price)
            else:
                lows.append(s.price)

        c = h4[idx]
        ref = c.close if close_based else (c.low if direction == "BULLISH" else c.high)
        new_dir = direction

        if direction == "BULLISH":
            # Flip on close below the most recent confirmed swing low (higher-low).
            if lows and (c.close if close_based else c.low) < lows[-1]:
                new_dir = "BEARISH"
        elif direction == "BEARISH":
            if highs and (c.close if close_based else c.high) > highs[-1]:
                new_dir = "BULLISH"

        # Establish / re-affirm from the staircase (works from NEUTRAL too).
        if new_dir == direction:  # no BOS flip this candle
            if asc(highs) and asc(lows):
                new_dir = "BULLISH"
            elif desc(highs) and desc(lows):
                new_dir = "BEARISH"
            # else: keep current (could be NEUTRAL)

        if new_dir != direction:
            direction = new_dir
            events.append((c.time, direction))

    return events


class FourHourBias:
    """
    Precomputes the 4H BOS timeline once, then answers `bias_for(day)` quickly.
    """

    def __init__(self, candles: list[Candle]):
        self.h4 = resample_4h(candles)
        if CONFIG.bias_method == "structure_hhll":
            self.events = _hhll_timeline(
                self.h4, CONFIG.fractal_n_bias, CONFIG.bias_4h_bos_close)
        else:
            self.events = _bos_timeline(
                self.h4, CONFIG.fractal_n_bias, CONFIG.bias_4h_bos_close)

        # Confirmed-swing timeline for the regime (chop) filter: each entry is
        # (confirm_time, kind, price). A swing at 4H index k is confirmed at k+n
        # (needs n candles to its right), and that candle closes 4h later.
        n = CONFIG.fractal_n_bias
        self._swings = []   # (confirm_close_time, kind, price), in time order
        for s in detect_fractals(self.h4, n):
            ci = s.index + n
            if ci < len(self.h4):
                confirm_close = self.h4[ci].time + timedelta(hours=4)
                self._swings.append((confirm_close, s.kind, s.price))
        self._swings.sort(key=lambda x: x[0])

        # --- Precompute for fast O(log n) lookups (avoids per-call rescans) ---
        # Separate, time-sorted high/low swing series.
        self._high_times = [t for (t, k, p) in self._swings if k == "HIGH"]
        self._high_prices = [p for (t, k, p) in self._swings if k == "HIGH"]
        self._low_times = [t for (t, k, p) in self._swings if k == "LOW"]
        self._low_prices = [p for (t, k, p) in self._swings if k == "LOW"]
        # BOS events: close times (confirm + 4h) and directions, time-sorted.
        self._event_close_times = [ct + timedelta(hours=4) for (ct, d) in self.events]
        self._event_dirs = [d for (ct, d) in self.events]

    def is_choppy_as_of(self, when: datetime) -> bool:
        """
        Regime (chop) filter. Returns True if 4H structure is in a contracting /
        overlapping range as of `when` -> "choppy, do not trade".

        Lenient definition (no tunable threshold): using the last 2 confirmed
        swing highs and last 2 confirmed swing lows available at `when`, the
        market is CHOPPY when it is making a LOWER high AND a HIGHER low — an
        inside / contracting range. Any clearly advancing structure is trending.
        Returns False if fewer than 2 highs and 2 lows are confirmed yet.
        """

        hi = bisect.bisect_right(self._high_times, when)   # # of highs with t <= when
        lo = bisect.bisect_right(self._low_times, when)
        if hi < 2 or lo < 2:
            return False
        lower_high = self._high_prices[hi - 1] < self._high_prices[hi - 2]
        higher_low = self._low_prices[lo - 1] > self._low_prices[lo - 2]
        return lower_high and higher_low

    def bias_as_of(self, when: datetime) -> str:
        """
        The confirmed bias as of `when`: the direction of the most recent BOS
        whose confirming 4H candle CLOSED at/before `when` (confirm_time + 4h
        <= when, no look-ahead). Returns "BULLISH"|"BEARISH"|"NEUTRAL".
        """

        idx = bisect.bisect_right(self._event_close_times, when)
        if idx == 0:
            return "NEUTRAL"
        return self._event_dirs[idx - 1]

    def _bias_as_of_legacy(self, when: datetime) -> str:
        """Original linear-scan version, kept only for correctness testing."""
        result = "NEUTRAL"
        for confirm_time, direction in self.events:
            close_time = confirm_time + timedelta(hours=4)
            if close_time <= when:
                result = direction
            else:
                break
        return result