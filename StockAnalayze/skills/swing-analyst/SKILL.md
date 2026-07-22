---
name: swing-analyst
description: Use when the user wants Indian-market swing-trade analysis or stock recommendations targeting ~10-20% gains over days-to-a-month. Two modes - analyze a specific NSE/BSE stock by code, or recommend the top swing candidates. NOT for intraday.
---

# Swing Analyst (Indian market)

Produce swing-trade analysis for Indian stocks, targeting **10–20% moves over a few
days to ~1 month**. NOT intraday. Output is **educational, not financial advice**.

You are the brain. The Python tool only fetches data — you do the reasoning and add live
web context.

## Paths (absolute)

- PYTHON: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
- SCRIPT: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze.py`

Always run with stderr suppressed so stdout is clean JSON:
`$PYTHON $SCRIPT ... 2>/dev/null`

## Mode detection

- If the user names a concrete stock code (e.g. `RELIANCE`, `NSE:TCS`, `TATAMOTORS`) →
  **Mode 1 (specific stock)**.
- If the user asks generically ("top 10 swing stocks", "what should I buy for swing",
  "recommend stocks") → **Mode 2 (recommendations)**.
- If ambiguous, ask one short clarifying question.

## Mode 1 — Specific stock

1. Run: `$PYTHON $SCRIPT -s <CODE> --json 2>/dev/null`. Parse the JSON.
   If it errors or returns no data, tell the user and suggest the `NSE:<CODE>` form.
2. Do 2–4 **WebSearch** queries for fresh context the script can't know:
   - `<company> share news latest`
   - `<company> Q result date 2026` (upcoming/last earnings)
   - `<company> stock target analyst` / sector sentiment
   Prefer results from the last few weeks. Note dates.
3. Score against **Swing criteria** below.
4. Emit ONE **Output block** (format below).

## Mode 2 — Top recommendations

Primary discovery is the **whole-market NSE bhavcopy screener** (objective, reproducible, covers
mid/small caps a fixed list or web search misses). WebSearch becomes the catalyst overlay.

- BHAV: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/bhav_screener.py`

1. **Discover candidates (whole market):**
   `$PYTHON /Users/mohdsuhel/ai-mini-projects/StockAnalayze/bhav_screener.py --profile swing --pool 30 --min-turnover-cr 25 2>/dev/null`
   Stage 1 scans ~2400 EQ stocks over ~30 EOD sessions for **sustained-trend** liquid names and
   returns ranked `candidates` (each with `ret_window_pct`, `near_recent_high`, `vol_surge`,
   `turnover_cr`). Take the top ~30 symbols. **Free — 0 Yahoo/AV cost.** (Fallback: WebSearch
   momentum/breakout/52-week-high lists if the bhav files are unreachable.)

   **Complementary LIVE Groww screens (`groww_market_screener.py`) — they fill the two gaps the
   momentum bhav screen misses.** The bhav screen only finds names *at their highs*; these add
   catalyst + pullback candidates:
   - **Catalyst discovery:** `$PYTHON groww_market_screener.py --screen volume-shockers --top 20 2>/dev/null`
     → unusual-volume names with an **`in_news`** flag (these fire `entry_quality.volume_spike_up`). For
     each, **WebSearch the catalyst** — a confirmed fresh catalyst is what earns High confidence.
   - **Dip-buy discovery:** `$PYTHON groww_market_screener.py --screen top-losers --top 20 2>/dev/null`
     → today's pullbacks. Keep those **near their 52w high** (`pct_from_52w_high` ≳ −12%, i.e. down today
     but still in an uptrend) and feed them to Stage 2 — `entry_quality.dip_buy` confirms the ones that are
     above SMA50 + oversold in a risk-on tape. This is how you surface the validated buy-the-dip setups.
   Merge these picks with the bhav list before Stage-2 verification (dedupe by symbol).
