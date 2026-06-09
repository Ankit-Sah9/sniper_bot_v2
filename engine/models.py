"""
models.py — Core data structures for the Modified ICT 2022 engine.

Pure dataclasses, no logic beyond simple helpers. Kept separate so both the
backtest engine and any future live module share identical definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Candle:
    """A single 5-minute OHLC bar. `time` is a timezone-aware NY datetime."""
    time: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def bullish(self) -> bool:
        return self.close >= self.open


@dataclass(frozen=True)
class SwingPoint:
    """A fractal swing high or low."""
    index: int          # position in the candle list
    time: datetime
    price: float
    kind: str           # "HIGH" or "LOW"


@dataclass(frozen=True)
class LiquidityLevel:
    """A level whose breach counts as a liquidity sweep."""
    price: float
    kind: str           # "HIGH" (BSL) or "LOW" (SSL)
    source: str         # e.g. "PREV_DAY", "PREV_WEEK", "ASIAN", "LONDON", "NY"
    formed_time: datetime


@dataclass
class FVG:
    """
    3-candle Fair Value Gap.

    Bullish FVG: gap between candle1.high and candle3.low (c1.high < c3.low).
                 zone = [c1.high (bottom), c3.low (top)]
    Bearish FVG: gap between candle3.high and candle1.low (c1.low > c3.high).
                 zone = [c3.high (bottom), c1.low (top)]
    """
    direction: str          # "BULLISH" or "BEARISH"
    top: float
    bottom: float
    c1_index: int
    c3_index: int
    formed_time: datetime

    @property
    def mid(self) -> float:
        """Consequent encroachment (50% of the gap)."""
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def near_edge(self) -> float:
        """
        The edge price reaches FIRST on a retrace.
        Bullish FVG sits below price after up-displacement -> price retraces DOWN,
        so it touches the TOP edge first.
        Bearish FVG sits above price after down-displacement -> price retraces UP,
        so it touches the BOTTOM edge first.
        """
        return self.top if self.direction == "BULLISH" else self.bottom


@dataclass
class Setup:
    """A fully-formed, validated setup ready to arm an entry order."""
    direction: str              # "LONG" or "SHORT"
    swept_level: LiquidityLevel
    sweep_extreme: float        # highest high / lowest low reached during sweep
    sweep_extreme_index: int
    reversal_swing: SwingPoint  # the swing whose break = MSS
    mss_index: int              # candle index where MSS confirmed (close beyond swing)
    displacement_start: float   # price at start of displacement leg
    displacement_end: float     # extreme of displacement leg
    fvg: FVG                    # selected entry FVG
    entry_price: float          # near edge of the FVG
    stop_price: float
    target_price: float
    armed_time: datetime        # time of the MSS candle (setup completion)


@dataclass
class Trade:
    """A completed (or open) trade record for logging and metrics."""
    trade_id: str
    direction: str
    armed_time: datetime
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    risk: float                 # entry-to-stop distance ($)
    swept_level_price: float
    swept_level_source: str
    sweep_extreme: float
    fvg_top: float
    fvg_bottom: float
    midnight_open: float
    bias: str
    # outcome
    result: str = "OPEN"        # "WIN" | "LOSS" | "OPEN"
    exit_time: datetime | None = None
    exit_price: float | None = None
    r_gained: float = 0.0       # +RR on win, -1 on loss
    pnl: float = 0.0            # dollar P&L using risk model
    points_gained: float = 0.0
