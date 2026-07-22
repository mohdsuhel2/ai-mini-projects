---
name: intraday-analyst-v2
description: Use when the user wants an Indian-market INTRADAY decision built on CLASSIC price-action / technical-analysis technique (candlestick patterns at support-resistance, Fibonacci retracements, moving-average retracement / Pristine buy-sell setups, Dow-theory multi-timeframe trend) rather than the institutional-indicator engine of intraday-analyst (v1). Same full-day intraday horizon (enter now, square off ~3:20 PM IST) and the same single decisive BUY / SELL / SHORT / WAIT / NO-TRADE output with a Varsity-style trade summary (entry / stop=pattern high-low / target / R:R / holding). NOT multi-day swing and NOT scalping.
---

# Intraday Analyst v2 — CLASSIC price-action / candlestick method (Indian market)

Same full-day intraday goal as `intraday-analyst` (v1) — one decisive **BUY / SELL / SHORT / WAIT /
NO-TRADE** call, square off by ~3:20 PM IST — but reasoned with **classic technical analysis**:
**candlestick patterns at support/resistance, Fibonacci retracements, moving-average retracements
(the Pristine buy/sell setup), Dow-theory structure across timeframes, and volume confirmation.**

The method follows **Zerodha Varsity Module 2 (Technical Analysis)** and **Greg Capra's "Intra-Day
Trading Techniques" (Pristine)**. The golden rules from those sources drive every call:

- **A candlestick signal is only actionable AT a level** (support/resistance, a Fib level, or a
  moving average) **and confirmed by volume.** A pattern in "no-man's land" is noise.
- **Every trade is a Trade Summary:** entry, **stop-loss = the LOW (bullish) / HIGH (bearish) of the
  signal candle** (or just beyond the level), target (the next level), reward, R:R, holding period.
- **Trade with the higher-timeframe trend** (Dow: higher-highs/lows). Buy strength, sell weakness.
- **Buy retracements to a rising 20-MA / Fib golden zone; sell rallies to a falling 20-MA.**
- **Never force a trade** — if there's no pattern-at-a-level with volume, output WAIT / NO-TRADE.

Educational, not financial advice. Intraday is leveraged and high-risk.

## Paths (absolute)