2. **Verify** them in one call:
   `$PYTHON $SCRIPT --screen "SYM1,SYM2,...,SYM30" 2>/dev/null`. Parse the JSON array.
   Skip entries that contain an `error` key (note how many were skipped).
3. **Score** each surviving stock against the Swing criteria. Drop weak/broken/over-extended setups.
   Add a WebSearch catalyst check on the top survivors (results due, orders, upgrades).
4. **Rank** by setup quality and expected risk-adjusted return; keep the **best 10**.
5. Emit a **summary table** — every row MUST carry the tradeable plan, not just a verdict:

   ```
   | # | Symbol | Verdict | Confidence | Entry | Stop-loss | Target | R:R | Expected % (weeks) | Why (1 line) |
   |---|--------|---------|------------|-------|-----------|--------|-----|--------------------|--------------|
   ```
   Keep Entry / Stop / Target ₹-precise (compute them per "Computing the trade plan" below). For WAIT/AVOID
   rows, put the trigger in Entry (e.g. "on close > ₹X") and leave Target/R:R as "—". Then a full **Output
   block** for each of the 10.
6. Warn the user up front: bhav discovery is quick, but verifying ~30 names on Yahoo takes a few
   minutes. **bhav discovery is EOD** — note the as-of date. Swing discovery surfaces strong
   multi-week movers, some **already extended** — favor pullback/continuation entries, don't chase.

## ⛔ ENTRY-QUALITY GATE — read `entry_quality` FIRST (this is the CGPOWER / AXISBANK fix)

The most common swing failure is **chasing an extended trend into resistance on drying volume**
— a name up +30–40% making new highs while *volume dries up* and *MACD rolls over* is
**distribution, not a fresh BUY** (CGPOWER on 2026-06-16 flagged `distribution-risk` and then
bled −5%; AXISBANK hit RSI 84 into resistance on 06-23 and fell −3%). The script now computes
`entry_quality` — treat these as **HARD gates that cap the verdict**, applied BEFORE the criteria below:

- **`entry_quality.distribution_risk == true`** (extended + near-high + `volume_drying` + `momentum_rolling_over`)
  → **NOT a fresh BUY.** Max verdict **WAIT** (or AVOID). Say "distribution risk — new highs on drying
  volume, wait for a real pullback + volume, don't chase."
- **`entry_quality.chase_into_resistance == true`** (RSI > 72 with `headroom_to_resistance_pct` < ~2.5%)
  → **do NOT chase.** Only BUY on a genuine pullback to support, or on a *fresh* breakout **with** a
  volume surge. Never a market BUY here.
- **`entry_quality.into_resistance == true`** (pinned under the 20-day high, `headroom_to_resistance_pct` < ~1.5%)
  → wait for the **break** (with volume) or a pullback — not a fresh entry at the ceiling.
- **`entry_quality.entry_grade`** ∈ {`distribution-risk`, `overbought-into-resistance`,
  `extended-no-volume`, `into-resistance`} → the honest call is **WAIT**, never a confident BUY.
  Only `constructive` (up-trend **with** volume ≥1.5) or a clean `neutral` with fresh volume earns a BUY.
- **Volume must confirm — but use the ROBUST 5-day-vs-20-day trend, not a noisy single bar.** A fresh BUY
  needs `entry_quality.volume_ok == true` (i.e. `volume_drying == false`; 5v20 volume trend ≥ ~0.9). A big
  single-day `volume.surge_ratio` ≥ 1.5 is a *bonus* (great on a breakout), **not mandatory** — requiring a
  1.5× spike on the exact entry bar is too noisy and skips good continuations. If `volume_drying == true`,
  the move lacks conviction → downgrade to WAIT.
