# Build Plan — Old "Sniper" Bot → New NQ Modified ICT 2022 Model

This document records how the prior **Sniper MT5 bot** (Gold / weekly-bias /
H1-H4-D1-W1 / 5-phase / AI-scored / TTPS) relates to this new project, so your
developer understands what was carried over and what was deliberately left
behind.

## TL;DR

The old bot and the new model are **different strategies**. We **did not modify**
the old strategy logic. We kept its *infrastructure patterns* and **rebuilt the
strategy clean** against the v1 spec. This avoided dragging in Gold pip-sizing,
the H1 timeframe baked throughout, the 5-phase scaffolding, the AI-bias inputs,
and the TTPS scoring — none of which belong in the new model.

---

## Reuse / Rewrite / Drop map

| Old project element | Decision | Where it lives now / why |
|---|---|---|
| **DST-aware UTC→NY conversion** (`backtest.utc_to_ny`) | **Reuse pattern** | `engine/data_loader.py` uses `zoneinfo` to convert any feed to NY, DST-correct. |
| **Session-window check** (`is_valid_trading_time`, "entry must be before 11:00") | **Reuse pattern** | `engine/strategy.in_entry_window` + window-on-fill check in `backtest.py`. |
| **News classification + `get_cpi_this_week`** | **Reuse pattern (live only)** | Documented as a live-only filter in the spec; the backtest ignores news by design. Not yet wired (v2 item). |
| **Walk-forward + metrics** | **Reuse pattern** | `engine/backtest.walk_forward` + `compute_metrics`. Simplified to fixed-parameter stability folds. |
| **Risk model / position sizing** (`risk_manager`) | **Reuse concept** | Collapsed into `config.account_balance` + `risk_per_trade`; R-based P&L in `backtest.py`. |
| **FVG dataclass + detection** (`FVGs.detect_fvgs`) | **Reuse concept, rewritten** | `engine/indicators.detect_fvgs_in_range` — same 3-candle definition, rewritten for 5-min and the displacement-leg constraint. |
| **Trade logging / CSV output** | **Reuse pattern** | `engine/backtest.write_outputs`. |
| **MT5 order plumbing** (`place_mt5_order`) | **Dropped (v1)** | Backtest + dashboard only, per scope. Re-add behind a live module later. |
| **5-phase architecture** (Phase 1–5) | **Dropped** | Replaced by a single linear chain in `engine/strategy.find_setup_at`. |
| **Weekly bias engine + AI bias** | **Dropped** | Replaced by mechanical midnight-open bias (`evaluate_bias`). |
| **TTPS 20-point master score** | **Dropped** | No confluence scoring in v1; the chain is pass/fail. |
| **Order Blocks / OTE** | **Dropped** | v1 is FVG-only by design. |
| **Gold (XAUUSD) calibration** | **Dropped** | New model is NQ, dollar terms, different thresholds. |
| **H1/H4/D1/W1 timeframes** | **Dropped** | New model is single-timeframe 5-min. |

---

## What is genuinely new (no analog in the old bot)

- **Mechanical midnight-open bias** with ±$10 neutral buffer, evaluated
  continuously at fill time (`strategy.evaluate_bias`).
- **The strict causal chain**: sweep extreme → reversal fractal (N=2) → MSS as a
  *close beyond that specific swing* → displacement defined by leaving an FVG.
- **Discount/premium FVG selection** via the 50% fib of the displacement leg.
- **Near-edge, first-touch** entry with $5-beyond-sweep stop and fixed 1:3 target.
- **No-look-ahead discipline**: fractals are only confirmed once N candles to the
  right exist (`indicators.confirmed_swings_as_of`), and liquidity levels are
  built only from closed prior periods.

---

## Known v1 simplifications (flagged for your developer)

1. **Session boundaries** for the "all sessions" liquidity set are defaults in
   `config.py` (Asian 20:00–00:00, London 02:00–05:00, NY 08:30–16:00). Confirm
   against your broker's session definitions.
2. **News filter** is live-only and not yet implemented; the backtest never
   blocks. Add a historical economic-calendar source to mirror it (v2).
3. **Sweep selection**: when multiple levels are swept in a morning,
   `find_setup_at` uses the most recent sweep scanning back from the MSS candle.
   If you want priority rules (e.g. prev-week over session), add them in
   `strategy.find_setup_at` step 1.
4. **Intra-bar resolution**: 5-min only; stop-first on same-bar ties. A 1-min
   confirmation pass would refine win/loss attribution.
5. **Slippage**: fixed $2 against entry+stop, none on target. Swap for a
   random draw or per-fill model in `config.py` + `backtest.py`.

---

## Suggested build order if extending toward live

1. Validate on **real 5-min NQ data** (replace the synthetic file).
2. Add the **live news filter** (NFP off, CPI-gated) using a calendar source.
3. Re-introduce a **live execution module** (port the old `place_mt5_order`),
   keeping the backtest as the source of truth for the strategy logic.
4. Make **entry timeframe configurable** to A/B 5-min vs 15-min.
