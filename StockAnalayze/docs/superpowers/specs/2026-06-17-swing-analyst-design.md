# Swing Stock Analyst — Design Spec

**Date:** 2026-06-17
**Goal:** Enhance the existing `stock_analyze.py` tool into a Claude-powered swing-trading
analyst for the **Indian market**, optimized for finding stocks likely to return
**10–20% within ~1 month**. Delivered as a reusable **Claude Code skill** plus an
upgraded data layer.

> Educational output, not financial advice. Market data can be delayed or wrong.

---

## 1. Problem & Intent

The current tool (`stock_analyze.py`) fetches Yahoo price history, computes indicators
(RSI/MACD/SMAs/ATR), pulls yfinance fundamentals, merges news, and compares to the NIFTY
benchmark — then hands everything to a **local Ollama model (`llama3.1:8b`)** for the final
decision. The local model is the weak link.

The user wants:
- **Claude as the brain** instead of the local Ollama model (far stronger reasoning).
- **Live internet checks** — fresh news, graph/data, sentiment — folded into the decision.
- Focus on **swing trading** (NOT intraday), target **10–20% in ~1 month**.
- A **Claude skill** with **two modes**:
  1. Analyze a **specific stock** by code (e.g. `RELIANCE`, `NSE:TCS`).
  2. **Generic recommendation** — return the **top 10** swing candidates.
- A **proper trade plan + expectation** per stock, the way a human weighing multiple
  aspects would conclude.

---

## 2. Architecture Overview

Two parts:

- **Part A — Upgraded data layer** (`stock_analyze.py`): reuse the proven Yahoo/yfinance/news
  fetching; add a clean JSON output, swing-specific signals, and a batch-screen mode.
- **Part B — Claude skill** (`~/.claude/skills/swing-analyst/`): orchestrates the script
  for data, adds live WebSearch, and produces the trade plan using Claude's reasoning.

```
User ──▶ /swing-analyst (Claude skill)
            │
            ├── Mode 1: specific stock
            │     ├─ run: stock_analyze.py -s SYM --json      (indicators, fundamentals, news, RS)
            │     ├─ WebSearch: fresh news / results / sentiment / sector
            │     └─ Claude synthesizes trade plan
            │
            └── Mode 2: top 10
                  ├─ WebSearch: discover ~30 current IN swing/momentum candidates
                  ├─ run: stock_analyze.py --screen SYM1,SYM2,...  (verify with real data)
                  ├─ Claude scores each against swing criteria
                  └─ return best 10 ranked
```

**Decision: Claude is the brain.** The skill never calls Ollama. The script is used purely
as a data provider (existing Ollama mode stays intact for standalone CLI use).

---

## 3. Part A — `stock_analyze.py` Upgrades (data only)

All changes are **additive**; existing `--dump-prompt` and Ollama paths are untouched.

### 3.1 `--json` output mode
Emit the same fetched data as **structured JSON** instead of an Ollama prose prompt. Schema
(top-level keys):

```json
{
  "symbol": "RELIANCE",
  "resolved_ticker": "RELIANCE.NS",
  "meta": { "name": "...", "sector": "...", "industry": "..." },
  "price": { "last": 0.0, "prev_close": 0.0, "day_change_pct": 0.0,
             "high_52w": 0.0, "low_52w": 0.0,
             "pct_from_52w_high": 0.0, "pct_from_52w_low": 0.0 },
  "indicators": { "sma20": 0.0, "sma50": 0.0, "sma200": 0.0,
                  "rsi14": 0.0, "atr14": 0.0, "atr_pct": 0.0,
                  "macd": {...}, "bollinger": {...},
                  "adx_proxy": 0.0, "trend": "up|down|sideways" },
  "volume": { "last": 0, "avg20": 0, "surge_ratio": 0.0 },
  "swing_signals": { "above_sma20": true, "above_sma50": true, "above_sma200": true,
                     "breakout": true, "consolidating": false,
                     "volume_confirmed": true },
  "benchmark": { "index": "^NSEI", "rel_return_1m": 0.0, "rel_return_3m": 0.0,
                 "outperforming": true },
  "fundamentals": { ...compact yfinance package... },
  "news": [ { "title": "...", "source": "...", "published": "...", "link": "..." } ],
  "warnings": [ "..." ]
}
```

JSON is printed to **stdout** only; logs stay on **stderr** so the skill can parse cleanly.

### 3.2 Swing-specific signals (added to computed metrics)
- `high_52w`, `low_52w`, `pct_from_52w_high`, `pct_from_52w_low` (proximity to highs =
  momentum / room).
- `volume.surge_ratio` = last volume ÷ 20-period average (institutional interest).
- `swing_signals.breakout` / `consolidating` — simple range-compression + breakout heuristic
  over recent daily bars.
- `indicators.adx_proxy` + `trend` label — trend-strength read (avoid choppy names).

These reuse existing OHLCV already fetched; no new network calls.

### 3.3 `--screen SYM1,SYM2,...` batch mode
Fetch JSON for many symbols in **one process**, reusing the existing inter-call sleeps to
limit Yahoo HTTP 429s. Output: a JSON array of per-symbol objects (same schema as `--json`),
with failed symbols captured as `{ "symbol": ..., "error": ... }` rather than aborting.

### 3.4 Out of scope for Part A
No new data vendors, no DB, no caching layer, no changes to symbol normalization or news
sources. Keep the diff focused.

