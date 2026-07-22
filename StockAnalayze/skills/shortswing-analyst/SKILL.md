---
name: shortswing-analyst
description: Use when the user wants an Indian-market SHORT-SWING call on a stock for a 3-5 TRADING DAY hold — a BUY / SELL / HOLD / WAIT / AVOID action with stop-loss, a realistic 3-5 day target, and expected move. Also does Mode 2 — "top 5 / top 10 stocks to BUY / that can go UP for the next 3-5 days". Daily bars, short MAs (SMA5/10/20), fast momentum, near-term swing levels, ATR-realistic targets. Alpha Vantage first, Yahoo fallback. NOT intraday (use intraday-analyst) and NOT month-long swing (use swing-analyst).
---

# Short-Swing Analyst (Indian market) — 3-5 trading day hold

Produce a **short-swing** call: hold roughly **3-5 trading days**, not more. This is the horizon
between `intraday-analyst` (same-day, square off by 3:20 PM) and `swing-analyst` (10-20% over
days-to-a-month). Actions are the standard set: **BUY / SELL / HOLD / WAIT / AVOID**. Targets are
**realistic ATR-based bands** for 3-5 days (typically ~3-8%), never month-long moves.

You are the brain. The Python tool only fetches daily bars and computes short-horizon facts (SMA5/
10/20, RSI + slope, MACD, 3/5/10-day ROC, recent 5/10/20-day swing highs/lows, breakout/breakdown,
volume surge, daily-ATR 3-5 day move band, optional short-window relative strength). You reason the
action, stop and target, and add live near-term news. **Educational, not financial advice.**

## Paths (absolute)

