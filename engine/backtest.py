"""
backtest.py — Backtest engine for the Modified ICT 2022 Model.

Pipeline per candle i (scanning chronologically):
  1. Skip if a trade already taken today (one trade/day).
  2. Try to confirm a setup with MSS at candle i (strategy.find_setup_at).
  3. If a setup arms, simulate the entry fill on subsequent candles:
       - entry fills when price TOUCHES the FVG near edge,
       - the fill must occur within 08:30-11:00 ET,
       - bias (vs midnight open, continuous) must be aligned at fill time,
       - apply entry slippage AGAINST the position.
  4. After fill, simulate exit to TP or SL (no time exit):
       - SL/TP fill when price TRADES THROUGH the level,
       - if both fall in the same bar -> stop wins (config),
       - apply stop slippage; target = no slippage.
  5. Record the trade; lock the day.

News is intentionally IGNORED in the backtest (live-only filter).

Outputs:
  data/backtest/trade_log.csv
  data/backtest/equity_curve.csv
  data/backtest/metrics.json
  data/backtest/walk_forward_summary.csv  (walk-forward mode)
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, date
from pathlib import Path
from uuid import uuid4

from .config import CONFIG
from .models import Candle, Trade, Setup
from .strategy import find_setup_at, evaluate_bias, in_entry_window, midnight_open_for_day

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "backtest"
TRADE_LOG = OUT_DIR / "trade_log.csv"
EQUITY_CSV = OUT_DIR / "equity_curve.csv"
METRICS_JSON = OUT_DIR / "metrics.json"
FOLD_CSV = OUT_DIR / "walk_forward_summary.csv"


# ── Fill simulation ──────────────────────────────────────────────────────

def _simulate_entry(candles: list[Candle], setup: Setup,
                    bias: str, midnight_open: float | None) -> tuple[int, float] | None:
    """
    Find the bar where price first touches the FVG near edge, within the entry
    window, with bias aligned. Returns (fill_index, fill_price) or None.
    Entry slippage is applied against the position.

    `bias` is the resolved directional bias for this setup ("BULLISH" |
    "BEARISH" | "NEUTRAL"). For 4h_structure it is evaluated at setup-arm time
    and constant through the fill. For midnight_open it is recomputed at the
    touch candle (legacy continuous behaviour).
    """
    start = setup.mss_index + 1   # entry can only happen after the MSS candle
    edge = setup.entry_price
    want = "BULLISH" if setup.direction == "LONG" else "BEARISH"

    for j in range(start, len(candles)):
        c = candles[j]
        # Same trading day only.
        if c.time.date() != setup.armed_time.date():
            return None
        if not in_entry_window(c.time):
            # Window closed for the day -> setup expires.
            if c.time.timetz().replace(tzinfo=None).strftime("%H:%M") >= CONFIG.window_end:
                return None
            continue

        touched = c.low <= edge <= c.high
        if not touched:
            continue

        # Bias gate.
        if CONFIG.bias_filter_enabled and CONFIG.bias_method != "off":
            if CONFIG.bias_method == "midnight_open":
                # Legacy: recompute continuously at the touch price.
                effective = evaluate_bias(edge, midnight_open)
            else:
                effective = bias  # 4h_structure: fixed at arm time
            if effective != want:
                return None  # bias not aligned -> reject

        # Apply entry slippage against the position.
        if setup.direction == "LONG":
            fill = edge + CONFIG.slippage
        else:
            fill = edge - CONFIG.slippage
        return j, fill
    return None


def _flat_cutoff_date(entry_day, hold_sessions: int, trading_days: list | None):
    """
    The date whose close the trade is flattened at.

    = the Nth trading day at/after entry_day (N = hold_sessions), but never later
    than the Friday of the entry week (no weekend holds).

    If trading_days is provided, "Nth trading day" steps through actual sessions
    in the data; otherwise it falls back to calendar weekdays.
    """
    from datetime import timedelta

    # Friday of the entry week = entry_day + (4 - weekday) days (Mon=0..Fri=4).
    days_to_friday = 4 - entry_day.weekday()
    week_friday = entry_day + timedelta(days=max(0, days_to_friday))

    if hold_sessions <= 0:
        return min(entry_day, week_friday)

    if trading_days:
        future = [d for d in trading_days if d >= entry_day]
        if future:
            idx = min(hold_sessions, len(future) - 1)
            nth = future[idx]
            return min(nth, week_friday)

    # Fallback: step weekdays.
    d = entry_day
    steps = 0
    while steps < hold_sessions:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            steps += 1
    return min(d, week_friday)


def _simulate_exit(candles: list[Candle], start_idx: int, setup: Setup,
                   entry_fill: float, trading_days: list | None = None) -> tuple[int, float, str]:
    """
    Walk forward from the bar AFTER entry until TP or SL fills, OR until the
    flat cutoff, at which point any open trade is closed at that candle's close
    ("FLAT").

    Flat cutoff = the close (CONFIG.session_close_flat) of the Nth trading day
    at/after entry, where N = CONFIG.hold_sessions. Never holds across the
    weekend: the cutoff is capped at the Friday of the entry week.

    Returns (exit_index, exit_price, result). result is "WIN" | "LOSS" | "FLAT".
    """
    stop = setup.stop_price
    target = setup.target_price
    entry_dt = candles[start_idx].time
    entry_day = entry_dt.date()

    flat_hm = CONFIG.session_close_flat
    flat_minutes = None
    flat_date = None
    if flat_hm:
        fh, fm = map(int, flat_hm.split(":"))
        flat_minutes = fh * 60 + fm
        flat_date = _flat_cutoff_date(entry_day, CONFIG.hold_sessions, trading_days)

    for j in range(start_idx + 1, len(candles)):
        c = candles[j]

        # Flat cutoff: at/after the flat time on the cutoff date (or any later
        # date, as a safety net if the exact cutoff day has no candles).
        if flat_minutes is not None and flat_date is not None:
            cm = c.time.hour * 60 + c.time.minute
            cd = c.time.date()
            if cd > flat_date or (cd == flat_date and cm >= flat_minutes):
                return j, c.close, "FLAT"

        if setup.direction == "LONG":
            hit_stop = c.low <= stop
            hit_tp = c.high >= target
        else:
            hit_stop = c.high >= stop
            hit_tp = c.low <= target

        if hit_stop and hit_tp:
            # Same-bar ambiguity -> stop wins (pessimistic).
            if CONFIG.intrabar_stop_first:
                return j, _stop_fill(setup, stop), "LOSS"
            return j, target, "WIN"
        if hit_stop:
            return j, _stop_fill(setup, stop), "LOSS"
        if hit_tp:
            tp_fill = target if not CONFIG.slippage_on_target else (
                target - CONFIG.slippage if setup.direction == "LONG"
                else target + CONFIG.slippage)
            return j, tp_fill, "WIN"

    # Unresolved at end of data.
    last = candles[-1]
    return len(candles) - 1, last.close, "OPEN"


def _stop_fill(setup: Setup, stop: float) -> float:
    """Stop fill with slippage against the position."""
    if setup.direction == "LONG":
        return stop - CONFIG.slippage
    return stop + CONFIG.slippage


# ── Main backtest loop ───────────────────────────────────────────────────

def run_backtest(candles: list[Candle],
                 start: date | None = None,
                 end: date | None = None) -> list[Trade]:
    """Run the full backtest over `candles`, returning the list of trades."""
    if not candles:
        return []

    # Precompute midnight opens per day in ONE pass (not a full scan per day).
    days = sorted({c.time.date() for c in candles})
    midnight: dict[date, float | None] = {d: None for d in days}
    for c in candles:
        if c.time.hour == 0 and c.time.minute == 0:
            d0 = c.time.date()
            if midnight.get(d0) is None:
                midnight[d0] = c.open

    # Build the 4H structure bias engine once (only needed for that method).
    four_h = None
    if CONFIG.bias_method in ("4h_structure", "structure_hhll") and CONFIG.bias_filter_enabled:
        from .htf_bias import FourHourBias
        four_h = FourHourBias(candles)

    # Precompute daily ATR only if the ATR stop mode is active (keeps the
    # dependency optional so non-ATR runs don't require it).
    if CONFIG.stop_mode == "atr":
        try:
            from .strategy import set_daily_atr_lookup
            set_daily_atr_lookup(candles)
        except ImportError:
            pass

    trades: list[Trade] = []
    traded_days: set[date] = set()

    i = 0
    n = len(candles)
    while i < n:
        c = candles[i]
        d = c.time.date()

        if start and d < start:
            i += 1
            continue
        if end and d > end:
            break

        # One trade per day.
        if d in traded_days:
            i += 1
            continue

        # MSS (setup-arm) time must fall within the configured MSS window.
        # Early pre-session setups (07:xx) were net losers, so this restricts
        # confirmation to mss_start..mss_end (default 08:00-11:00).
        sh, sm = map(int, CONFIG.mss_start.split(":"))
        eh, em = map(int, CONFIG.mss_end.split(":"))
        minute_of_day = c.time.hour * 60 + c.time.minute
        if not (sh * 60 + sm <= minute_of_day < eh * 60 + em):
            i += 1
            continue

        setup = find_setup_at(candles, i)
        if setup is None:
            i += 1
            continue

        # Resolve bias at setup-arm time (MSS candle time).
        if four_h is not None:
            arm_bias = four_h.bias_as_of(setup.armed_time)
            # Regime (chop) filter: skip if 4H structure is contracting/ranging.
            if CONFIG.regime_filter and four_h.is_choppy_as_of(setup.armed_time):
                i += 1
                continue
        else:
            arm_bias = "NEUTRAL"  # midnight_open recomputes in _simulate_entry

        mo = midnight.get(d)
        entry = _simulate_entry(candles, setup, arm_bias, mo)
        if entry is None:
            i += 1
            continue

        fill_idx, fill_price = entry
        exit_idx, exit_price, result = _simulate_exit(candles, fill_idx, setup, fill_price, days)

        # Recompute realized risk from actual fill (slippage shifts it slightly).
        if setup.direction == "LONG":
            risk = fill_price - setup.stop_price
            points = exit_price - fill_price
        else:
            risk = setup.stop_price - fill_price
            points = fill_price - exit_price

        # Realized R from the actual exit, valid for WIN / LOSS / FLAT alike.
        # (With liquidity targets the reward is not a fixed multiple, so we always
        # derive R from the real exit distance rather than assuming risk_reward.)
        r_gained = points / risk if risk else 0.0

        risk_amount = CONFIG.account_balance * CONFIG.risk_per_trade
        pnl = risk_amount * r_gained

        trades.append(Trade(
            trade_id=f"{c.time.strftime('%Y%m%d')}-{uuid4().hex[:6]}",
            direction=setup.direction,
            armed_time=setup.armed_time,
            entry_time=candles[fill_idx].time,
            entry_price=round(fill_price, 2),
            stop_price=round(setup.stop_price, 2),
            target_price=round(setup.target_price, 2),
            risk=round(risk, 2),
            swept_level_price=round(setup.swept_level.price, 2),
            swept_level_source=setup.swept_level.source,
            sweep_extreme=round(setup.sweep_extreme, 2),
            fvg_top=round(setup.fvg.top, 2),
            fvg_bottom=round(setup.fvg.bottom, 2),
            midnight_open=round(mo, 2) if mo is not None else 0.0,
            bias="BULLISH" if setup.direction == "LONG" else "BEARISH",
            result=result,
            exit_time=candles[exit_idx].time,
            exit_price=round(exit_price, 2),
            r_gained=round(r_gained, 3),
            pnl=round(pnl, 2),
            points_gained=round(points, 2),
        ))

        traded_days.add(d)
        # Resume scanning from the day after the entry to enforce 1/day cleanly.
        i = fill_idx + 1

    return trades


# ── Metrics ──────────────────────────────────────────────────────────────

def compute_metrics(trades: list[Trade]) -> dict:
    # All filled trades count (WIN, LOSS, FLAT). FLAT = closed at session flat.
    closed = [t for t in trades if t.result in ("WIN", "LOSS", "FLAT")]
    wins = [t for t in closed if t.r_gained > 0]
    losses = [t for t in closed if t.r_gained < 0]
    flats = [t for t in closed if t.result == "FLAT"]
    total_r = sum(t.r_gained for t in closed)
    total_pnl = sum(t.pnl for t in closed)

    gross_win = sum(t.r_gained for t in wins)
    gross_loss = abs(sum(t.r_gained for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss else (gross_win if gross_win else 0.0)

    # Equity curve in R and max drawdown (chronological by exit).
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    for t in sorted(closed, key=lambda x: x.exit_time or x.entry_time):
        equity += t.r_gained
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        curve.append((t.exit_time, round(equity, 3)))

    return {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flats),
        "win_rate_pct": round(100 * len(wins) / len(closed), 2) if closed else 0.0,
        "total_r": round(total_r, 3),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 3),
        "avg_r_per_trade": round(total_r / len(closed), 3) if closed else 0.0,
        "max_drawdown_r": round(max_dd, 3),
        "rr_setting": CONFIG.risk_reward,
        "_equity_curve": curve,
    }


# ── Output writers ───────────────────────────────────────────────────────

def write_outputs(trades: list[Trade], metrics: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Trade log
    fields = [
        "trade_id", "direction", "armed_time", "entry_time", "entry_price",
        "stop_price", "target_price", "risk", "swept_level_price",
        "swept_level_source", "sweep_extreme", "fvg_top", "fvg_bottom",
        "midnight_open", "bias", "result", "exit_time", "exit_price",
        "r_gained", "pnl", "points_gained",
    ]
    with TRADE_LOG.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in trades:
            row = asdict(t)
            row["armed_time"] = t.armed_time.strftime("%Y-%m-%d %H:%M")
            row["entry_time"] = t.entry_time.strftime("%Y-%m-%d %H:%M")
            row["exit_time"] = t.exit_time.strftime("%Y-%m-%d %H:%M") if t.exit_time else ""
            w.writerow({k: row[k] for k in fields})

    # Equity curve
    curve = metrics.pop("_equity_curve", [])
    with EQUITY_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exit_time", "cumulative_r"])
        for t_time, eq in curve:
            w.writerow([t_time.strftime("%Y-%m-%d %H:%M") if hasattr(t_time, "strftime") else t_time, eq])

    # Metrics
    with METRICS_JSON.open("w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)


# ── Walk-forward ─────────────────────────────────────────────────────────

def walk_forward(candles: list[Candle], folds: int = 4) -> list[dict]:
    """
    Simple sequential walk-forward: split the date range into `folds` equal
    validation windows and report metrics per fold. (No re-optimization here,
    since v1 has fixed parameters — this validates stability across periods.)
    """
    days = sorted({c.time.date() for c in candles})
    if len(days) < folds:
        return []
    chunk = len(days) // folds
    rows = []
    for f in range(folds):
        seg_days = days[f * chunk: (f + 1) * chunk] if f < folds - 1 else days[f * chunk:]
        if not seg_days:
            continue
        seg_trades = run_backtest(candles, start=seg_days[0], end=seg_days[-1])
        m = compute_metrics(seg_trades)
        m.pop("_equity_curve", None)
        rows.append({
            "fold": f + 1,
            "start": seg_days[0].isoformat(),
            "end": seg_days[-1].isoformat(),
            "trades": m["total_trades"],
            "win_rate_pct": m["win_rate_pct"],
            "profit_factor": m["profit_factor"],
            "total_r": m["total_r"],
            "max_drawdown_r": m["max_drawdown_r"],
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if rows:
        with FOLD_CSV.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows