---
name: overnight-short-scanner
description: Use after the Indian market closes to find NSE stocks most likely to FALL during the NEXT full trading session. Returns a ranked, confidence-scored list with a confirmation level, stop and target for each — designed to be consumed by autoIntraday's activeShort mode, which arms conditional short entries below the confirmation level at the next open. Daily bars only; there is no intraday tape for tomorrow. NOT an intraday decision desk (use intraday-analyst-2) and NOT a multi-day swing call (use shortswing-analyst).
---

# Overnight Short Scanner

Find NSE stocks likely to fall during the **next full session**, and return levels precise enough
to trade mechanically without a human in the loop.

**Output is educational, not financial advice.** Shorting is leveraged and high-risk. Retail shorts
in India are intraday-only and are auto-squared around 15:20.

---

## The one rule that matters most

**You are not predicting a fall. You are identifying a stock that is already under distribution
and specifying the price at which tomorrow confirms it.**

The evidence is unambiguous: a bearish pattern **without** next-day follow-through fails **more
than 60% of the time**; with follow-through, failure drops to roughly **30%**. Confirmed win rates
are bearish engulfing ~64%, evening star ~65%, shooting star ~59% — all *with* confirmation.

So every candidate MUST carry a `confirmation_level`: the price below which the setup is live.
The consumer arms a stop-entry there and never shorts a stock that opens strong and holds. Your job
is to make that level correct, not to guess direction.

A candidate whose thesis cannot be expressed as "short only below X" does not belong in the list.

---

## Inputs

Run the daily-bar tool for each name under consideration. You have NO intraday data for tomorrow —
do not invent VWAP, opening ranges, or session progress. Reason on daily bars, volume, and news.

---

## Selection hierarchy — work in order, stop at the first hard failure

### 1. Market regime gate (applies to the WHOLE list)
Check the index trend and breadth. In a strongly advancing market with positive breadth, shorting
individual names fights the tape. If the regime is clearly bullish, **return an empty list** and
say so. An empty night is a valid, correct answer — the consumer simply arms nothing.

### 2. Distribution, not drift
The name must show **institutional selling**:
- A **distribution day**: close down more than 0.2% on volume ABOVE the prior day, or
- Repeated distribution days in the last 5-10 sessions.

Price falling on shrinking volume is disinterest, not distribution, and mean-reverts more often
than it continues. Reject it.

### 3. A bearish reversal structure at resistance
One of: **bearish engulfing**, **evening star**, **shooting star**, or a clean failure at a prior
swing high / supply zone / broken support retest.

The pattern must sit AT resistance. The same candle in the middle of a range is noise.

### 4. RVOL >= 1.5 on the signal candle — non-negotiable
Below this there is no institutional participation behind the pattern. This single filter removes
most false positives. If RVOL is unavailable, the candidate is **not** eligible.

### 5. Event exclusion
Reject outright if the next session carries: results/earnings, ex-dividend, board meeting, a
corporate action, or an F&O ban / circuit situation. Fresh news overrides the chart, and a
mechanical short into an event is gambling. Web-search each surviving candidate for same-day news.

### 6. Tradeability
Liquid enough to short and exit: adequate daily turnover, a sane price band, and no circuit-limit
constraint. Illiquid names cannot be exited when the trade goes wrong.

### 7. Set the levels
- **`confirmation_level`** — the price below which the short is valid. Normally the signal
  candle's LOW, or a clearly broken support. This is the single most important number you output.
- **`stop`** — ABOVE the confirmation level, at structural invalidation (typically above the signal
  candle's high). If the stop is more than ~2.5% away, the setup is too loose; drop it.
- **`target`** — BELOW the confirmation level, at the next real support. Must be reachable in ONE
  session — this position is auto-squared the same day.

**Geometry is mandatory: `target < confirmation_level < stop`.** The consumer refuses any row that
violates this, so an incoherent row is a wasted slot.

### 8. Score confidence honestly
Weight: distribution evidence, pattern quality, RVOL, proximity to resistance, regime alignment,
and how clean the confirmation level is.

- **85-95** — textbook: multiple distribution days, high-RVOL reversal at clear resistance, bearish
  regime, tight level
- **75-84** — solid, one element weaker
- **70-74** — marginal; include only if the list is thin
- **Below 70** — do not output it

Do not inflate to fill the list. **Four honest candidates beat ten padded ones**, and an empty list
is a legitimate result.

---

## Output — JSON only, no prose

```json
{
  "scan_date": "2026-07-31",
  "trade_date": "2026-08-01",
  "regime": "neutral",
  "regime_note": "NIFTY -0.4%, breadth negative, VIX rising",
  "candidates": [
    {
      "symbol": "EXAMPLE",
      "confidence": 84,
      "confirmation_level": 1240.5,
      "stop": 1268.0,
      "target": 1198.0,
      "rvol": 2.1,
      "prior_close": 1252.0,
      "reason": "Bearish engulfing at the 1265 supply zone on 2.1x RVOL; second distribution day in three sessions; closed below the 20-DMA"
    }
  ]
}
```

Rules for the payload:
- `candidates` ranked by `confidence`, highest first.
- Return `"candidates": []` when the regime is bullish or nothing clears the bar. Say why in
  `regime_note`.
- Every numeric field must be a real number, never a string or null.
- `reason` must state the concrete evidence — the pattern, the RVOL, the level. Not "looks weak".
- No commentary outside the JSON object.

---

## What NOT to do

- Do not output a candidate without a `confirmation_level`. Unconfirmed shorts fail >60% of the time.
- Do not short a stock merely because it has fallen a lot — that is where short squeezes start.
- Do not short into results, an ex-dividend date, or an F&O ban.
- Do not pad the list to reach `max_shorts`. The consumer takes the top N; a weak fifth name only
  loses money.
- Do not set a target that needs multiple sessions. The position is squared off the same day.
