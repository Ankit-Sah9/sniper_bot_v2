# Modified ICT 2022 Model — Strategy Specification (v1)

**Purpose:** Implementation specification for an automated/backtestable trading strategy based on a modified version of the ICT 2022 Mentorship Model.
**Audience:** The developer building the algorithm. Every rule below is intended to be unambiguous and directly codeable.
**Status:** v1. Items marked **[CODER DECISION]** have a recommended default chosen; they may be overridden. Items marked **[OPEN]** are deferred to a later version.

---

## 1. Summary

This is an intraday, single-instrument, single-timeframe strategy. On a qualifying day, the algorithm waits for price to sweep a known liquidity level, confirm a reversal via a Market Structure Shift (MSS) that leaves a Fair Value Gap (FVG), and then enters on a retrace into that FVG — but only in the direction permitted by a mechanical daily bias, and only if the entry fills inside the New York morning window.

The full trade chain, in order:

> **Sweep** (price takes a liquidity level by ≥ $3) → **Reversal** (marks the sweep extreme) → **Fractal swing forms** on the reversal side (N=2) → **MSS** (a 5-min candle closes beyond/at that swing in the bias direction) → **Displacement** (the breaking leg must leave an FVG) → **FVG selection** (first FVG in the discount/premium half of the displacement leg) → **Entry** (near edge of that FVG, first touch, within 08:30–11:00 ET, bias-aligned) → **Stop** ($5 beyond sweep extreme) → **Target** (fixed 1:3 R:R) → **Exit** (runs to TP/SL, no time exit).

One trade per day maximum.

---

## 2. Instrument & Data

| Parameter | Value |
|---|---|
| Instrument | NASDAQ 100 (NQ) |
| Feed type | CFD / index feed |
| Price units | US dollars ($) — all thresholds are in dollar terms |
| Primary timeframe | 5-minute candles |
| Bias reference timeframe | 5-minute (for the midnight open candle) |

**Timezone:** All times in this document are **New York time (America/New_York), DST-aware.** Source data may be timestamped in UTC or broker-server time; it must be converted to America/New_York with correct daylight-saving handling before any session logic runs. (DST mis-mapping is the most common silent backtesting error.)

---

## 3. Trading Window

| Item | Rule |
|---|---|
| Entry window | **08:30–11:00 ET** |
| Sweep timing | The liquidity sweep **may occur before 08:30**. It does not need to be inside the window. |
| MSS / displacement timing | May occur before or during the window, as long as the entry fill lands inside it. |
| Entry fill | **Must occur within 08:30–11:00 ET.** A retrace/fill at or after 11:00 does not qualify. |
| Trades per day | **Maximum 1.** Once a trade triggers (fills), no further setups are considered that day, regardless of outcome. |

This window corresponds to the NY AM / Silver Bullet session for index trading.

---

## 4. Daily Bias (mechanical filter)

Bias is a **hard filter**: only setups aligned with the current bias may be taken. Counter-bias setups are vetoed entirely.

| Item | Rule |
|---|---|
| Reference | **New York Midnight Open** = the open price of the 00:00 ET 5-minute candle. |
| Bullish bias | Price is **above** the midnight open (by more than the neutral buffer) → **longs only**. |
| Bearish bias | Price is **below** the midnight open (by more than the neutral buffer) → **shorts only**. |
| Neutral buffer | If price is **within ±$10** of the midnight open → **no bias → no trade**. |
| Evaluation timing | **Continuous.** Bias is evaluated at the moment each setup completes (i.e. at the entry fill). A setup completing at 09:45 uses price-vs-midnight-open as of 09:45. |

**Interaction note:** Because bias is checked continuously at setup completion, a setup is only valid if, at the moment of entry, price is clearly above/below the midnight open (outside the ±$10 buffer). This naturally rejects setups forming while price chops around the open.

**Bias vs. sweep direction:** Direction is proposed by the sweep (a swept high → look for shorts; a swept low → look for longs). Bias then acts as a veto: if the proposed direction conflicts with the bias, the setup is discarded. Both must agree.

> **Note:** This bias rule is explicitly a v1 placeholder. It is expected to be refined/replaced later. Keep it isolated/modular in code so it can be swapped.

---

## 5. Liquidity Levels & The Sweep

### 5.1 Liquidity reference levels

At any time, the following levels are considered "live" liquidity that can be swept:

- **Session highs/lows** — Asian, London, and prior NY session highs and lows.
- **Previous Day** high and low.
- **Previous Week** high and low.

Any one of these being taken out can initiate a setup.

