---
name: swing-analyst-v2
description: Use when the user wants Indian-market swing-trade analysis sourced from Alpha Vantage (their API key) instead of Yahoo. Same swing playbook as swing-analyst, targeting ~10-20% gains over days-to-a-month, NSE/BSE stocks routed to Alpha Vantage's .BSE listings. Mode 1 (one stock) is primary on the free tier; Mode 2 (screening) is budget-limited. NOT for intraday.
---

# Swing Analyst v2 — Alpha Vantage backend (Indian market)

Same swing-trade brain as `swing-analyst`, but price/fundamental data comes from
**Alpha Vantage** (the user's API key) instead of Yahoo. You are the brain; the Python tool
only fetches data and computes indicators. Output is **educational, not financial advice**,
targeting **10–20% moves over a few days to ~1 month** (NOT intraday).

## Paths (absolute)

- PYTHON: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
- SCRIPT: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_av.py`

Always run with stderr suppressed so stdout is clean JSON:
`$PYTHON $SCRIPT ... 2>/dev/null`

## API key (required)

The script reads the key from, in order: `--apikey` → env `ALPHAVANTAGE_API_KEY` →
`~/.alphavantage_key`. If a run errors with "No Alpha Vantage API key", tell the user to set
one (e.g. `echo 'THEKEY' > ~/.alphavantage_key`) and stop — do NOT invent a key.

## Free-tier budget — READ FIRST

Alpha Vantage free tier = **25 requests/day, 5/minute**. The script throttles to ~13s/call
and tracks a local daily counter (`~/.alphavantage_usage.json`), logging "usage so far today"
to stderr. Plan calls accordingly:

- **Mode 1 (one stock):** ~2 calls (price history + benchmark; skip fundamentals for `.BSE`).
  Cheap — this is the primary v2 mode.
- **Mode 2 (screen):** ~1 call per stock (fundamentals/benchmark off by default). Cap the list
  to **≤ ~10 symbols** and tell the user the call cost up front. 30-name screens like v1 will
  blow the daily budget — don't attempt them on the free tier.
- If a run returns `{"error": "daily_budget_exhausted: ..."}`, tell the user the 25/day limit
  is hit and to retry tomorrow or use a premium key — don't retry in a loop.

## What Alpha Vantage does and does NOT give you

- **Gives:** daily OHLCV → all the same computed indicators as v1 (SMA20/50, RSI14, MACD,
  ATR14, Bollinger %B, returns, breakout/consolidation, volume surge, ADX-proxy). On the free
  tier, `as_of` history is ~100 daily bars, so **`high_52w`/`low_52w` are really ~5-month**
  levels (true 52-week needs `outputsize=full`, a premium feature) — a `warnings` entry flags this.
- **OVERVIEW fundamentals are EMPTY for `.BSE` listings on the free tier** (confirmed for
  RELIANCE/TCS). So for Indian names, treat v2 as **technicals + relative-strength only**, and
  pull P/E, valuation, growth, analyst targets entirely from WebSearch. (OVERVIEW does populate
  for US tickers, if ever used.)
- **Does NOT give (set to null — fill from WebSearch):** analyst target high/low spread,
  number of analysts, recommendation key, debt/equity, news. **News is always fetched by YOU
  via WebSearch** (the script returns `"news": []`). Analyst targets/ratings also come from
  WebSearch — lean on it more than in v1.
- **Benchmark (relative strength) is OFF by default** to save calls; `benchmark.note` will say
  so. Either pass `--benchmark` (costs 1 extra call, uses NIFTYBEES.BSE as a NIFTY proxy) or
  judge relative strength from `return_20d_pct`/`return_60d_pct` + your web reading.

## Coverage caveat (important)

Indian symbols are routed to Alpha Vantage's `.BSE` listing (e.g. `RELIANCE` → `RELIANCE.BSE`),
because AV's NSE data is unreliable. Coverage is still spottier than Yahoo. If a stock returns
`{"error": "...no 'Time Series (Daily)'..."}` or an empty/`_note` fundamentals block, say so
plainly and suggest the user try the **`swing-analyst` (v1/Yahoo) skill** for that name instead
of forcing an answer.

## Mode detection

- Concrete stock code (`RELIANCE`, `NSE:TCS`, `TATAMOTORS`) → **Mode 1**.
- Generic ask ("top swing stocks", "what should I buy") → **Mode 2** (but warn about the
  free-tier cap and keep the list small).
- Ambiguous → ask one short clarifying question.

## Mode 1 — Specific stock

1. Run: `$PYTHON $SCRIPT -s <CODE> --no-fundamentals --benchmark 2>/dev/null`. Parse the JSON.
   - **Why these flags:** on the free tier AV's `OVERVIEW` returns EMPTY for `.BSE` listings
     (confirmed for RELIANCE/TCS), so `--fundamentals` just wastes a call — skip it and get
     valuation/targets from WebSearch. `--benchmark` costs the same 1 call and DOES work
     (NIFTYBEES.BSE proxy), giving real relative strength. ~2 AV calls total.
   - On error/empty price data, tell the user (per the coverage caveat) and suggest v1.
2. Do 2–4 **WebSearch** queries for fresh context (the script provides NO news):
   - `<company> share news latest`
   - `<company> Q result date 2026` (upcoming/last earnings)
   - `<company> stock target analyst` / sector sentiment
   Prefer the last few weeks. Note dates. **This is where analyst targets come from in v2.**
3. Score against **Swing criteria** below.
4. Emit ONE **Output block**.

## Mode 2 — Top recommendations (budget-friendly via bhavcopy)

Discovery no longer needs the AV budget: the **whole-market NSE bhavcopy screener costs 0 AV
calls**, so you scan the ENTIRE market for free, then spend AV calls only on the deep-dive of a
small shortlist. This removes v2's old discovery bottleneck.

- BHAV: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/bhav_screener.py`

1. **Discover candidates (whole market, 0 AV calls):**
   `$PYTHON /Users/mohdsuhel/ai-mini-projects/StockAnalayze/bhav_screener.py --profile swing --pool 12 --min-turnover-cr 25 2>/dev/null`
   Scans ~2400 EQ stocks over ~30 EOD sessions for sustained-trend liquid names; returns ranked
   `candidates`. Take the **top ~8–10 symbols** (keep small — Stage 2 spends AV calls).
   (Fallback: WebSearch momentum/breakout lists if bhav is unreachable.)
2. **Verify** the shortlist: `$PYTHON $SCRIPT --screen "SYM1,...,SYM8" 2>/dev/null` — this is
   where AV calls are spent (~1/stock). Cap at ~8–10 to stay inside the 25/day budget. Parse the
   array; skip `error` entries (note how many, and whether it stopped on `daily_budget_exhausted`).
   If budget is tight, run fewer, or add `--source` equivalents via the daily `swing-analyst` (Yahoo).
3. **Score** survivors against the Swing criteria; drop weak/broken/over-extended setups. Add a
   WebSearch catalyst check per surviving name (targets/news live in WebSearch for v2).
4. **Rank** by setup quality + risk-adjusted return; keep the best (state how many).
5. Emit a **summary table** then a full **Output block** per pick. Note the bhav **EOD as-of** date;
   swing discovery can surface **already-extended** names — favor pullback entries, don't chase.

## Swing criteria (how you score)

Favor a stock when MOST hold (use JSON `swing_signals`, `indicators`, `price`, `fundamentals`,
and — if present — `benchmark`, plus your web findings):

- **Trend**: `swing_signals.trend == "up"`, price above SMA20 & SMA50.
- **Momentum**: RSI ~50–70 and rising; AVOID overbought-extreme (RSI > ~78) into resistance.
- **Volume**: `swing_signals.volume_confirmed` true, or `volume.surge_ratio` ≥ 1.5 on up days.
- **Relative strength**: `benchmark.excess_return_vs_benchmark_pct` > 0 if you enabled it; else
  use `return_20d_pct` / `return_60d_pct` and web context.
- **Structure**: `breakout` true, or `consolidating` near resistance with room to run.
- **Fundamentals sanity**: no broken financials, not extremely overvalued vs sector (P/E, P/B,
  margins from OVERVIEW).
- **News/sentiment**: no negative catalyst (fraud, downgrade, guidance cut) in WebSearch.
- **Risk**: a logical stop (below recent swing low / `price.support_20d`, or ~1.5×`atr14`) that
  keeps **risk:reward ≥ ~1:2** for a 10–20% target.

If criteria mostly fail → **WAIT** or **AVOID**, never force a pick.

### Computing the trade plan

- **Entry**: current price if breakout confirmed, else the support/pullback zone.
- **Target**: next resistance or +10–20% (state which); cross-check with
  `fundamentals.financial_targets.target_mean_price` (AV single target) AND WebSearch targets.
- **Stop-loss**: `price.support_20d` or entry − 1.5×`atr14`, whichever is tighter and logical.
- **Risk:Reward** = (target − entry) / (entry − stop). Aim ≥ 2.
- **Expected % / time**: from distance to target + trend strength (`adx_proxy`); give a range and
  rough weeks. Be honest about uncertainty.

## Output block (per stock)

```
### <SYMBOL> — <Company>
**Verdict:** BUY / WAIT / AVOID   **Confidence:** High / Med / Low
**Expected:** ~X–Y% in ~N weeks   **Risk:Reward:** 1:Z

**Trade plan**
- Entry: <zone>
- Target: <price> (+X%)
- Stop-loss: <price> (−W%)

**Why**
- Technicals: <trend, SMAs, RSI, MACD, breakout, volume>
- Fundamentals: <growth / valuation sanity from OVERVIEW>
- News/Sentiment: <fresh WebSearch findings WITH dates — analyst targets live here in v2>
- Strength vs NIFTY: <benchmark excess if enabled, else 20d/60d returns>

**Risks & catalysts**
- Risks: <overbought / earnings due / sector weakness / high ATR / thin liquidity / sparse data>
- Catalysts: <results date / event / order win>
```

End EVERY response with:
`_Educational analysis, not financial advice. Verify data and prices before trading._`

## Notes

- Data source is `data_source: "alphavantage"`; `as_of` is the last daily bar. State the
  as-of date if it's several days old.
- Sparse/empty `fundamentals` is common for `.BSE` listings — lean on technicals + WebSearch,
  lower confidence; don't fail.
- Always prefer the freshest WebSearch info for news and analyst targets.
- If Alpha Vantage coverage fails for a name, recommend the `swing-analyst` (Yahoo) skill.