- **⛔ Volatility regime — `entry_quality.low_volatility_grinder == true` (ATR < ~2.5% of price) caps the
  verdict at WAIT/low-confidence.** Backtested (3,640 trades): the SAME BUY setup won +8%/month **32% of the
  time on volatile names (ATR ≥ 2.5%, avg +4.5%) but only ~12% on calm ATR<2.5% grinders (avg ≈ 0)** — half
  the base rate. A calm grinder (e.g. JSWCEMENT: steady +6%/month, ATR ~1.5–2%) simply won't travel far
  enough to hit a swing target in the window — its ~10–20% move takes *months*, not weeks. So for a
  low-volatility name: don't force a swing BUY; say "constructive trend but ATR too low for a swing target
  in this window — accumulate/positional, not a swing," and lower confidence. The edge lives in **volatile
  trending names** — favor them.
- **⭐ `entry_quality.volume_spike_up == true` (a ≥3× volume day on a ≥1.5% up-close in the last ~3 bars) =
  accumulation / catalyst-likely — this is your CUE TO WEBSEARCH THE NEWS.** Backtested it beats base
  (~21% vs 14% hit +8%), and the biggest missed winners were catalyst gaps this flags: ZENSARTECH printed a
  91× spike before its +13% run, KALYANKJIL a 17× spike on its Q1-update circuit day before +34%. When you
  see it: **search "<company> stock news today / results" immediately** — a confirmed fresh catalyst turns a
  Med setup into a **High-confidence, catalyst-driven BUY** (this is the ONE lever that beats the technical
  ceiling). No catalyst found behind the spike → treat with caution (could be a one-off).
- **Earnings/event proximity (from your WebSearch): a result or major event INSIDE the ~1-month window is
  decisive.** A fresh beat/upgrade = the catalyst to ride; an unreported result *due* inside the window is
  binary risk → either trade the post-result direction or size down. Always check the next results date.
