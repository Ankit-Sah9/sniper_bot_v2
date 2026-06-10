# analysis/ — P&L & pattern visualization (additive, non-invasive)

This folder is **completely separate** from your `engine/` and `run.py` code.
Nothing here imports or modifies the strategy/backtester. It only **reads** the
CSV files the backtester already writes into `data/backtest/`.

## What it does

After you run a backtest, this turns `data/backtest/trade_log.csv` into an
interactive, **offline** dashboard (no internet needed) that answers
"which days / months make money", shown as real trading-style charts.

Visualizations included:

1. **Monthly equity candles** — green/red candlesticks, one per month, with wicks
   (the "trading chart" view of monthly profit).
2. **Cumulative equity curve** with drawdown shading.
3. **Calendar heatmap** — Year × Month P&L grid (green = profit, red = loss).
4. **Seasonality** — P&L by calendar month, all years combined.
5. **P&L by day of week** — which weekday pays.
6. **P&L by entry hour** (NY time).
7. **Yearly P&L** bars.
8. **Long vs Short** breakdown.

Plus headline cards: total P&L, win rate, profit factor, best/worst month, etc.

## How to run

From the project root (`sniper_bot_v2/`):

```bash
# 1. produce the backtest output (your existing command, unchanged)
python run.py backtest

# 2. build the dashboard (this also runs the aggregation for you)
python analysis/build_dashboard.py
```

Then open `analysis/output/dashboard.html` in any browser.

### Optional: run on a different trade log

```bash
python analysis/analyze.py --trades data/backtest/trade_log.csv
python analysis/build_dashboard.py
```

## Files

| File | Role |
|------|------|
| `analyze.py` | Reads `trade_log.csv`, writes `output/analysis.json` (aggregates). |
| `build_dashboard.py` | Reads `analysis.json`, writes self-contained `output/dashboard.html`. |
| `output/analysis.json` | Machine-readable aggregates (regenerated each run). |
| `output/dashboard.html` | The interactive report (regenerated each run). |

## Notes

- P&L is anchored on each trade's **exit time** (when it's booked), matching the
  engine's own `compute_metrics`. Only `WIN`/`LOSS`/`FLAT` trades are counted.
- The dashboard embeds its data inline and draws everything with plain
  `<canvas>` + vanilla JS, so it works with no internet connection.
- Safe to delete `analysis/` entirely; it has zero effect on the bot.