- PYTHON: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
- SCRIPT: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_shortswing.py`

Always run with stderr suppressed so stdout is clean JSON: `$PYTHON $SCRIPT ... 2>/dev/null`

## Data source — Alpha Vantage first, Yahoo fallback (both modes)

Default `--source auto`: tries **Alpha Vantage `TIME_SERIES_DAILY`** first (1 call/stock; same
25/day free-tier budget/counter as swing-analyst-v2), auto-falls back to **Yahoo daily** on
budget-exhaustion OR empty AV data. Unlike intraday, **AV daily DOES work for `.BSE`**, so
single-stock runs usually stay on AV. Check `data_source` (`alphavantage_daily` vs `yahoo_daily`)
and mention which fed the call.

- `--benchmark` adds short-window (~10-day) relative strength vs NIFTY (1 extra fetch). Optional.
- `{"error":"daily_budget_exhausted..."}` only appears with `--source av`; in `auto` it falls to
  Yahoo silently (noted in `warnings`). `{"error":"no daily data..."}` = uncovered/renamed symbol;
  say so and ask the user to confirm the NSE code.

## Mode detection

- Concrete stock code (`RELIANCE`, `NSE:TCS`, `TATAMOTORS`) → **Mode 1**.
- Generic ask ("top 5 stocks to buy for a few days", "what can go up this week") → **Mode 2**.
- Ambiguous → ask one short clarifying question.

## Mode 1 — Specific stock (action for the next 3-5 days)

1. Run: `$PYTHON $SCRIPT -s <CODE> --benchmark 2>/dev/null`. Parse the JSON. Handle `error` per above.
2. Do **1-3 WebSearch** queries for **near-term** catalysts (results due this week, block deals,
   sector moves, upgrades/downgrades). Note dates — a catalyst inside the 3-5 day window is pivotal.
3. Score the **action** (below). Compute the **trade plan** (ATR-realistic).
4. Emit ONE **Output block**.

## Mode 2 — Top N for 3-5 days (BUY / UP candidates)

Primary discovery is the **local universe screener** (objective, reproducible — it ranks a fixed
NSE list by the exact signals we compute, catching names an opinion-based web search misses). Then
overlay **WebSearch** for catalysts the chart can't see. This beats web-only discovery.

1. Tell the user the plan up front: you'll scan a ~100-name NSE universe (Yahoo, **~2-3 min**), then
   news-check the top hits. It uses **Yahoo (0 AV calls)** so it never touches the free-tier budget.
2. **Find candidates — pick the discovery mode:**
   - **Whole-market (recommended for "best movers", incl. mid/small caps):**
     `$PYTHON $SCRIPT --discover bhav --pool 40 --top 15 --direction up 2>/dev/null`.
     Stage 1 downloads the last ~7 official **NSE bhavcopies** and screens the ENTIRE market
     (~2400 EQ stocks) for liquid movers (turnover + 5-day momentum + volume surge), then Stage 2
     deep-dives the shortlist on Yahoo and ranks. `picks` carries both `screen_score` and
     `stage1_discovery` (turnover_cr, ret_window_pct, vol_surge, near_recent_high). EOD-only.
     Tune with `--min-turnover-cr` (liquidity floor, default 10) and `--pool` (shortlist size).
   - **Large-cap only (faster, no bhav):**
     `$PYTHON $SCRIPT --universe nifty100 --top 15 --direction up 2>/dev/null` (`nifty50` = faster).
   - **⭐ LIVE Groww screens (`groww_market_screener.py`) — best for near-term catalysts, which is the
     short-swing edge.** Two high-value feeds:
     - `--screen volume-shockers --top 20` → unusual-volume names + an **`in_news`** flag → these are the
       catalyst-driven movers; WebSearch each (a dated catalyst inside the 3-5d window is the whole game).
     - `--screen top-losers --top 20` → today's pullbacks; keep those still near the 52w high
       (`pct_from_52w_high` ≳ −12%) and feed to Stage 2 — `entry_quality.dip_buy` confirms the buy-the-dip
       ones (the strongest short-swing edge). Feed `picks[].symbol` straight into `$SCRIPT --screen`.
   - For "what to SELL / can go down" add `--direction down`. If the user names their own list,
     `--screen "A,B,C" --source yahoo`.
   - Parse the JSON: `picks` is the ranked array; note `market_universe_size`/`scanned` and
     `skipped_no_data`. **Discovery names are often mid/small caps — more volatile and frequently
     overbought/extended** (they got there by moving). Check `stage1_discovery.turnover_cr` for
     liquidity, and lean on the overbought/into-resistance judgement hard.
3. **Overlay WebSearch** on the top ~8-10 `picks`: same-day/near-term catalysts (results this week,
   block deals, upgrades). A catalyst inside the 3-5 day window is decisive — weight it heavily.
4. **Re-score** with the full short-swing criteria (the screen score is a coarse pre-rank): drop
   overbought-into-resistance names, broken structure, or event-risk you don't like; confirm R:R.
5. **Rank** the survivors by setup quality + ATR-realistic risk:reward; keep the requested count.
6. Emit a **summary table** (Symbol · Action · Expected 3-5d % · Confidence · 1-line reason), then a
   full **Output block** per pick. **Be honest if fewer than N clean BUYs exist** — in a weak tape,
   return the real BUYs plus WAIT setups and say so; never pad the list with forced BUYs.

> The `screen_score` ranks *bullish setup quality*, not conviction — a high score on an RSI-85 name
> still means "overbought, buy the pullback," not "chase it." Always apply the scoring section's
> overbought/into-resistance judgement on top of the rank.

## Short-swing scoring — the action call

Weigh the JSON with confluence (not one signal). **WAIT and AVOID are first-class** — never force a
BUY. Use `short_swing_signals`, `entry_quality`, `market_regime`, `moving_averages`, `momentum`,
`structure`, `volatility`, `volume`, `benchmark` + your web reading.

### ⛔ ENTRY-QUALITY GATE — read `entry_quality` FIRST (caps the action)

Even for a 3-5 day hold, do NOT chase overbought-into-resistance on drying volume (the CGPOWER /
AXISBANK failure mode). Apply these as HARD gates before the BUY checklist:

- **`entry_quality.distribution_risk == true`** → **not a fresh BUY**; max action **WAIT**.
- **`entry_quality.chase_into_resistance == true`** (RSI > 72, `headroom_to_resistance_pct` < ~2%)
  → **do NOT chase**; only BUY a pullback to support, or a *fresh* breakout **with** volume.
- **`entry_quality.into_resistance == true`** (< ~1.5% to the 10/20-day high) → wait for the break or a
  dip, not a fresh entry at the ceiling.
- **`entry_quality.entry_grade`** ∈ {`distribution-risk`, `overbought-into-resistance`, `into-resistance`,
  `extended-no-volume`} → downgrade to **WAIT/HOLD**, never a confident BUY.
- **Volume must confirm — use the ROBUST 5v20 trend:** require `entry_quality.volume_ok == true`
  (`volume_drying == false`; 5-day vs 20-day avg ≥ ~0.9). A single-day `volume.surge_vs_20d_avg` ≥ 1.5 is a
  bonus on a breakout, **not mandatory** (a 1.5× spike on the exact bar is too noisy). If `volume_drying`,
  treat as WAIT.
- **⛔ Volatility regime — `entry_quality.low_volatility_grinder == true` (ATR < ~2.5%) → WAIT / defer.**
  Calm low-ATR names don't travel far enough in 3-5 days to clear a real R:R target — backtested, the swing
  edge on ATR<2.5% names is roughly HALF the base rate. A steady low-vol grinder (e.g. JSWCEMENT, ATR ~1.5-2%,
  +2-3% chop over 5 days) is a **positional/swing name, not a short-swing one** — tell the user to use
  `swing-analyst` instead, or WAIT for a real catalyst that expands its range.
- **⭐ `entry_quality.volume_spike_up == true` (≥3× volume on a ≥1.5% up-day in the last ~3 bars) = your CUE
  TO WEBSEARCH THE CATALYST NOW.** The biggest 3-5d moves are catalyst gaps this flags — ZENSARTECH printed a
  91× spike before +13%, KALYANKJIL a 17× spike before its +34% Q1-update run, both while the pure chart
  looked weak (volume "drying" on the trailing average). **On a spike, search "<company> news today / results"
  before deciding.** A confirmed near-term catalyst = the High-confidence short-swing BUY the skill is built
  for; no catalyst behind the spike → be cautious (could fade).

### Market regime — read `market_regime`

- `market_regime.regime` = `risk-on` / `neutral` / `risk-off`. In **risk-off** (index below SMA20/50),
  require a stronger stock-specific setup and lower confidence; long setups fail more in a weak tape.

**BUY (long, 3-5 days) when most hold AND the entry-quality gate is clear:**
- `short_swing_signals.trend == "up"`; price above SMA5/10/20 and `sma5_above_sma10` true.
- Momentum rising: `rsi14` ~50-70 and `rsi_rising` true; `macd_histogram` > 0; positive 3/5-day ROC.
- Structure: `breakout_5d` true (closed above prior-5-day high) OR a clean bounce off `low_5d`/`low_10d`
  support with room to the next resistance.
- `volume_confirmed` true (surge ≥ ~1.5 on the up move); `benchmark.excess_return...` > 0 if enabled.
- No negative catalyst in WebSearch; **avoid buying overbought (RSI > ~72) straight into resistance.**

**⭐ BUY THE DIP (`entry_quality.dip_buy == true`) — the SECOND buy path.** A pullback (short-term
down/breakdown) **inside a longer uptrend** (above SMA50), while the **market is risk-on**, the name is
**oversold (RSI 30–55)** and **volatile (not a grinder)**. Backtested this mean-reversion bounce hits **+3%
in 5 days ~40% of the time (avg +1.9%)** — ~2× base — and it's the strongest edge on the short-swing horizon.
Take it as a **BUY (Med confidence)**: entry near price/SMA10, stop below the recent swing low, target the
prior 5–10d high. This is buying *weakness in a strong name* — distinct from the momentum BUY. (Does not
fire on low-ATR grinders — validated: their dips don't bounce.)

**SELL / AVOID — mostly "don't go long / exit a long," NOT "short it."** Signals: trend down, below
SMA5/10/20, `sma5_above_sma10` false, `breakdown_5d` true, RSI falling / <50, MACD hist < 0, negative ROC,
weak vs NIFTY.
> ⚠️ **The short side has NO reliable 3-5d edge on liquid stocks — do not issue SELL as a *short trade* by
> default.** Backtested on 1,374 nifty50 "trend-down/breakdown" signals: the stock **fell only ~47% of the
> time (avg +0.35%, i.e. it drifted UP)** — and no filter (below-SMA50, risk-off) fixed it. The market's
> baseline drift is up, so shorting a short-term breakdown is negative-expectancy and gets run over
> (in testing: INDUSINDBK "SELL" → +10%, MARUTI/BRITANNIA/TCS "SELL" → +6-8% within days).
> **So:** a weak/down chart → **AVOID (stand aside) / EXIT if you hold**, not a fresh short. Only call an
> actual SHORT on strong confluence — **below SMA50 + clear downtrend + risk-off regime + a fresh NEGATIVE
> catalyst** — and even then mark it **Low confidence** and use a tight stop. When unsure, AVOID, don't short.

**HOLD:** already in an up-trend and still constructive but extended or mid-range — keep an existing
long, but the risk:reward on a **fresh** entry here isn't attractive. Say "hold, don't add yet."

**WAIT:** setup forming but not triggered — e.g. coiling just below the 5-day high, or RSI basing but
not yet rising. Give the exact trigger to wait for.

**AVOID:** broken structure, overbought into resistance with no room, negative catalyst, or R:R < ~1.5.

Confidence: **High** = strong confluence + volume + clear levels + supportive catalyst; **Med** =
mixed but a lean; **Low** = thin data / choppy / conflicting / event risk inside the window.

## Confidence calibration & honest expectations (measured on 3,744 backtested 3-5d setups)

**The pure-technical 3-5 day edge is WEAK — be honest and very selective:**

- **Base rate:** only ~12% of large-cap longs gain ≥4% in 5 days; a clean gated setup nudged that to just
  ~17%, and "went up at all" was ~45% — a **coin flip**. Average 5-day return of BUY setups ≈ 0. On
  technicals ALONE, short-swing is close to noise. Never present a technicals-only 3-5d BUY as High confidence.
- **So only take a short-swing BUY when there is a real NEAR-TERM CATALYST inside the 3-5 day window**
  (results due, order/deal, block trade, sector move). Technicals set the entry/stop level; the **catalyst
  is what supplies the edge.** No catalyst → **WAIT**, or switch to the daily `swing-analyst` horizon.
- **Confidence:** **High** = clean setup **+ a dated catalyst inside the window**; **Med** = setup + softer
  near-term context; **Low / WAIT** = technicals only. 
- Expectancy game: keep R:R ≥ ~1.5–2 (ATR-capped target), and be honest that ~half of 3-5d setups fail on a
  single session — size accordingly and always use the stop.

## Trade plan (ATR-realistic, 3-5 days)

- **Entry:** current price if the trigger is already met (e.g. just broke the 5-day high on volume);
  else the explicit trigger/pullback zone (e.g. "on a close above prior-5d-high ₹X" or "on a bounce
  from SMA10 ₹Y").
- **Stop-loss:** the nearest real level below (for longs: `low_5d`/`low_10d` swing low, SMA10/SMA20)
  but **never tighter than ~1.5× `volatility.atr14`** — pick the more logical, slightly-wider one.
- **Target:** the next resistance (`high_5d`/`high_10d`/`high_20d`), **capped by**
  `volatility.expected_move_3_5d_pct` — do NOT promise more than the ATR band allows for 3-5 days.
  State the level and the % (typically ~3-8%).
- **Risk:Reward** = (target − entry) / (entry − stop). Aim **≥ ~1.5-2**. If it doesn't clear ~1.5,
  downgrade to WAIT/AVOID.
- **Expected % / time:** from distance to target + `expected_move_3_5d_pct`; give a range and a rough
  "~3-5 trading days." Be honest about noise.

## Output block

```
### <SYMBOL> — <Company>   (short-swing 3-5d · <data_source> · as-of <as_of>)
**Action:** BUY / SELL / HOLD / WAIT / AVOID   **Confidence:** High / Med / Low
**Expected:** ~X-Y% in ~3-5 trading days   **Risk:Reward:** 1:Z

