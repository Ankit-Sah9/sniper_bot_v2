"""
strategy.py — The Modified ICT 2022 Model decision logic.

Implements the full trade chain, in order:

  Sweep (>= $3 through a liquidity level)
    -> Reversal (marks the sweep extreme)
    -> Fractal swing forms on the reversal side (N=2)
    -> MSS (a 5-min candle CLOSES beyond/at that swing, in bias direction)
    -> Displacement (the breaking leg must leave an FVG)
    -> FVG selection (first FVG in the discount/premium half via 50% fib)
    -> Entry (near edge of FVG, first touch, within 08:30-11:00 ET, bias-aligned)
    -> Stop ($5 beyond sweep extreme), Target (fixed 1:3 R:R)

The functions here only DETECT setups. Fill simulation / exits live in
backtest.py so the same setup logic can later be reused for live trading.
"""

from __future__ import annotations

from datetime import datetime, time as dtime

from .config import CONFIG
from .models import Candle, SwingPoint, FVG, LiquidityLevel, Setup
from .indicators import (
    confirmed_swings_as_of,
    detect_fvgs_in_range,
    build_liquidity_levels,
    candle_sweeps_level,
    atr_at,
)


# ── Time helpers ─────────────────────────────────────────────────────────

def _hm(s: str) -> dtime:
    h, m = map(int, s.split(":"))
    return dtime(h, m)


def in_entry_window(t: datetime) -> bool:
    """True if NY datetime t falls within [window_start, window_end)."""
    start = _hm(CONFIG.window_start)
    end = _hm(CONFIG.window_end)
    return start <= t.timetz().replace(tzinfo=None) < end


def midnight_open_for_day(candles: list[Candle], day_date) -> float | None:
    """
    The NY midnight open = open of the 00:00 ET 5-min candle for that date.
    Returns None if not present (e.g. first day / data gap).
    """
    for c in candles:
        if c.time.date() == day_date and c.time.hour == 0 and c.time.minute == 0:
            return c.open
    return None


# ── Bias (mechanical) ────────────────────────────────────────────────────

def evaluate_bias(price: float, midnight_open: float | None) -> str:
    """
    Continuous bias vs the NY midnight open with a +/- neutral buffer.

    Returns "BULLISH", "BEARISH", or "NEUTRAL".
      price > open + buffer  -> BULLISH (longs only)
      price < open - buffer  -> BEARISH (shorts only)
      within +/- buffer      -> NEUTRAL (no trade)
    """
    if midnight_open is None:
        return "NEUTRAL"
    buf = CONFIG.bias_neutral_buffer
    if price > midnight_open + buf:
        return "BULLISH"
    if price < midnight_open - buf:
        return "BEARISH"
    return "NEUTRAL"


# ── Setup detection ──────────────────────────────────────────────────────

# Per-day cache of liquidity levels (they don't change intraday).
_LIQ_CACHE: dict = {}


def _liquidity_for_day(candles: list[Candle], i: int, day_start_idx: int):
    """
    Liquidity levels for the current day, cached. Computed once using data
    available at the day's open (day_start_idx), so it's stable intraday and
    avoids per-candle recomputation.
    """
    today = candles[i].time.date()
    key = (id(candles), today)
    cached = _LIQ_CACHE.get(key)
    if cached is None:
        cached = build_liquidity_levels(candles, day_start_idx)
        _LIQ_CACHE[key] = cached
    return cached


def _choose_target(direction: str, entry_price: float, risk: float,
                   levels: list[LiquidityLevel]) -> float | None:
    """
    Decide the target price.

    target_mode == "liquidity":
        Target the NEAREST opposing liquidity pool that is at least
        min_reward_r * risk away (option c: skip pools that are too close, look
        further). For a LONG, opposing pools are HIGHs above entry; for a SHORT,
        LOWs below entry. If no qualifying pool exists, fall back to fixed RR.

    target_mode == "fixed_rr":
        Always entry +/- risk_reward * risk.
    """
    if CONFIG.target_mode == "fixed_rr":
        return (entry_price + CONFIG.risk_reward * risk if direction == "LONG"
                else entry_price - CONFIG.risk_reward * risk)

    # Liquidity mode.
    min_dist = CONFIG.min_reward_r * risk
    if direction == "LONG":
        # Opposing pools = highs above entry, clearing the 1R floor.
        candidates = [lv.price for lv in levels
                      if lv.kind == "HIGH" and lv.price >= entry_price + min_dist]
        if candidates:
            return min(candidates)            # nearest qualifying pool
    else:
        candidates = [lv.price for lv in levels
                      if lv.kind == "LOW" and lv.price <= entry_price - min_dist]
        if candidates:
            return max(candidates)            # nearest qualifying pool

    # Fallback: no qualifying opposing liquidity -> fixed RR.
    return (entry_price + CONFIG.risk_reward * risk if direction == "LONG"
            else entry_price - CONFIG.risk_reward * risk)