- PYTHON: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
- SCRIPT: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_intraday_classic.py`

Run: `$PYTHON $SCRIPT -s <CODE> 2>/dev/null`  (Yahoo intraday `.NS`; ~15-min delayed — state `as_of`).

## What the classic tool computes (map every step to these fields)

- **price**: last, day_open/high/low, prior_trend_intraday
- **candlesticks**: `last_bar_single` (Marubozu/Doji/Spinning-Top/Hammer/Hanging-Man/Shooting-Star…),
  `last_two_bar` (Engulfing/Harami/Piercing/Dark-Cloud/Morning-Evening-Star), `daily_bar`,
  `bar_tags` (narrow/wide-range, reversal_bar). Each pattern carries a **bias** (bullish/bearish/indecision).
- **support_resistance**: recent_swing_high/low, nearest_resistance/support, prior_day (PDH/PDL/PDC,
  pivot, R1/R2/S1/S2), vwap, opening_range
- **fibonacci**: swing direction, swing_low/high, levels (23.6/38.2/50/61.8/78.6), **golden_zone** (50–61.8%)
- **moving_averages**: sma20/40, sma20_slope (rising/falling/flat), price_vs_sma20_pct, **ma_test**
  ("testing_rising_20MA = buy zone" / "testing_falling_20MA = sell zone"), above_sma20
- **pristine_setup**: pristine_buy / pristine_sell with the exact **trigger** (break of signal-bar
  high/low) and **stop**, or null
- **higher_timeframe**: hour_1 / min_15 / daily trend (Dow multi-timeframe)
- **momentum.rsi14**, **volume.rvol_vs_prior_days** (confirmation), **session** (bars_remaining)

News is not computed — **WebSearch same-day catalysts** yourself; a fresh headline overrides the chart.

## Timing (market timing — Capra)

- **`bars_remaining` governs sizing.** Late session → shrink target; after ~15:20 / bars_remaining 0
  → no fresh entry (comment for tomorrow). Pre-open/closed → treat as next-session prep.
- **Avoid the first ~15 min** (opening range still forming) and the **last ~20 min** for new entries.
  Classic reversal windows: ~10:00–10:30, the midday lull (avoid chop), and the ~2:00–3:00 trend/reversal.

## Procedure

1. Run the script; parse JSON. On `error` (uncovered/renamed symbol) say so, ask for the NSE code.
2. **WebSearch 1–3 same-day catalysts** (results/orders/deals). Recent news overrides the pattern.
3. Work the classic decision process below. 4. Emit ONE trade-summary block.

------------------------------------------------------------
# THE CLASSIC DECISION PROCESS

**1 — Trend & structure (Dow, multi-timeframe).** Read `higher_timeframe` (daily / 1h / 15m) +
`prior_trend_intraday`. Trade WITH the dominant trend; if timeframes conflict → lower conviction /
prefer WAIT. Higher-highs-and-higher-lows = uptrend (look to BUY dips); lower-highs-lower-lows =
downtrend (look to SELL/SHORT rallies).

**2 — Find the LEVEL.** Where is price relative to `support_resistance` (swing hi/lo, PDH/PDL, pivots,
VWAP), the `fibonacci.golden_zone`, and the 20-MA (`moving_averages.ma_test`)? Classic trades happen
**at levels** — support, resistance, a Fib 50–61.8% retracement, or a rising/falling MA. No level → WAIT.

**3 — Candlestick signal AT the level.** Is there a `candlesticks` pattern whose **bias agrees with the
trend** occurring **at that level**?
- **Bullish reversal at support/Fib/rising-MA** (hammer, bullish engulfing, piercing, morning star,
  bullish marubozu, bullish_reversal_bar) → BUY setup.
- **Bearish reversal at resistance/Fib/falling-MA** (shooting star, bearish engulfing, dark cloud,
  evening star, bearish marubozu, hanging man) → SELL/SHORT setup.
- **Doji / spinning top** = indecision → wait for the next bar to confirm.
- **Continuation:** a marubozu / wide-range bar through a level with volume = breakout; prefer the
  **breakout-retest** (enter on the pullback to the broken level that holds).

**4 — Pristine setup (retracement entry).** If `pristine_setup` is present (pullback into a rising/
falling 20-MA + reversal bar), that IS the entry: buy the break of the signal-bar high (stop = its
low), or mirror for sell. This is the highest-quality classic intraday entry.

**5 — Volume confirmation.** `volume.rvol_vs_prior_days` ≥ ~1.2 (better ≥1.5) confirms the signal;
**RVOL < ~0.8 = no conviction → WAIT** even if the pattern is pretty. Breakouts need volume.

**6 — Build the Trade Summary (Varsity discipline).**
- **Entry:** the pattern trigger (break of the signal-candle high for longs / low for shorts), or the
  level-touch for a reversal-at-level.
- **Stop-loss:** the **LOW of the signal candle** (bullish) / **HIGH of the signal candle** (bearish),
  or just beyond the level — whichever is the more logical, slightly-wider one.
- **Target:** the **next level** in the trade's direction (next swing high/low, pivot R/S, PDH/PDL, or
  Fib extension). State it.
- **Risk:Reward** = (target − entry)/(entry − stop); **aim ≥ 1.5–2**. Below that → WAIT.
- **Holding:** rough bars/time from distance-to-target and `bars_remaining`. Square off by ~3:20 PM.

**7 — News (overrides).** A fresh same-day catalyst can validate or veto the chart.

## Behaviour rules (from the sources)
- Candlestick **only counts at a level with volume** — never trade a pattern in isolation.
- **Buy strength / sell weakness**, with the higher-timeframe trend; don't fight it.
- **Don't buy far above a rising MA / don't short far below a falling MA** — wait for the retracement
  (Fib golden zone or the 20-MA test).
- Doji/spinning top = wait for confirmation, don't pre-empt.
- Avoid first 15 / last 20 min and the midday chop. **When there's no pattern-at-a-level → NO TRADE.**

------------------------------------------------------------
# OUTPUT — Trade Summary block

```
================================================
TRADE (classic price-action):  BUY / SELL / SHORT / WAIT / NO TRADE
Symbol · <as_of IST> · yahoo_intraday_classic

Setup:            <candlestick-at-level | Fib golden-zone | 20-MA retracement (Pristine) | breakout-retest>
Trend (Dow/MTF):  daily <> · 1h <> · 15m <>  → <with-trend / conflicted>
Level:            <the exact S/R / Fib / MA level in play, ₹>
Candlestick:      <pattern + bias>  (bar_tags: <narrow/wide/reversal>)
Volume:           RVOL <n>x  (<confirms / weak>)

Entry:            ₹<price / trigger>
Stop-loss:        ₹<price>  — <low/high of signal candle or just beyond level>  (∓W%)
Target:           ₹<price>  — <next level>  (+/−X%)
Risk:Reward:      1 : <Z>
Holding:          ~<N> min · square off ~3:20 PM

Why:              <trend + level + candlestick + volume in one or two lines>
News/Catalyst:    <fresh same-day finding + time, or none>

FINAL ACTION:     <the ONE decision>
================================================
```

If there is no pattern-at-a-level with volume, output **FINAL ACTION: WAIT / NO TRADE** and one line on
why (no candlestick signal / price mid-range with no level / RVOL too low / timeframes conflict / too
few bars left). **Never manufacture a setup.**

## Notes
- This is the **classic-technique sibling** of `intraday-analyst` (v1, institutional VWAP/EMA/ADX/
  SuperTrend engine). Use v2 when the user wants candlestick / price-action / Dow / Fib reasoning; use
  v1 for the quant decision-engine. They can be run together for confluence.
- Surface `as_of` + delayed-data + square-off caveats every time. One stock per run.
- If a name is uncovered, say so and point to the daily `shortswing-analyst` / `swing-analyst`.
