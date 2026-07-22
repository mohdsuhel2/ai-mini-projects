# StockAnalayze

Swing-style stock analysis from **Yahoo Finance** price history, **optional deep fundamentals** (via [yfinance](https://github.com/ranaroussi/yfinance)), **news** (Yahoo + Google News RSS), **benchmark relative strength**, and a local **Ollama** model. The tool builds a large prompt and asks the model for a **decision summary** (if you already hold vs. if you want to buy) plus narrative.

This is **educational** output, not financial advice. Market data can be **delayed** or **wrong**.

---

## Prerequisites

- **Python 3.10+** (3.12+ recommended)
- **[Ollama](https://ollama.com/)** running locally (or reachable via `--ollama-host`)
- A **virtual environment** is recommended. On many systems (e.g. Homebrew Python), system-wide `pip install` is blocked (PEP 668); use a venv or `pip install --break-system-packages` if you know the trade-offs.

---

## Install

### 1. Create a venv and install dependencies

From this directory (`StockAnalayze/`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

This installs **`yfinance`** (needed for reliable Yahoo fundamentals; without it, fundamentals may be sparse and fallbacks may fail).

### 2. Pull the default model in Ollama

Default model in code: **`llama3.1:8b`**.

```bash
ollama pull llama3.1:8b
```

Use another model anytime with `-m your-model:tag`.

### 3. Optional: environment variables

| Variable | Meaning |
|----------|---------|
| `OLLAMA_HOST` | Base URL for Ollama (default `http://localhost:11434`) |
| `LOG_LEVEL` | Default log level if you omit `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Run

Always prefer the **venv Python** so `yfinance` imports correctly:

```bash
.venv/bin/python stock_analyze.py -s RELIANCE
```

Examples:

```bash
# Indian plain symbol → normalized to NSE suffix (.NS)
.venv/bin/python stock_analyze.py -s RELIANCE

# Explicit exchange
.venv/bin/python stock_analyze.py -s NSE:TCS

# Another model
.venv/bin/python stock_analyze.py -s INFY -m llama3.1:8b

# Print the constructed prompt only (no LLM call; useful for debugging)
.venv/bin/python stock_analyze.py -s RELIANCE --dump-prompt

# More verbose progress on stderr
.venv/bin/python stock_analyze.py -s RELIANCE --log-level DEBUG
```

**Outputs**

- **Stdout**: Model reply (must start with the fixed **STOCKANALAYZE DECISION SUMMARY** block per prompt instructions).
- **Stderr**: Progress logs (`→ Fetching…`, fundamentals source, warnings).

---

## Symbol formats

| Input | Resolved Yahoo-style ticker (examples) |
|-------|----------------------------------------|
| `RELIANCE` | `RELIANCE.NS` |
| `NSE:TCS` | `TCS.NS` |
| `INFY.BO` | kept as BSE-style |

Symbols with `-` or existing `.NS` / `.BO` suffixes are left as-is.

---

## What gets fetched (high level)

1. **Charts**: Yahoo `v8` chart — weekly/daily horizons (e.g. 5y weekly, 2y daily, 3mo daily, 1mo daily).
2. **Computed metrics**: SMAs, ranges, ATR, RSI, MACD/Bollinger-style extras, volume ratios, etc.
3. **Benchmark**: Regional index (e.g. `^NSEI` for `.NS`) — overlapping-window relative returns vs. stock.
4. **Deep fundamentals**: **`yfinance`** (primary) + legacy urllib **`quoteSummary`** fallback — valuation, margins, targets, analyst snippets when Yahoo exposes them.
5. **Listing snapshot**: Yahoo Finance search (sector/industry; may hit rate limits).
6. **News**: Yahoo headlines + Google News RSS (merged, de-duplicated).

Fetches can take **trivial sleeps** between Yahoo calls to reduce **HTTP 429** rate limiting.

---

## Claude skill: swing-analyst (recommended)

A Claude Code skill uses **Claude** (not the local Ollama model) as the analyst, plus live
web search, optimized for **swing trades targeting 10–20% over days-to-a-month** in the
**Indian market**. Installed at `~/.claude/skills/swing-analyst/`.

**Two ways to use it (just ask Claude Code in plain English):**

- **Specific stock:** `analyze RELIANCE for swing` or `/swing-analyst TATAMOTORS`
- **Top recommendations:** `give me top 10 swing stocks`

Under the hood the skill calls the script's new data modes:

```bash
# Structured JSON for one symbol (no LLM call)
.venv/bin/python stock_analyze.py -s RELIANCE --json

# Batch-screen many symbols → JSON array (no LLM call)
.venv/bin/python stock_analyze.py --screen "TCS,INFY,RELIANCE,HDFCBANK"
```

> Educational analysis, not financial advice.

---

## Claude skill: intraday-analyst

For **intraday** (full-day hold, not scalping): asks about ONE stock mid-session and returns a single
decisive **BUY / SELL / SHORT / WAIT / NO-TRADE** call — entry, stop, targets, R:R and invalidation,
institutional decision-engine style — squared off by ~3:20 PM IST. Installed at
`~/.claude/skills/intraday-analyst/`.

- **Ask:** `/intraday-analyst RELIANCE` or `is TCS good for intraday today?`
- The tool computes VWAP, opening range, prior-day pivots, gap, RVOL, 15-min RSI/MACD/ATR **plus
  EMA 9/20/50/200 + alignment, ADX (+DI/−DI), SuperTrend, Bollinger, multi-timeframe trend
  (5m/15m/1h/daily) + overall bias, and India-VIX / NIFTY market context.** **Alpha Vantage intraday
  first, automatic Yahoo fallback** (AV intraday is usually absent for `.BSE`).
- Full institutional mode is default (~15–20s, parallelized fetches); add `--fast` for the base 15-min
  block only (~3s — used when screening many names).
- **Candidate pools for "top 5":** `groww_intraday_screener.py` scrapes `groww.in/stocks/intraday` for
  TODAY's live intraday movers (LTP, volume ratio, `in_news` flag, market cap) — the preferred source for
  what's actually in play; `bhav_screener.py --profile intraday` is the EOD whole-market liquid fallback.

```bash
.venv/bin/python groww_intraday_screener.py --direction up   --top 12 --min-mcap-cr 1000 --min-price 50 2>/dev/null  # gainers → longs
.venv/bin/python groww_intraday_screener.py --direction down --top 12 --min-mcap-cr 1000 --min-price 50 2>/dev/null  # losers  → shorts
```

**`intraday-analyst-v2`** is the **classic price-action sibling** — same intraday horizon, but reasoned
with **candlestick patterns at support/resistance, Fibonacci retracements, moving-average retracements
(Pristine buy/sell setup), and Dow-theory multi-timeframe trend**, per Zerodha Varsity Module 2 and
Greg Capra's *Intra-Day Trading Techniques*. It computes candlestick detection (Marubozu/Doji/Hammer/
Engulfing/Harami/Piercing/Dark-Cloud/Morning-Evening-Star), swing S/R, Fibonacci levels, SMA20/40 + the
"MA test", and the Pristine setup — and outputs a Varsity **trade summary** (entry / stop = signal-candle
low-high / target / R:R / holding). Use v1 for the quant engine, v2 for classic TA, or both for confluence.

```bash
.venv/bin/python stock_analyze_intraday_classic.py -s RELIANCE 2>/dev/null   # candlesticks / S-R / Fib / MA / Pristine
```

```bash
# Structured intraday JSON for one symbol (AV first, Yahoo fallback)
.venv/bin/python stock_analyze_intraday.py -s RELIANCE 2>/dev/null

# Force the Yahoo feed (skip Alpha Vantage)
.venv/bin/python stock_analyze_intraday.py -s TCS --source yahoo 2>/dev/null
```

> Intraday is leveraged and high-risk. Educational analysis, not financial advice.

---

## Claude skill: shortswing-analyst

For a **3–5 trading day** hold (between intraday and month-long swing): a **BUY / SELL / HOLD /
WAIT / AVOID** call with stop-loss and a realistic ATR-based 3–5 day target. Also does **top-5 /
top-10** "what can go UP / to BUY for the next few days". Installed at
`~/.claude/skills/shortswing-analyst/`.

- **Specific stock:** `/shortswing-analyst RELIANCE` or `is HDFCBANK a buy for the next few days?`
- **Top picks:** `top 5 stocks to buy for the next 3-5 days`
- Daily bars; computes SMA5/10/20, RSI + slope, MACD, 3/5/10-day ROC, recent 5/10/20-day swing
  levels, breakout/breakdown, volume surge, and a daily-ATR 3–5 day move band. **Alpha Vantage
  daily first, Yahoo fallback.**

```bash
# One symbol, short-swing JSON (AV first, Yahoo fallback) + 10-day relative strength
.venv/bin/python stock_analyze_shortswing.py -s RELIANCE --benchmark 2>/dev/null

# Screen a specific list (Yahoo, protects the AV free-tier budget)
.venv/bin/python stock_analyze_shortswing.py --screen "TCS,INFY,HDFCBANK" --source yahoo 2>/dev/null

# Universe screener: rank a bundled NSE list by short-swing setup quality (~2-3 min, 0 AV calls)
.venv/bin/python stock_analyze_shortswing.py --universe nifty100 --top 10 --direction up 2>/dev/null
.venv/bin/python stock_analyze_shortswing.py --universe nifty50  --top 10 --direction down 2>/dev/null

# WHOLE-MARKET discovery: NSE bhavcopy Stage-1 (all ~2400 EQ) -> Yahoo Stage-2 deep-dive + rank
.venv/bin/python stock_analyze_shortswing.py --discover bhav --pool 40 --top 10 --direction up 2>/dev/null
```

Two Mode-2 discovery engines:

- **`--universe nifty100/50`** — ranks a bundled NSE list (`universes/*.txt`, editable) via Yahoo by
  our own signals (trend, 5-day breakout, RSI slope, volume surge, relative strength) → `screen_score`.
  Fast, large-cap only.
- **`--discover bhav`** — the whole-market engine. Downloads the last ~7 official **NSE bhavcopy**
  EOD files (`bhav_screener.py`), screens **all ~2400 EQ stocks** for liquid movers (turnover +
  5-day momentum + volume surge), shortlists the top `--pool`, then runs the same Yahoo Stage-2
  deep-dive + ranking. Finds mid/small-cap movers a fixed index misses. EOD-only; tune
  `--min-turnover-cr` for the liquidity floor. `bhav_screener.py` also runs standalone.

`bhav_screener.py` is a **shared discovery engine** with horizon profiles, reused across the skills:

```bash
# shortswing (3-5d): fresh breakouts + volume     (used by shortswing-analyst --discover)
.venv/bin/python bhav_screener.py --profile shortswing --pool 40 2>/dev/null
# swing (weeks): sustained multi-week trend        (used by swing-analyst v1 & v2 Mode 2)
.venv/bin/python bhav_screener.py --profile swing --pool 30 --min-turnover-cr 25 2>/dev/null
# intraday: liquidity x volatility WATCHLIST (EOD)  (used by intraday-analyst, prep-only)
.venv/bin/python bhav_screener.py --profile intraday --pool 12 --min-turnover-cr 25 2>/dev/null
```

For **swing-analyst-v2** this is a big deal: discovery over the whole market costs **0 Alpha Vantage
calls**, so the 25/day budget is spent only on the deep-dive of a small shortlist.

Both return ranked `picks`; the skill overlays WebSearch for catalysts. `--direction up` = BUY/long
candidates, `down` = SELL/short.

> Educational analysis, not financial advice.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| `HTTP Error 404` / `No fundamentals data found for symbol: HAL.NS` (or similar) | **Normal for some tickers.** Yahoo’s `quoteSummary` bundle is missing even when the stock is valid; `yfinance` still fills `Ticker.info` from other endpoints. StockAnalayze **filters this log noise** on stderr. Trust OHLCV + computed metrics; fundamentals JSON may be partial. |
| `ModuleNotFoundError: yfinance` | Use `.venv/bin/python` after `pip install -r requirements.txt`, or activate the venv. |
| Empty or sparse fundamentals | Ensure `yfinance` is installed; try again later (Yahoo rate limits). |
| `429` from Yahoo | Wait and rerun; avoid hammering many symbols in a loop. |
| Ollama errors | Confirm `ollama serve` / daemon is up; `ollama pull` your `-m` model; check `--ollama-host`. |
| Model ignores decision format | Retry or use a stronger instruction-following model (`-m`). |

---

## Requirements file

See [`requirements.txt`](./requirements.txt) for pinned **`yfinance`** and comments about the default Ollama model and logging.