def find_setup_at(candles: list[Candle], i: int) -> Setup | None:
    """
    Attempt to confirm a setup where candle index `i` is the MSS candle
    (the candle that CLOSES beyond the reversal swing).

    Walks the chain backwards from the MSS candle:
      1. Determine which prior liquidity level was swept and the sweep extreme.
      2. Identify the reversal swing (the fractal between sweep and MSS).
      3. Confirm the MSS candle closes beyond/at that swing in a consistent dir.
      4. Confirm the breaking (displacement) leg leaves an FVG.
      5. Select the FVG (discount/premium half via 50% fib of displacement leg).
      6. Build entry/stop/target.

    Returns a Setup if every link holds, else None.

    Note: bias and entry-window checks are applied at FILL time in the backtest,
    not here, because bias is evaluated continuously at setup completion and the
    fill may occur on a later candle. We still require the MSS candle itself to
    sit on/after the session prep so setups are same-day. The backtest enforces
    one-trade-per-day and window-on-fill.
    """
    if i < CONFIG.fractal_n_entry + 3:
        return None

    mss = candles[i]
    today = mss.time.date()

    # Index of the first candle of today's session (bounds intraday searches).
    day_start_idx = i
    while day_start_idx > 0 and candles[day_start_idx - 1].time.date() == today:
        day_start_idx -= 1

    # --- 1. Find ALL sweeps prior to (and including) this candle, this day ---
    # Previously we only tried the most recent sweep; on real data every day has
    # multiple sweeps, and a non-recent one often forms the valid chain that THIS
    # candle confirms as MSS. So we collect all sweep candidates and try each;
    # the first that completes a valid chain (with candle i as the MSS) wins.
    # Because the backtest scans candles chronologically, the first candle i that
    # yields any setup is the EARLIEST MSS confirmation of the day (option b).
    levels = _liquidity_for_day(candles, i, day_start_idx)
    if not levels:
        return None

    sweep_candidates = []   # (sweep_idx, level)
    for j in range(i, day_start_idx - 1, -1):
        for lvl in levels:
            if candle_sweeps_level(candles[j], lvl):
                sweep_candidates.append((j, lvl))
    if not sweep_candidates:
        return None

    # Confirmed reversal swings depend only on (i, day_start_idx), NOT on the
    # sweep — so compute them ONCE here and reuse across all sweep candidates
    # (avoids recomputing fractals dozens of times per day).
    swings_today = confirmed_swings_as_of(candles, i - 1, from_index=day_start_idx)

    # Try each sweep candidate (most recent first) and return the first that
    # forms a complete, valid setup confirmed by candle i.
    for sweep_idx, swept_level in sweep_candidates:
        setup = _try_chain(candles, i, day_start_idx, sweep_idx, swept_level,
                           levels, swings_today)
        if setup is not None:
            return setup
    return None


