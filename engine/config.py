"""
config.py — Single source of truth for all strategy parameters.

Every tunable value from the Modified ICT 2022 Model v1 spec lives here.
Change values here; the engine reads them. Nothing is hard-coded elsewhere.

All prices are in US dollars (NQ CFD / index feed).
All times are New York time (America/New_York), DST-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategyConfig:
    # ── Instrument & data ────────────────────────────────────────────────
    symbol: str = "NQ"
    entry_timeframe_minutes: int = 5          # primary timeframe

    # ── Trading window (New York time) ───────────────────────────────────
    window_start: str = "08:30"               # entry FILL window open (ET)
    window_end: str = "11:00"                 # entry FILL window close (ET)
    # MSS (setup-arm) time must fall in [mss_start, mss_end). Early pre-session
    # setups (07:xx) were net losers across 2015-2025, so restrict to 08:00-11:00.
    mss_start: str = "07:30"
    mss_end: str = "11:00"
    # Sweep/MSS may occur before the window; the ENTRY FILL must be inside it.

    # ── Daily bias (directional filter) ──────────────────────────────────
    # Which bias method gates trades:
    #   "structure_hhll" = 4H HH/HL trend (2 higher highs + 2 higher lows to
    #                      establish; single BOS flips), neutral = no trade.
    #   "4h_structure"  = 4H single-BOS market-structure trend (always one side)
    #   "midnight_open" = price vs the NY midnight open (legacy, simple proxy)
    #   "off"           = no bias gate (take all sweep->MSS->FVG setups)
    bias_method: str = "4h_structure"
    bias_filter_enabled: bool = True          # master on/off (off == bias_method "off")

    # -- 4H market-structure bias --
    fractal_n_bias: int = 2                    # fractal N for marking 4H swings
    bias_4h_bos_close: bool = True             # BOS confirmed on CLOSE beyond/at swing
    # Regime (chop) filter: when True, skip trades while 4H structure is in a
    # contracting/overlapping range (lower-high AND higher-low). Layered on top
    # of the directional bias; it only removes trades, never changes direction.
    regime_filter: bool = True
    # Bias = direction of the last confirmed 4H break of structure (always one side).
    # Evaluated at setup-arm time, using only 4H candles closed before the session.

    # -- midnight-open bias (legacy) --
    bias_neutral_buffer: float = 10.0          # +/- $ around midnight open => no trade

    # ── Liquidity levels & sweep ─────────────────────────────────────────
    sweep_penetration: float = 3.0            # price must trade >= $3 through a level
    # No close-back required. No staleness limit.

    # Session boundaries (ET) used to build session highs/lows.
    # [CODER NOTE] Confirm/adjust these. Format: (start "HH:MM", end "HH:MM").
    session_asian: tuple = ("20:00", "00:00")
    session_london: tuple = ("02:00", "05:00")
    session_ny: tuple = ("08:30", "16:00")

    # ── Market Structure Shift (MSS) ─────────────────────────────────────
    fractal_n_entry: int = 1                   # fractal N for intraday reversal/MSS
    # MSS = 5-min candle CLOSES beyond/at the sweep's reversal swing, bias-aligned.

    # ── Displacement & FVG ───────────────────────────────────────────────
    # Displacement is defined by leaving an FVG inside the MSS-breaking leg.
    # FVG selection: first FVG in the discount (long) / premium (short) half,
    # where halves are split by the 50% fib of the displacement leg.
    min_fvg_size: float = 0.0                 # optional min FVG width filter ($); 0 = off

    # ── Entry / Stop / Target ────────────────────────────────────────────
    # Entry depth into the FVG:
    #   "near_edge" = enter at the near edge (first touch).
    #   "fvg_50"    = enter at 50% of the FVG gap (consequent encroachment).
    # Entry depth into the FVG:
    #   "near_edge"   = enter at the near edge (first touch).
    #   "fvg_50"      = enter at 50% of the FVG gap (consequent encroachment).
    #   "disp_50_fvg" = enter at FVG near edge, but ONLY if the FVG reaches the
    #                   50% equilibrium of the displacement leg (else no trade).
    entry_mode: str = "disp_50_fvg"
    # Stop placement:
    #   "sweep"   = beyond the sweep extreme (original v1).
    #   "fvg"     = beyond the extreme of the 3 FVG candles.
    #   "percent" = entry_price * stop_pct (pure proportion of price).
    #   "atr"     = atr_mult * daily ATR (volatility-based; available, not active).
    stop_mode: str = "percent"
    # percent mode: stop distance = entry_price * stop_pct (0.2% = $40 at 20,000).
    stop_pct: float = 0.002
    stop_floor: float = 0.0
    # atr mode (inactive): stop distance = atr_mult * daily_ATR(atr_period days).
    atr_mult: float = 0.20
    # Buffer = fixed stop_buffer  +  atr_stop_mult * ATR(atr_period) at the MSS.
    stop_buffer: float = 5.0                  # fixed $ component (sweep/fvg modes)
    atr_period: int = 14                      # candles for ATR
    atr_stop_mult: float = 0.0                # ATR multiples added (0 = disabled)
    # Target mode:
    #   "liquidity" = target the nearest opposing liquidity pool that is at least
    #                 `min_reward_r` away; if none qualifies, fall back to fixed RR.
    #   "fixed_rr"  = always target risk_reward x risk.
    target_mode: str = "fixed_rr"
    risk_reward: float = 3.0                  # fixed 1:3 R:R (fallback / fixed mode)
    min_reward_r: float = 1.0                 # min target distance in R for liquidity mode
    # No partials. No time-based exit beyond the session flat.

    max_trades_per_day: int = 1

    # Force any open trade flat at this NY time (session-close discipline).
    # Set to None to disable and run to TP/SL only.
    session_close_flat: str = "16:00"
    # Number of extra trading sessions to hold before the flat applies.
    #   0 = flat at the entry day's close (same-day discipline).
    #   1 = flat at the NEXT trading day's close.
    # Never holds across the weekend: a trade is always closed by Friday's flat
    # time at the latest, regardless of hold_sessions.
    hold_sessions: int = 0

    # ── Backtest mechanics ───────────────────────────────────────────────
    slippage: float = 2.0                     # $ applied AGAINST position
    # Applied to entry (worse fill) and stop (worse fill).
    # Target assumed to fill as resting limit -> no slippage.
    slippage_on_target: bool = False
    intrabar_stop_first: bool = True          # when stop & target in same bar -> stop wins

    # ── News filter (LIVE ONLY — backtest ignores all news) ──────────────
    news_filter_live_only: bool = True        # documentation flag; backtest never blocks
    # Live rules (not used in backtest):
    #   - No trading on NFP days at all.
    #   - On CPI weeks, no trading until after CPI release.
    #   - FOMC excluded by timing (falls outside 08:30-11:00).

    # ── Risk (for position sizing / reporting) ───────────────────────────
    account_balance: float = 100000.0
    risk_per_trade: float = 0.01              # 1% risk per trade
    point_value: float = 1.0                  # $ per 1.0 index point per 1 unit
    # NQ CFD point value varies by broker; 1.0 keeps R-based math clean.


# Single shared instance the whole engine imports.
CONFIG = StrategyConfig()


# ── Timezone constant ────────────────────────────────────────────────────
NY_TZ = "America/New_York"