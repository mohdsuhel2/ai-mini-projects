# Intraday Analyst — design

_2026-07-01. Educational tool, not financial advice._

## Goal

A third analysis mode (alongside `swing-analyst` v1/Yahoo and `swing-analyst-v2`/Alpha Vantage)
for **intraday trading with a full-day horizon** — you ask about one stock around, say, 12–1 PM
IST and it tells you: UP / DOWN / NO-TRADE bias for the **rest of today's session**, a stop-loss,
an expected move, and a target — to be squared off by ~3:20 PM. NOT scalping (few-minute) and NOT
multi-day swing.

## Decisions (locked)

- **Bar interval:** 15-minute.
- **Session window:** usable any time in the 09:15–15:30 IST session; adapts to time remaining.
- **Stop-loss:** structure + ATR blend — nearest real level (VWAP / opening-range edge / prior-day
  pivot / PDH-PDL) but never tighter than ~1× the 15-min ATR.
- **Data:** Alpha Vantage `TIME_SERIES_INTRADAY` first; fall back to Yahoo intraday on
  `daily_budget_exhausted` OR empty/insufficient AV bars (AV intraday for `.BSE` is expected to be
  spotty, so Yahoo will often be the workhorse). Both feeds may be ~15 min delayed — output states
  the exact bar timestamp.
- **Scope:** single-stock only (Mode 1). No multi-stock screening in v1.

## Components

- **`stock_analyze_intraday.py`** — fetch + compute only; prints clean JSON. Reuses v1 pure math
  (`sma`, `wilder_atr`, `rsi_simple`, `macd_bollinger_pack`, `OHLCVBar`) and v2 AV plumbing
  (`resolve_av_symbol`, `av_get`, `resolve_apikey`, budget errors).
- **`intraday-analyst` skill** — runs the script, adds same-day WebSearch catalysts, scores the
  bias, computes the trade plan, emits the output block.

## Facts the script computes (15-min bars, today + prior days)

VWAP (session), opening range (first 30 min) + breakout flags, prior-day PDH/PDL/PDC + floor
pivots (P, R1/R2, S1/S2), gap %, session hi/lo + position-in-range, 15-min RSI14 / MACD / ATR14,
RVOL (cumulative vol vs same-time prior-day average), nearest support/resistance from the level
stack, and session progress (minutes elapsed / to 15:20 square-off, bars remaining, ATR-projected
remaining move).

## The brain (skill)

Bias UP / DOWN / **NO-TRADE** (first-class — chop = stay out) from VWAP side, ORB, gap behavior,
pivots/PDH-PDL, 15-min RSI/MACD, RVOL confluence. Entry = trigger level or "now". Stop = structure
+ ATR blend. Target = next level in direction, capped by ATR projection over remaining bars. R:R
shown, aim ≥ ~1.5. Always: square-off-by-3:20 reminder + leverage/high-risk + delayed-data caveats.

## Output block

```
### <SYMBOL> — <Company>  (intraday, as-of <bar ts IST>)
**Direction:** UP / DOWN / NO-TRADE   **Confidence:** High/Med/Low
**Expected (rest of session):** ~X–Y%   **Risk:Reward:** 1:Z   **Square off by ~3:20 PM**
Trade plan (entry/trigger, target, stop) · Why (VWAP/ORB/pivots/gap/RSI-MACD/RVOL) ·
News/Sentiment (same-day) · Risks (chop/whipsaw/low RVOL/data delay/time-left)
```