def _try_chain(candles: list[Candle], i: int, day_start_idx: int,
               sweep_idx: int, swept_level: LiquidityLevel,
               levels: list[LiquidityLevel],
               swings: list[SwingPoint] | None = None) -> Setup | None:
    """
    Attempt the full chain for ONE sweep, with candle i as the candidate MSS.
    Returns a Setup if every link holds, else None.
    """
    mss = candles[i]
    sweep_side = swept_level.kind

    # Direction proposed by the sweep:
    #   swept a HIGH -> price ran up into buy-side liq -> look for SHORT
    #   swept a LOW  -> price ran down into sell-side liq -> look for LONG
    direction = "SHORT" if sweep_side == "HIGH" else "LONG"

    # --- 2. Sweep extreme = most extreme price between sweep and MSS ---
    span = candles[sweep_idx:i + 1]
    if direction == "SHORT":
        sweep_extreme = max(c.high for c in span)
        sweep_extreme_index = sweep_idx + max(
            range(len(span)), key=lambda k: span[k].high)
    else:
        sweep_extreme = min(c.low for c in span)
        sweep_extreme_index = sweep_idx + min(
            range(len(span)), key=lambda k: span[k].low)

    # --- 3. Reversal swing(s): confirmed fractals on the reversal side,
    #        formed AFTER the sweep extreme and BEFORE the MSS candle. ---
    if swings is None:  # fallback if called directly
        swings = confirmed_swings_as_of(candles, i - 1, from_index=day_start_idx)
    # For a SHORT we break a swing LOW (downward MSS); for LONG a swing HIGH.
    want_kind = "LOW" if direction == "SHORT" else "HIGH"
    candidate_swings = [
        s for s in swings
        if s.kind == want_kind and sweep_extreme_index <= s.index < i
    ]
    if not candidate_swings:
        return None

    # --- 3b. Confirm MSS: the MSS candle CLOSES beyond/at ANY qualifying
    #        reversal swing (Option 1). Among the swings the candle actually
    #        breaks, choose the one nearest the sweep extreme (earliest), since
    #        that represents the most significant structural break.
    if direction == "SHORT":
        broken = [s for s in candidate_swings if mss.close <= s.price]
    else:
        broken = [s for s in candidate_swings if mss.close >= s.price]
    if not broken:
        return None
    # Earliest qualifying swing = the structurally meaningful one.
    reversal_swing = min(broken, key=lambda s: s.index)

    # --- 4 & 5. Displacement leg + FVG ---
    # Displacement leg runs from the sweep extreme to the MSS candle.
    disp_start_idx = sweep_extreme_index
    disp_end_idx = i
    if direction == "SHORT":
        displacement_start = candles[disp_start_idx].high
        displacement_end = min(c.low for c in candles[disp_start_idx:disp_end_idx + 1])
        fvg_dir = "BEARISH"
    else:
        displacement_start = candles[disp_start_idx].low
        displacement_end = max(c.high for c in candles[disp_start_idx:disp_end_idx + 1])
        fvg_dir = "BULLISH"

    fvgs = detect_fvgs_in_range(candles, disp_start_idx, disp_end_idx, direction=fvg_dir)
    if not fvgs:
        return None  # no FVG in the displacement leg -> no valid displacement

    # Displacement-leg 50% (equilibrium) = midpoint of sweep extreme <-> MSS extreme.
    disp_50 = (displacement_start + displacement_end) / 2.0

    # disp_50_fvg entry gate. Entry requires price to be SIMULTANEOUSLY inside the
    # actual FVG gap AND at/beyond the 50% equilibrium of the displacement leg.
    #   LONG (gap = [bottom, top], retrace DOWN, near edge = top):
    #     - reject if the whole gap is above 50% (gap bottom > disp_50): price would
    #       leave the gap before reaching 50% -> the two conditions never coincide.
    #     - otherwise entry = the DEEPER of {near edge (top), 50% level} that is still
    #       inside the gap = min(top, disp_50). (Gap fully below 50% -> near edge;
    #       gap straddles 50% -> the 50% level.)
    #   SHORT: mirror.
    if CONFIG.entry_mode == "disp_50_fvg":
        if direction == "LONG":
            fvgs = [f for f in fvgs if f.bottom <= disp_50]   # gap reaches 50% or deeper
        else:
            fvgs = [f for f in fvgs if f.top >= disp_50]
        if not fvgs:
            return None  # no FVG overlaps the 50%-or-deeper zone -> no trade

    # Select the first FVG that price retraces into = the one nearest the
    # displacement end (closest to current price), since price retraces from the
    # end back toward it.
    if direction == "LONG":
        # Price is high after up-move; retraces down -> first touched = highest near edge.
        selected = max(fvgs, key=lambda f: f.near_edge)
    else:
        # Price is low after down-move; retraces up -> first touched = lowest near edge.
        selected = min(fvgs, key=lambda f: f.near_edge)

    # --- 6. Entry / Stop / Target ---
    # Entry depth into the FVG.
    if CONFIG.entry_mode == "disp_50_fvg":
        # Enter at the deeper of {near edge, 50% level}, clamped inside the gap.
        if direction == "LONG":
            entry_price = min(selected.near_edge, disp_50)      # gap reaches 50%, so >= bottom
            entry_price = max(entry_price, selected.bottom)
        else:
            entry_price = max(selected.near_edge, disp_50)
            entry_price = min(entry_price, selected.top)
    elif CONFIG.entry_mode == "fvg_50":
        entry_price = selected.mid          # consequent encroachment (50% of gap)
    else:
        entry_price = selected.near_edge

    # Stop placement (CONFIG.stop_mode):
    #   "sweep"   = beyond the sweep extreme (original v1 / validated config).
    #   "fvg"     = beyond the extreme of the 3 FVG candles.
    #   "percent" = fixed percent of entry price (scales with price level).
    # Buffer = fixed stop_buffer + atr_stop_mult * ATR at the MSS candle.
    atr = atr_at(candles, i, CONFIG.atr_period)
    buffer = CONFIG.stop_buffer + CONFIG.atr_stop_mult * atr
    if CONFIG.stop_mode == "percent":
        dist = max(CONFIG.stop_floor, entry_price * CONFIG.stop_pct)
        if direction == "LONG":
            stop_price = entry_price - dist
            risk = entry_price - stop_price
        else:
            stop_price = entry_price + dist
            risk = stop_price - entry_price
    else:
        if CONFIG.stop_mode == "fvg":
            fvg_candles = candles[selected.c1_index:selected.c3_index + 1]
            stop_anchor_long = min(c.low for c in fvg_candles)
            stop_anchor_short = max(c.high for c in fvg_candles)
        else:  # "sweep"
            stop_anchor_long = sweep_extreme
            stop_anchor_short = sweep_extreme
        if direction == "LONG":
            stop_price = stop_anchor_long - buffer
            risk = entry_price - stop_price
        else:
            stop_price = stop_anchor_short + buffer
            risk = stop_price - entry_price
    if risk <= 0:
        return None

    target_price = _choose_target(direction, entry_price, risk, levels)
    if target_price is None:
        return None

    return Setup(
        direction=direction,
        swept_level=swept_level,
        sweep_extreme=sweep_extreme,
        sweep_extreme_index=sweep_extreme_index,
        reversal_swing=reversal_swing,
        mss_index=i,
        displacement_start=displacement_start,
        displacement_end=displacement_end,
        fvg=selected,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        armed_time=mss.time,
    )