- **⭐ SECOND BUY PATH — BUY THE DIP (`entry_quality.dip_buy == true`).** The momentum rule (trend up + RSI
  50–70) misses a whole class of winners: a **pullback inside a longer uptrend**. `dip_buy` fires when the
  stock is short-term weak (down / below SMA20) but **still above SMA50 (uptrend intact)**, the **market is
  risk-on**, it's **oversold (RSI 30–55)**, and it's a **volatile name (not a grinder)**. Backtested: ~2×
  base (29% hit +5% in 10d, 40% hit +3% in 5d, avg +1.7–1.9%) and lower drawdown. Treat it as a **BUY (Med
  confidence) on a mean-reversion bounce** — entry near current price / SMA20, stop below the recent swing
  low, target the prior high. It roughly **doubles BUY coverage** vs momentum-only. (It does NOT fire on
  low-ATR grinders — their dips don't bounce reliably; that exclusion is essential and validated.)

## Market regime — read `market_regime`

- `market_regime.regime` = `risk-on` / `neutral` / `risk-off`. In **risk-off** (index below its SMA20/50,
  falling), demand a *stronger* stock-specific setup and lower confidence — a month-long long into a
  weak tape usually stalls. In risk-on, normal scoring.
- **Relative-strength trap:** a very high `benchmark.excess_return_vs_benchmark_pct` (> ~25–30%) on an
  **already-extended** name (`entry_quality.extended == true`) is *backward-looking exhaustion*, not a
  green light — the run already happened. Weight RS as a *positive* only when the name is NOT extended.

## Swing criteria (how you score, AFTER the gate above passes)

For a BUY, MOST of these must hold (use `swing_signals`, `entry_quality`, `indicators`, `benchmark`,
`market_regime`, `price`, `fundamentals` + your web findings):

- **Trend**: `swing_signals.trend == "up"`, price above SMA20 & SMA50.
- **Momentum**: RSI ~50–70 and **rising**; `indicators.macd_histogram` > 0 (not rolling over);
  AVOID overbought-extreme (RSI > ~78) into resistance.
- **Volume (required)**: `entry_quality.volume_ok == true` (5v20 trend not drying). A single-day
  `volume.surge_ratio` ≥ 1.5 / `swing_signals.volume_confirmed` is a bonus on breakouts, not mandatory.
- **Relative strength**: `benchmark.excess_return_vs_benchmark_pct` > 0 — but see the RS trap above.
- **Structure**: `breakout` true (with volume), or `consolidating` with real **headroom to resistance**
  (`entry_quality.headroom_to_resistance_pct` ≳ 3–4%) — not pinned at the ceiling.
- **Fundamentals sanity**: no broken financials, not extremely overvalued vs sector. (Note: fundamentals
  are a *live* snapshot, not point-in-time — don't over-weight them.)
- **News/sentiment**: no negative catalyst (fraud, downgrade, guidance cut) in web search.
- **Risk**: a logical stop (below recent swing low / `price.support_20d`, or ~1.5×`atr14`)
  that keeps **risk:reward ≥ ~1:2** for a 10–20% target.

If the entry-quality gate blocks it, or criteria mostly fail → **WAIT** or **AVOID**, never force into a
top-10. **When a name is extended on drying volume, the right answer is "wait for the pullback," and say so.**

## Confidence calibration & honest expectations (measured on 3,640 backtested swing setups)

Swing is a **magnitude / expectancy game, not a high-hit-rate game** — be honest with the user, never
imply 80–90% accuracy:

- **Base rate:** a random NSE large-cap long rises ≥8% in a month only ~14% of the time (≥5% ~25%), and
  is positive at all only ~52% — DIRECTION is near a coin flip.
- **A clean setup that clears the gate** historically did ~1.6× base: ~23% hit +8% and ~36% hit +5% in a
  month, avg forward return +2.3% (vs +0.6% for everything else). A **real but modest** edge — there is no
  reliable 80%+ swing signal.
- **Map Confidence to this reality — state the honest ~probability, don't inflate:**
  - **High** — clean technicals (gate passed, trend up, MACD>0, `volume_ok`, beating NIFTY) **AND a fresh
    positive catalyst** (result beat, order win, upgrade) **AND risk-on regime**. Still only ~1 in 3–4 hits
    the target: "~30–40% chance of target, ~2× base."
  - **Med** — clean technicals, no catalyst / mixed regime: "~20–25% chance, ~1.5× base."
  - **Low** — partial setup / into-resistance-but-basing → prefer WAIT.
- **The money is in R:R, not win-rate:** only take setups with target/stop ≥ ~2.5:1 so ~35% winners still
  profit. Use a **wider structural / 1.5×ATR stop** — a tight ~5% stop got whipsawed out too often in testing.
- **The one lever that beats the technical ceiling is the CATALYST** (the chart can't price tomorrow's news).
  No fresh catalyst → cap Confidence at **Med** and favor pullback entries / WAIT.

### Computing the trade plan

- **Entry**: current price if breakout confirmed, else the support/pullback zone.
- **Target**: next resistance or +10–20% (state which); cross-check with
  `fundamentals.financial_targets.target_mean_price` if present.
- **Stop-loss**: `price.support_20d` or entry − 1.5×`atr14`, whichever is tighter and logical.
- **Risk:Reward** = (target − entry) / (entry − stop). Aim ≥ 2.
- **Expected % / time**: from distance to target + trend strength (`adx_proxy`); express a
  range and rough weeks. Be honest about uncertainty.

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
- Fundamentals: <growth / valuation sanity>
- News/Sentiment: <fresh web findings WITH dates>
- Strength vs NIFTY: <excess return, out/under-performing>

**Risks & catalysts**
- Risks: <overbought / earnings due / sector weakness / high ATR / thin liquidity>
- Catalysts: <results date / event / order win>
```

End EVERY response with:
`_Educational analysis, not financial advice. Verify data and prices before trading._`

## Notes

- Sparse/empty `fundamentals` is normal for some tickers — lean on technicals + web, lower
  confidence; don't fail.
- If `as_of` (last bar date) is several days old (weekend/holiday), state the as-of date.
- Always prefer the freshest WebSearch info for news; the script's news can be stale.
