# NQ — Modified ICT 2022 Model (Backtest + Dashboard)

A clean-room implementation of the **Modified ICT 2022 Model** for NASDAQ (NQ),
built to the v1 strategy specification. Includes a backtesting engine, a
walk-forward validator, a synthetic sample dataset, and a zero-dependency
HTML dashboard.

This project was **rebuilt from scratch** for this strategy. It reuses
*architectural patterns* from a prior MT5 "Sniper" bot (DST handling, news
classification structure, walk-forward, risk model) but **none of that bot's
strategy logic** — see `docs/BUILD_PLAN.md` for the full reuse/rewrite map.

---

## The strategy in one paragraph

On a qualifying day, price sweeps a liquidity level (previous day/week high/low
or a session high/low) by at least $3. Price reverses, a fractal swing (N=2)
forms on the reversal side, and a 5-minute candle **closes beyond that swing**
(the MSS). The breaking leg must leave a **Fair Value Gap** (the displacement).
Using the 50% fib of the displacement leg, the engine selects the first FVG in
the discount half (long) / premium half (short) and enters at its **near edge**
on the first touch — provided the fill lands inside **08:30–11:00 ET** and the
**mechanical bias** (price vs the NY midnight open, ±$10 neutral buffer) agrees.
Stop is **$5 beyond the sweep extreme**, target is a fixed **1:3 R:R**, one trade
per day, and the trade runs to TP/SL with no time-based exit.

All parameters live in `engine/config.py` — the single source of truth.

---

## Quick start

Requires **Python 3.11+** (uses `zoneinfo`, no third-party packages needed for
the core engine).

```bash
# 1. Generate the synthetic sample dataset (creates data/history/NQ_5min.csv)
python run.py gen-data

# 2. Run the backtest (writes results into data/backtest/)
python run.py backtest

# 3. Run walk-forward validation
python run.py walk-forward

# Or do everything at once:
python run.py all

# Inspect the active parameters:
python run.py config

# Pull real data from a running MT5 terminal (Windows + MetaTrader5):
python run.py pull-data --symbol NAS100 --start 2024-01-01 --end 2024-06-30
```

Then open **`dashboard.html`** in any browser.

- If it opens over `http://` (e.g. `python -m http.server`), it auto-loads the
  result files.
- If you open it directly as a `file://` URL and the browser blocks local file
  reads, use the **file pickers** in the top-right to load the four files from
  `data/backtest/` manually. Everything else works identically.

---

## ⚠️ About the sample data

`data/history/NQ_5min.csv` is **synthetic** — generated to exercise the engine
and populate the dashboard. The win rate and P&L it produces are **artifacts of
the generator**, not a real edge. To get meaningful results, replace it with a
real 5-minute NQ export:

```bash
python run.py backtest --data path/to/your_NQ_5min.csv --source-tz UTC
```

### Required CSV format

```
time,open,high,low,close
2024-01-02 00:00:00,16800.25,16805.50,16798.00,16803.75
...
```

- `time` can be in NY local time (`--source-tz America/New_York`, the default)
  or UTC (`--source-tz UTC`). It is converted to NY with DST handling.
- 5-minute bars. The engine relies on a candle existing at **00:00 ET** each day
  to read the midnight-open bias reference.
- Extra columns (e.g. `time_broker`) are ignored, so the MT5 export file works
  as-is.

---

## Pulling real data from MetaTrader 5

A built-in exporter pulls M5 bars straight from a **running, logged-in MT5
terminal** and writes them in the engine's format, auto-converting to NY time.

**Requirements:** Windows, an open MT5 terminal logged into your broker, and
`pip install MetaTrader5`. (The package is Windows-only — it won't run on
Mac/Linux.)

```bash
# Find your broker's Nasdaq symbol in Market Watch first (NAS100 / USTEC / US100 / ...)
python run.py pull-data --symbol NAS100 --start 2024-01-01 --end 2024-06-30

# Then backtest the exported (already NY-converted) file:
python run.py backtest --data data/history/NQ_5min.csv --source-tz America/New_York
```

**Timezone note (important):** MT5 does not expose the broker's timezone, so the
tool *infers* the server offset by comparing a live tick to UTC and converts to
NY. This catches the current offset; brokers that shift ±1h with European DST may
be slightly off on older dates. The tool **prints the detected offset** and keeps
a `time_broker` audit column so you can verify against the chart. If the offset
looks wrong, override it:

```bash
python run.py pull-data --symbol NAS100 --start 2024-01-01 --end 2024-06-30 --assume-offset 2
```

Verify by checking that a known session lines up (e.g. the 09:30 ET equity open
should sit at the start of the cash-session move). The midnight-open bias depends
on this, so it's worth a one-time sanity check.

---

## Project layout

```
nq_ict_bot/
├── run.py                      # CLI: gen-data | backtest | walk-forward | all | config
├── dashboard.html              # zero-dependency results dashboard
├── engine/
│   ├── config.py               # ALL strategy parameters (edit here)
│   ├── models.py               # Candle, FVG, SwingPoint, LiquidityLevel, Setup, Trade
│   ├── indicators.py           # fractals, FVG detection, liquidity levels, sweep test
│   ├── strategy.py             # bias + the full sweep→MSS→displacement→FVG chain
│   ├── data_loader.py          # CSV → Candle, timezone/DST conversion
│   ├── mt5_export.py           # pull M5 bars from a running MT5 terminal
│   ├── backtest.py             # fills, exits, slippage, metrics, walk-forward
│   └── generate_sample_data.py # synthetic 5-min NQ generator
├── data/
│   ├── history/NQ_5min.csv     # input data (synthetic; replace with real)
│   └── backtest/               # outputs: trade_log, equity_curve, metrics, walk_forward
└── docs/
    ├── STRATEGY_SPEC.md        # the v1 specification
    └── BUILD_PLAN.md           # old-project reuse/rewrite map
```

---

## Outputs

| File | Contents |
|------|----------|
| `data/backtest/trade_log.csv` | one row per trade: entry/stop/target, swept level, FVG, bias, result, R, P&L |
| `data/backtest/equity_curve.csv` | cumulative R after each closed trade |
| `data/backtest/metrics.json` | win rate, total R, profit factor, max drawdown, etc. |
| `data/backtest/walk_forward_summary.csv` | per-fold stability metrics |

---

## Backtest assumptions (v1)

- **Fills:** entry fills when price *touches* the FVG near edge; stop/target fill
  when price *trades through*. Entry and stop get **$2 slippage against** the
  position; target fills with no slippage (resting limit model).
- **Same-bar stop & target:** stop assumed hit first (pessimistic).
- **Granularity:** 5-minute only; intra-bar sequence is not resolved finer.
- **News:** the backtest **ignores news entirely**. The NFP/CPI/FOMC filter is a
  live-only rule (see spec). Live and backtest results are therefore not directly
  comparable.

---

## Notes for extending

- Bias is deliberately modular (`strategy.evaluate_bias`) — it's a v1 placeholder
  meant to be refined.
- `config.min_fvg_size` lets you add a minimum FVG width filter.
- Order Blocks and OTE were intentionally excluded from v1; the FVG-only entry
  keeps the parameter surface small.
- The entry timeframe is fixed at 5-min in v1; making it configurable is a
  natural next step for testing 5 vs 15.