> **[CODER DECISION — level set]** "All sessions" must be enumerated precisely with their time boundaries (see §10 for the open items list). For v1, define each session's start/end in ET and compute its high/low from the candles within it. Recommended starting definitions to confirm: Asian 20:00–00:00 ET, London 02:00–05:00 ET, prior NY 08:30–16:00 ET of the previous day.

### 5.2 Sweep definition

| Item | Rule |
|---|---|
| Penetration | Price must trade **≥ $3 beyond** the level (through it). |
| Close-back requirement | **None.** The sweep candle does **not** need to close back on the other side. The MSS provides confirmation instead. |
| Sweep extreme | The **most extreme price reached** during the sweep before reversal: the highest high if a high was swept, the lowest low if a low was swept. This point is used for stop placement and for defining the reversal swing. |
| Staleness | **None.** A swept level remains valid until the setup completes or the window closes. There is no time limit between sweep and MSS. |

---

## 6. Market Structure Shift (MSS)

The MSS confirms the reversal after the sweep.

| Item | Rule |
|---|---|
| Swing identification | **Fractal rule, N=2** (Williams-style): a swing high is a candle whose high exceeds the highs of the 2 candles on each side; a swing low is the mirror. |
| Which swing must break | **(b) The specific reversal swing created by the sweep** — not just any nearby swing. After price sweeps the level and reverses off the sweep extreme, a fractal swing (N=2) forms on the reversal side. The MSS is the break of *that* swing. |
| Break condition | A **5-minute candle that closes beyond or exactly at** that fractal swing point, in the bias direction. (Close-based, not wick-based — reduces noise.) |
| Direction | Must be in the **bias direction** and opposite to the sweep (sweep a high → MSS to the downside for a short; sweep a low → MSS to the upside for a long). |

### Precise construction (two reference points)

1. **Sweep extreme** — set when price penetrates the level by ≥ $3 and then reverses (highest high / lowest low reached).
2. **Reversal swing** — the first N=2 fractal swing that forms on the reversal side after the sweep extreme.
3. **MSS trigger** — the first 5-min candle that **closes beyond/at** the reversal swing, in the bias direction.

> **[CODER NOTE]** A clear diagram should accompany the implementation. The chain is: sweep level → sweep extreme → reversal swing forms → 5-min close past reversal swing = MSS. The displacement requirement (§7) must also be satisfied by the breaking leg.

---

## 7. Displacement & FVG

### 7.1 Displacement definition

Displacement is defined **by the FVG**: the leg that breaks structure (the MSS move) **must leave at least one Fair Value Gap.** If the MSS-breaking leg leaves no FVG, there is no valid displacement and no setup.

### 7.2 Fair Value Gap (FVG)

An FVG is a 3-candle imbalance:

- **Bullish FVG:** a gap between the **high of candle 1** and the **low of candle 3** (the gap exists when candle 1's high < candle 3's low). The gap zone is [candle 1 high, candle 3 low].
- **Bearish FVG:** a gap between the **low of candle 1** and the **high of candle 3** (the gap exists when candle 1's low > candle 3's high). The gap zone is [candle 3 high, candle 1 low].

Only FVGs created **within the displacement (MSS-breaking) leg** are eligible.

### 7.3 FVG selection (which FVG to enter)

If the displacement leg leaves more than one FVG, select using a discount/premium filter built on the displacement leg range:

1. Compute the **50% level** of the **displacement leg** (Fibonacci 50% between the start of the displacement and its extreme).
2. **Bullish (long):** consider only FVGs whose **near edge sits below the 50% level** (discount half). Select the **first one price retraces into**.
3. **Bearish (short):** consider only FVGs whose **near edge sits above the 50% level** (premium half). Select the **first one price retraces into**.

> **[CODER NOTE — straddle case]** If an FVG straddles the 50% line, it qualifies based on whether its **near edge** (the edge price touches first on the retrace) is in the correct half (discount for long, premium for short).

---

## 8. Entry, Stop, Target

| Item | Rule |
|---|---|
| **Entry** | At the **near edge** of the selected FVG, on **first touch** (the first time price retraces to that edge). |
| Entry window check | The fill must occur within **08:30–11:00 ET** (§3). |
| Bias check | Bias must be valid and aligned at the moment of fill (§4). |
| **Stop loss** | **$5 beyond the sweep extreme** (below it for a long, above it for a short). |
| **Take profit** | **Fixed 1:3 R:R.** Target distance = 3 × (entry-to-stop distance). Long target = entry + 3 × risk; short target = entry − 3 × risk. |
| Partials / scaling | **None.** Single take-profit, all-out. |
| **No-retrace rule** | If price never retraces into a qualifying FVG before 11:00 ET, the setup **expires unfilled — no trade.** |
| **Time exit** | **None.** Once filled, the trade runs to TP or SL regardless of the clock. A trade entered near 11:00 may remain open well past the window. The backtest must keep evaluating bars after 11:00 until TP or SL is hit. |

