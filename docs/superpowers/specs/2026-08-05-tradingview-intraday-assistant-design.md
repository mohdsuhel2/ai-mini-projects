# TradingView Intraday Assistant — Chrome Extension Design

**Date:** 2026-08-05
**Status:** Approved (user, in-session)

## Purpose

A Chrome extension (Manifest V3) that, when clicked on an open TradingView chart
(e.g. `https://www.tradingview.com/chart/?symbol=NSE%3AADANIENT`), scrapes the
visible chart data and gives one decisive intraday verdict — the way an expert
Indian-market intraday trader would: **BUY NOW / BUY ON PULLBACK / SHORT NOW /
WAIT / NO TRADE**, with entry, stop-loss, targets, risk:reward and the reasons.

Decisions locked in with the user:

- **Data source: scrape the TradingView page only.** No external APIs, no local
  Python server. The user adds the standard indicator set to their chart —
  **VWAP, EMA 9, EMA 20, RSI 14** (volume is on by default) — and the extension
  reads their live values from the chart legend.
- **UI: popup on icon click.** Read the chart at that moment, render the verdict.
- **Market: NSE intraday** (square-off day-trading, IST session times).

## Folder

`ai-mini-projects/tradingview-intraday-assistant/`

```
tradingview-intraday-assistant/
├── manifest.json      # MV3, activeTab + scripting, tradingview.com only
├── content.js         # scraper: symbol, OHLC, price, legend indicator values
├── engine.js          # pure decision function (browser + Node, testable)
├── popup.html
├── popup.css
├── popup.js           # orchestrates: message content script → engine → render
├── tests/
│   └── engine.test.js # plain Node asserts, `node tests/engine.test.js`
└── README.md          # install (load unpacked) + required chart setup
```

## Components

### content.js — scraper

Declared as a content script on `*://*.tradingview.com/*`; answers a
`{type: "SCRAPE"}` runtime message with a data object. If messaging fails
(page loaded before the extension was installed), popup.js falls back to
injecting `content.js` via `chrome.scripting.executeScript` and retries once.

What it reads, in order of preference:

1. **Symbol/exchange** — URL `symbol` param (`NSE:ADANIENT`), falling back to
   the page title.
2. **Price and change %** — page title (`"ADANIENT 2,455.30 ▲ +1.2% …"`),
   falling back to legend values.
3. **OHLC** — the main series legend row's label/value pairs (O, H, L, C).
4. **Indicators** — every study row in the legend: match by *title text*
   (`VWAP`, `EMA 9`, `EMA 20`, `RSI`, `Vol`), never by TradingView's hashed
   CSS class names. Attribute selectors like `[data-name="legend-source-item"]`
   and `[class*="valueValue"]` only.

Value parser handles: thousands commas, Unicode minus, and K/M/B/L/Cr suffixes.
`prevClose` is derived from price and change % — from it, gap % = (open −
prevClose)/prevClose.

Anything not found is reported in a `warnings` array (e.g. *"EMA 20 not on
chart — add Moving Average Exponential (20) for better signals"*) rather than
failing. Scrape only fails hard when price itself can't be found.

### engine.js — decision logic

One pure function `analyze(data, now)` (browser global + CommonJS export) so it
runs identically in the popup and in Node tests. `now` is injected for
testability; time math uses Asia/Kolkata.

**Evidence scoring** (each with a human-readable reason string):

| Signal | Bull | Bear |
|---|---|---|
| Price vs VWAP | above | below |
| EMA alignment | price > EMA9 > EMA20 | price < EMA9 < EMA20 |
| RSI 14 | > 60 | < 40 |
| Day-range position | top 30% of range | bottom 30% |
| Change % | ≥ +0.75% | ≤ −0.75% |

Modifiers: RSI > 75 / < 25 = overextension caution; price stretched > 1% from
VWAP = "don't chase" (converts BUY NOW → BUY ON PULLBACK toward VWAP/EMA9);
gap context noted.

**Time gates (IST):** market closed / weekend → NO TRADE (market closed).
9:15–9:40 → caution note (opening volatility). After 14:45 → no fresh entries
(WAIT with square-off framing). 15:20+ → square-off reminder.

**Verdict mapping:** net score ≥ +3 → BUY NOW (or BUY ON PULLBACK if
stretched); ≤ −3 → SHORT NOW; otherwise WAIT with "what would change the
call". Mixed/contradictory evidence (e.g. above VWAP but RSI < 40) → WAIT.

**Levels (long; short mirrored):** entry = current price (pullback verdicts:
zone between EMA9 and VWAP); stop = the tighter sensible structure —
max(VWAP, EMA20) below entry minus a 0.3% buffer, bounded by day low; targets =
1.5R and 2.5R plus day high as reference; R:R vs target 1. **If R:R < 1.2 the
verdict downgrades to WAIT** ("good setup, bad location — wait for the
pullback"). Position-size hint: shares = floor(₹1,000 / per-share risk),
labeled as such.

### popup — UI

Dark card, verdict-colored (green BUY / red SHORT / amber WAIT / gray NO
TRADE): header (symbol, price, change), big verdict banner, levels table
(entry / stop / T1 / T2 / R:R / size@₹1k risk), reasons as ✓/✗ list, warnings
strip, footer with IST timestamp, the "don't hover old candles when clicking"
caveat, and a not-financial-advice line.

Not on tradingview.com → friendly "open a TradingView chart first" state.

## Error handling summary

- Wrong site → guidance state, no scrape.
- Content script not loaded → inject-and-retry once, then readable error.
- Missing indicators → degrade + name exactly what to add.
- Legend showing a hovered historical bar → caveat text in footer (cannot be
  detected reliably; documented in README).

## Testing

`engine.js` is the risk concentration → Node test file covers: strong-bull →
BUY NOW; stretched-bull → BUY ON PULLBACK; strong-bear → SHORT NOW; mixed →
WAIT; R:R-floor downgrade; time gates (pre-open, opening window, post-14:45,
post-close, weekend); missing-indicator degradation; parser cases (commas,
minus, K/M suffixes) if parsing lives in shared code. Scraper is verified
manually on the ADANIENT chart (DOM is not unit-testable offline).

## Out of scope (honest limits)

No history-based math (no ADX, RVOL, multi-timeframe, SuperTrend) — the legend
only exposes current values. This is a fast chart-side second opinion, not a
replacement for the Python `/intraday-analyst-2` engine. No auto-refresh, no
alerts, no order placement.
