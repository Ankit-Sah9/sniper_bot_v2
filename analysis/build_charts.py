"""
analysis/build_charts.py — Build a TradingView-style multi-timeframe chart
viewer for the bot's trades. ADDITIVE: reads the master 5-min history and the
trade log; never touches engine/ or run.py.

Idea
----
668k 5-min candles (11 years) is far too much to dump into one browser page.
So instead of one giant chart, this builds a *per-trade* viewer (exactly how
you actually review a strategy): for each trade it slices a window of 5-min
candles around the entry, resamples that window up to 15m / 1h / 1D, and
attaches the trade's markings (entry, stop, target, exit, swept level, FVG).

The viewer (built by render_charts.py) then lets you:
  * step through trades,
  * switch between 5m / 15m / 1h / 1D,
  * see entry/exit marked with a green target zone + red stop zone like a
    real chart annotation.

Outputs:
  analysis/output/charts.json   (consumed by render_charts.py)

Usage:
  python analysis/build_charts.py                      # default: most recent 60 trades
  python analysis/build_charts.py --year 2024          # only 2024 trades
  python analysis/build_charts.py --result WIN         # only wins
  python analysis/build_charts.py --max 40             # cap how many trades
  python analysis/build_charts.py --pre-days 3 --post-days 2   # window size
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "data" / "history" / "NQ_master.csv"
TRADES = ROOT / "data" / "backtest" / "trade_log.csv"
OUT_DIR = Path(__file__).resolve().parent / "output"
OUT_JSON = OUT_DIR / "charts.json"

# Pandas resample rules for each timeframe label.
TF_RULES = {"5m": "5min", "15m": "15min", "1h": "1h", "1D": "1D"}


def load_history() -> pd.DataFrame:
    df = pd.read_csv(HIST)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").set_index("time")
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna()


def load_trades() -> pd.DataFrame:
    df = pd.read_csv(TRADES)
    df = df[df["result"].isin(["WIN", "LOSS", "FLAT"])].copy()
    for c in ("entry_time", "exit_time", "armed_time"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("entry_price", "stop_price", "target_price", "exit_price",
              "swept_level_price", "fvg_top", "fvg_bottom", "pnl", "r_gained"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["entry_time"]).sort_values("entry_time").reset_index(drop=True)


def resample(df_window: pd.DataFrame, rule: str) -> list[dict]:
    """Resample an OHLC window to `rule`, return list of candle dicts."""
    o = df_window["open"].resample(rule).first()
    h = df_window["high"].resample(rule).max()
    l = df_window["low"].resample(rule).min()
    c = df_window["close"].resample(rule).last()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()
    return [
        {"t": ts.strftime("%Y-%m-%d %H:%M"),
         "o": round(float(r.open), 2), "h": round(float(r.high), 2),
         "l": round(float(r.low), 2), "c": round(float(r.close), 2)}
        for ts, r in out.iterrows()
    ]


def build(hist: pd.DataFrame, trades: pd.DataFrame,
          pre_days: int, post_days: int) -> list[dict]:
    out = []
    for _, t in trades.iterrows():
        entry = t["entry_time"]
        exit_t = t["exit_time"] if pd.notna(t["exit_time"]) else entry
        lo = entry - pd.Timedelta(days=pre_days)
        hi = exit_t + pd.Timedelta(days=post_days)
        win = hist.loc[lo:hi]
        if len(win) < 5:
            continue

        frames = {tf: resample(win, rule) for tf, rule in TF_RULES.items()}

        out.append({
            "id": t["trade_id"],
            "direction": t["direction"],
            "result": t["result"],
            "entry_time": entry.strftime("%Y-%m-%d %H:%M"),
            "exit_time": exit_t.strftime("%Y-%m-%d %H:%M"),
            "entry_price": round(float(t["entry_price"]), 2),
            "stop_price": round(float(t["stop_price"]), 2),
            "target_price": round(float(t["target_price"]), 2),
            "exit_price": round(float(t["exit_price"]), 2) if pd.notna(t["exit_price"]) else None,
            "swept_level": round(float(t["swept_level_price"]), 2) if pd.notna(t["swept_level_price"]) else None,
            "swept_source": t.get("swept_level_source", ""),
            "fvg_top": round(float(t["fvg_top"]), 2) if pd.notna(t["fvg_top"]) else None,
            "fvg_bottom": round(float(t["fvg_bottom"]), 2) if pd.notna(t["fvg_bottom"]) else None,
            "pnl": round(float(t["pnl"]), 2) if pd.notna(t["pnl"]) else 0.0,
            "r_gained": round(float(t["r_gained"]), 3) if pd.notna(t["r_gained"]) else 0.0,
            "frames": frames,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build multi-timeframe chart data for trades")
    ap.add_argument("--year", type=int, default=None, help="only trades whose entry is in this year")
    ap.add_argument("--result", choices=["WIN", "LOSS", "FLAT"], default=None)
    ap.add_argument("--direction", choices=["LONG", "SHORT"], default=None)
    ap.add_argument("--max", type=int, default=60, help="max number of trades to include (default 60)")
    ap.add_argument("--pre-days", type=int, default=7, help="days of context before entry")
    ap.add_argument("--post-days", type=int, default=2, help="days of context after exit")
    args = ap.parse_args()

    if not HIST.exists():
        print(f"No history at {HIST}. Run import-data first.")
        return 1
    if not TRADES.exists():
        print(f"No trade log at {TRADES}. Run `python run.py backtest` first.")
        return 1

    hist = load_history()
    trades = load_trades()

    if args.year:
        trades = trades[trades["entry_time"].dt.year == args.year]
    if args.result:
        trades = trades[trades["result"] == args.result]
    if args.direction:
        trades = trades[trades["direction"] == args.direction]

    # Keep the most recent N (most relevant), but show oldest->newest in the viewer.
    trades = trades.sort_values("entry_time").tail(args.max).reset_index(drop=True)
    if trades.empty:
        print("No trades match the filter.")
        return 1

    charts = build(hist, trades, args.pre_days, args.post_days)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "count": len(charts),
            "timeframes": list(TF_RULES.keys()),
            "pre_days": args.pre_days,
            "post_days": args.post_days,
            "filter": {"year": args.year, "result": args.result, "direction": args.direction},
        },
        "trades": charts,
    }
    OUT_JSON.write_text(json.dumps(payload))
    wins = sum(1 for c in charts if c["pnl"] > 0)
    print(f"Built chart data for {len(charts)} trades  (wins {wins}, losses {len(charts)-wins})")
    print(f"  Timeframes per trade: {', '.join(TF_RULES)}")
    print(f"  Wrote {OUT_JSON}  ({OUT_JSON.stat().st_size/1024:.0f} KB)")
    print("  Now run: python analysis/render_charts.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())