---

## 4. Part B — The `swing-analyst` Skill

### 4.1 Location & invocation
- **Personal skill:** `~/.claude/skills/swing-analyst/SKILL.md` (usable from any directory).
- References the script by absolute path:
  `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
  `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze.py`.
- **Invoke by:** `/swing-analyst <CODE>` for a specific stock, or natural language such as
  *"analyze TATAMOTORS for swing"* / *"give me top 10 swing stocks"*. The skill detects which
  mode from whether a concrete symbol is present.

### 4.2 Mode 1 — Specific stock
1. Run `stock_analyze.py -s <SYM> --json`, parse JSON.
2. **WebSearch** (2–4 queries) for: latest company news, upcoming/last results date, analyst
   view, sector/sentiment — to supplement possibly-stale script news and add context.
3. Score against the swing criteria (§4.4) and produce the **output block** (§5).

### 4.3 Mode 2 — Top 10 recommendations
1. **WebSearch** to discover **~30** current Indian swing/momentum/breakout candidates from
   screeners, market news, and analyst lists. Dedupe to NSE codes.
2. Run all ~30 through `stock_analyze.py --screen ...` (one process).
3. Score each against the swing criteria (§4.4); drop names with broken data or failing
   setups.
4. Rank and return the **top 10**, each as a compact output block, plus a one-line summary
   table at the top (symbol · verdict · expected % · confidence).
5. **Depth:** ~30 candidates (user chose "deep"). Warn that this can take a few minutes due
   to Yahoo rate-limit sleeps.

### 4.4 Swing scoring criteria (baked into the skill)
A candidate is favorable when most hold:
- **Trend:** established uptrend; price above SMA20 & SMA50 (SMA200 a plus).
- **Momentum:** RSI healthy (≈50–70), **not** overbought-extreme (>~78) into resistance.
- **Volume:** recent surge / confirmation on up-moves.
- **Relative strength:** outperforming NIFTY over 1–3 months.
- **Structure:** near a breakout or clean consolidation, with room to the next resistance for
  a 10–20% move.
- **Fundamentals sanity:** no obviously broken financials / not extremely overvalued.
- **Risk:** a logical stop (e.g. below recent swing low / ~1.5×ATR) that keeps **risk:reward
  ≥ ~1:2** for the target.

Failing candidates get **WAIT** or **AVOID**, never forced into the top 10.

---

## 5. Output Format (per stock)

```
### <SYMBOL> — <Company>
**Verdict:** BUY / WAIT / AVOID   **Confidence:** High / Med / Low
**Expected:** ~X% in ~Y weeks   **Risk:Reward:** 1:Z

**Trade plan**
- Entry: <zone>
- Target: <price> (+X%)
- Stop-loss: <price> (−W%)

**Why**
- Technicals: <trend, SMAs, RSI, breakout, volume>
- Fundamentals: <growth/valuation sanity>
- News/Sentiment: <fresh web findings + dates>
- Strength vs NIFTY: <out/under-performing>

**Risks & catalysts**
- Risks: <overbought / earnings due / sector weak / high ATR ...>
- Catalysts: <results date / event ...>
```

Mode 2 prepends a ranked summary table. Every response ends with:
`_Educational analysis, not financial advice. Verify data before trading._`

---

## 6. Error Handling

- **Script/network failure** (Yahoo 429, missing fundamentals): skill surfaces the warning,
  proceeds with available data, and lowers confidence rather than failing outright. Partial
  fundamentals are explicitly normal (see README troubleshooting).
- **Bad/unknown symbol:** report it; suggest exchange-qualified form (`NSE:XXX`).
- **WebSearch unavailable / thin results:** fall back to script data, note reduced freshness.
- **`--screen` partial failures:** per-symbol `error` entries are skipped from ranking, noted
  in a footnote.
- **Stale data:** if last price date is old (e.g. weekend/holiday), state the as-of date.

---

## 7. Testing / Verification

- **Script `--json`:** run on `RELIANCE`, `TCS`, and a thin-fundamentals ticker (e.g. `HAL`);
  confirm valid JSON on stdout, logs only on stderr, all schema keys present, swing_signals
  populated.
- **`--screen`:** run on a 3-symbol list incl. one invalid symbol; confirm array output with
  an `error` entry and no abort.
- **Skill Mode 1:** invoke on a known symbol; confirm full output block with live web news.
- **Skill Mode 2:** invoke "top 10"; confirm ~30 discovered → screened → ranked 10 with
  summary table.
- **Regression:** existing `--dump-prompt` and Ollama path still work unchanged.

---

## 8. Deliverables

1. Upgraded `stock_analyze.py` (`--json`, swing signals, `--screen`).
2. `~/.claude/skills/swing-analyst/SKILL.md`.
3. Short usage note appended to `README.md` (how to invoke both modes).

---

## 9. Decisions Locked

| Topic | Decision |
|-------|----------|
| Brain | Claude (skill), not Ollama |
| Data | Reuse + upgrade `stock_analyze.py` |
| Top-10 universe | Web-discover then verify with script |
| Screen depth | ~30 candidates (deep) |
| Install | Personal skill `~/.claude/skills/swing-analyst/` |
| Output | Trade plan + verdict/confidence/expected + multi-aspect why + risks/catalysts |
| Market | Indian (NSE default, `.NS`) |