**Trade plan**
- Entry / trigger: <level or "now">
- Target: <price> (+/−X%)  — <which level, within ATR band>
- Stop-loss: <price> (∓W%)  — <swing low / SMA / 1.5×ATR>

**Why**
- Trend/MAs: <vs SMA5/10/20, sma5>sma10?>   · Momentum: <RSI + slope, MACD hist, 3/5d ROC>
- Structure: <5-day breakout/breakdown, distance to near-term hi/lo>   · Volume: <surge?>
- Strength vs NIFTY: <10-day excess if enabled, else ROC context>
- Expected 3-5d move band: ~<pct>% (daily ATR based)

**News/Sentiment**
- <fresh near-term WebSearch catalysts WITH dates — flag anything inside the 3-5 day window>

**Risks & catalysts**
- Risks: <overbought/into-resistance / event inside window / low volume / high ATR / thin liquidity>
- Catalysts: <results date / event / breakout follow-through>
```

For Mode 2, precede the blocks with a table where **every row carries the tradeable plan**
(Entry / Stop / Target / R:R / Confidence), not just an action:
```
| # | Symbol | Action | Confidence | Entry | Stop | Target | R:R | Expected 3-5d | Why |
|---|--------|--------|------------|-------|------|--------|-----|---------------|-----|
```
Keep Entry / Stop / Target ₹-precise (from the Trade-plan section, ATR-capped). For WAIT/HOLD/AVOID rows,
put the trigger in Entry (e.g. "on close > ₹X") and leave Target/R:R as "—".

End EVERY response with:
`_Educational analysis, not financial advice. Short-swing setups can fail on a single session — use a stop and verify live prices before trading._`

## Notes

- `data_source` / `as_of` tell you feed + freshness — surface them; if `as_of` is a few days stale
  (weekend/holiday), say so.
- Keep the horizon honest: this is **3-5 trading days**. If the real setup needs weeks, say so and
  point to `swing-analyst`; if it's a same-day move, point to `intraday-analyst`.
- A catalyst (earnings, event) landing inside the 3-5 day window is decisive — weight it heavily and
  lower confidence for the binary risk.
- Prefer the freshest WebSearch for near-term news; a live headline outranks the chart.
- If a name is uncovered on both feeds, say so and suggest the daily `swing-analyst` for a broader view.