> **[OPEN — v2]** Session-close force-flat as a safety net for trades still open at the daily close. Not implemented in v1.

---

## 9. News Filter

| Event | Rule | Applies to |
|---|---|---|
| **NFP** | **No trading at all** on NFP days. | Live only |
| **CPI** | On weeks containing a CPI release, **no trading until after CPI is released.** Trading resumes for the remainder of the week after the print. | Live only |
| **FOMC / rates** | Excluded **automatically by timing** — the release generally falls outside the 08:30–11:00 window, so no special rule is needed. | Live only |

**Backtest behavior:** The backtest **does not use any news data.** NFP/CPI/FOMC filters are **live-only.**

> **Important consequence:** Because the backtest ignores news, it will include NFP-day and pre-CPI trades that would be skipped in live trading. **Live and backtest performance will therefore not be directly comparable.** This is an accepted v1 tradeoff. (A future version may add a historical economic-calendar source so the backtest can mirror the live filter.)

---

## 10. Backtest Mechanics

| Item | Rule |
|---|---|
| Bar granularity | **5-minute only.** No finer timeframe is used. |
| Midnight open | Open of the **00:00 ET 5-minute candle.** |
| Fill — entry | Filled when price **touches** the near edge of the selected FVG. |
| Fill — stop/target | Filled when price **trades through** the level. |
| **Slippage** | **$1–$3.** See coder decision below. |
| **Intra-bar ambiguity** | When both stop and target fall within the **same 5-min bar**, the bar cannot tell which was hit first. **Default assumption: stop hit first** (pessimistic — avoids overstating performance). |

> **[CODER DECISION — slippage]** "$1–$3" is a range; the backtest needs one deterministic rule. **Default:** apply a **fixed $2** slippage *against* the position. Applied to **entry** (worse fill) and to **stop** (worse fill). **Target fills assume no slippage** (modelled as a resting limit order). Asymmetry is intentional and realistic. Override options: random draw in [$1, $3], or apply to all fills.

> **[CODER DECISION — intra-bar]** Default "stop first" may be replaced with a 1-min confirmation pass later, but v1 accepts the 5-min ambiguity with the pessimistic assumption.

---

## 11. Parameter Table (single source of truth)

All tunable values in one place so the developer can expose them as config.

| Parameter | v1 Value | Notes |
|---|---|---|
| Instrument | NQ (CFD/index) | |
| Entry timeframe | 5 min | |
| Entry window | 08:30–11:00 ET | |
| Bias reference | NY midnight open (00:00 ET 5-min candle open) | |
| Bias neutral buffer | ±$10 | tunable |
| Bias evaluation | continuous (at setup completion) | |
| Sweep penetration | $3 | tunable |
| Sweep close-back | not required | |
| Sweep staleness | none | |
| Fractal N | 2 | tunable |
| MSS break | 5-min close beyond/at reversal swing | |
| MSS swing target | the sweep's own reversal swing | |
| Displacement | must leave an FVG | |
| FVG selection | first FVG in discount/premium half via 50% fib of displacement leg | |
| Entry point | near edge of FVG, first touch | |
| Stop | $5 beyond sweep extreme | tunable |
| Risk:Reward | 1:3 (fixed) | tunable |
| Partials | none | |
| Time exit | none | |
| Trades per day | 1 | |
| Slippage | $2 fixed against position (entry+stop) | [CODER DECISION] |
| Intra-bar tie | stop hit first | [CODER DECISION] |
| News filter | live-only (NFP off; CPI after release; FOMC by timing) | backtest ignores |

---

## 12. Open Items / Deferred

1. **[OPEN]** Precise session boundary definitions for the "all sessions" liquidity set (Asian/London/prior NY start–end in ET). Confirm before coding §5.1.
2. **[OPEN — v2]** Session-close force-flat safety net (§8).
3. **[OPEN — v2]** Historical economic-calendar source so the backtest can mirror the live news filter (§9).
4. **[FUTURE]** Bias rule refinement/replacement (§4) — keep modular.
5. **[FUTURE]** Optional re-introduction of Order Blocks / OTE as confluence filters (dropped in v1).
6. **[FUTURE]** Test entry timeframe 5 vs 15 min once v1 is stable (entry timeframe should be a single config parameter).

---

*End of specification v1.*
