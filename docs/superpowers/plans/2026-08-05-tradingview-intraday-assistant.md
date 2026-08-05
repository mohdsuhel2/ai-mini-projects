# TradingView Intraday Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chrome MV3 extension that scrapes an open TradingView chart and renders a decisive intraday verdict (BUY NOW / BUY ON PULLBACK / SHORT NOW / WAIT / NO TRADE) with entry, stop, targets, R:R and reasons.

**Architecture:** Content script scrapes the TradingView legend by title text (never hashed classes) and answers a runtime message; a pure `analyze(data, now)` function in `engine.js` (loaded in both popup and Node tests) turns the scrape into a verdict; the popup orchestrates and renders. Spec: `docs/superpowers/specs/2026-08-05-tradingview-intraday-assistant-design.md`.

**Tech Stack:** Plain JS (no build step), Chrome Manifest V3, Node built-in `assert` for tests.

## Global Constraints

- Folder: `tradingview-intraday-assistant/` at repo root.
- No external APIs, no network calls — scrape only.
- Host scope: `*://*.tradingview.com/*` only; permissions `activeTab`, `scripting`.
- `engine.js` must run in browser and Node (`if (typeof module !== 'undefined') module.exports = …`).
- All times IST (Asia/Kolkata); `analyze` takes `now` injected for testability.
- Degrade on missing indicators via `warnings`; hard-fail only when price is missing.
- Every displayed number formatted to 2 decimals; ₹1,000 default risk for size hint.

---

### Task 1: Scaffold — manifest + README

**Files:**
- Create: `tradingview-intraday-assistant/manifest.json`
- Create: `tradingview-intraday-assistant/README.md`

**Interfaces:**
- Produces: extension shell that Chrome loads unpacked; content script `content.js` and popup `popup.html` referenced (created in later tasks).

- [ ] **Step 1: Write manifest.json** — MV3, name "TradingView Intraday Assistant", `action.default_popup: popup.html`, `content_scripts: [{matches: ["*://*.tradingview.com/*"], js: ["content.js"], run_at: "document_idle"}]`, `permissions: ["activeTab", "scripting"]`, `host_permissions: ["*://*.tradingview.com/*"]`. No icons (Chrome default).
- [ ] **Step 2: Write README.md** — install via `chrome://extensions` → Developer mode → Load unpacked; required chart setup (add VWAP, EMA 9, EMA 20, RSI 14; keep Volume); usage & caveats (5-minute chart recommended, don't hover historical candles when clicking the icon, legend must be visible, not financial advice).
- [ ] **Step 3: Commit** — `feat: scaffold TradingView intraday assistant extension`.

### Task 2: engine.js — number parser (TDD)

**Files:**
- Create: `tradingview-intraday-assistant/engine.js`
- Test: `tradingview-intraday-assistant/tests/engine.test.js`

**Interfaces:**
- Produces: `parseNum(text) -> number|null` handling: `"2,455.35"`, Unicode minus `"−12.34"`, suffixes `K/M/B` (×1e3/1e6/1e9), `L` (lakh ×1e5), `Cr` (crore ×1e7), garbage → `null`. Exported (with `analyze`) via CommonJS guard + attached to `globalThis.IntradayEngine` for the browser.

- [ ] **Step 1: Write failing tests** for the cases above in `tests/engine.test.js` using `node:assert` and a tiny `t(name, fn)` runner that counts pass/fail and exits 1 on failure.
- [ ] **Step 2: Run** `node tests/engine.test.js` — expect failure (module missing).
- [ ] **Step 3: Implement `parseNum`** in `engine.js`.
- [ ] **Step 4: Run tests** — expect pass.
- [ ] **Step 5: Commit** — `feat: engine number parser`.

### Task 3: engine.js — analyze() decision function (TDD)

**Files:**
- Modify: `tradingview-intraday-assistant/engine.js`
- Test: `tradingview-intraday-assistant/tests/engine.test.js`

**Interfaces:**
- Consumes: `parseNum` (Task 2).
- Produces:
  ```js
  analyze(data, now) -> {
    verdict,            // 'BUY NOW'|'BUY ON PULLBACK'|'SHORT NOW'|'WAIT'|'NO TRADE'
    confidence,         // 'high'|'medium'|'low'
    direction,          // 'long'|'short'|null
    headline,           // one-sentence summary of the call
    levels: { entry, entryZone: [lo,hi]|null, stop, target1, target2, dayRef, rr, qtyAt1kRisk } | null,
    reasons: [{side: 'bull'|'bear'|'note', text}],
    warnings: [string],
    score: {bull, bear, net},
    session: {phase, label}   // 'closed'|'opening'|'normal'|'no-new'|'squareoff'
  }
  ```
  `data` = `{symbol, exchange, price, open, high, low, changePct, vwap, ema9, ema20, rsi, volumeText, warnings}` (numbers or null). `now` = `Date`.

Scoring/verdict rules — implement exactly per spec table: VWAP side ±1, full EMA alignment ±1 (partial 0), RSI >60/+1 <40/−1, range position >0.7/+1 <0.3/−1, changePct ≥+0.75/+1 ≤−0.75/−1. Stretch = |price−vwap|/vwap > 1% → BUY NOW becomes BUY ON PULLBACK (entryZone [min(ema9,vwap), max(ema9,vwap)]); RSI >75/<25 adds caution note and drops confidence one level. Net ≥+3 long, ≤−3 short, else WAIT. prevClose = price/(1+changePct/100); gap noted ≥|0.3%|. Session gates from IST clock: weekend or outside 09:15–15:30 → NO TRADE 'closed'; 09:15–09:40 'opening' note; ≥14:45 'no-new' → actionable verdicts become WAIT with square-off framing; ≥15:20 'squareoff'. Long stop = `max(vwap, ema20) below entry` (fallback: the one present, else day low) × (1−0.003), floored at day low; short mirrored with min(...)×1.003 capped at day high. target1 = entry+1.5R, target2 = entry+2.5R, dayRef = day high (long)/low (short); rr = (target1−entry)/(entry−stop) rounded 2dp (=1.5 by construction unless entryZone midpoint used); if entry−stop ≤ 0 or risk > 2.5% of entry → WAIT with reason. qtyAt1kRisk = floor(1000/riskPerShare). Missing vwap/ema/rsi: skip that signal, keep scraper warnings, cap confidence at 'medium' if ≥2 indicators missing.

- [ ] **Step 1: Write failing tests** — fixtures: strong bull (all bullish, in-session Tue 11:00 IST) → BUY NOW, long levels, rr ≥ 1.2; stretched bull (price 2% over VWAP) → BUY ON PULLBACK with entryZone; strong bear → SHORT NOW; mixed (above VWAP, RSI 35) → WAIT; 15:00 IST strong bull → WAIT + 'no-new'; Sunday → NO TRADE; missing ema20+rsi → verdict still returned, confidence ≤ medium, warnings preserved; stop-wider-than-2.5% → WAIT.
- [ ] **Step 2: Run** — expect failures. **Step 3: Implement.** **Step 4: Run** — pass. **Step 5: Commit** — `feat: intraday decision engine`.

### Task 4: content.js — legend scraper

**Files:**
- Create: `tradingview-intraday-assistant/content.js`

**Interfaces:**
- Produces: message listener — `chrome.runtime.onMessage` for `{type:'SCRAPE'}` → `sendResponse({ok:true, data})` or `{ok:false, error}`. `data` shape exactly what `analyze` consumes. Idempotent under double injection (guard `window.__tvIntradayAssistantLoaded`).

Scrape order: symbol/exchange from URL `symbol` param → fallback title regex `/^([A-Z0-9.&-]+)\s/`. Price+changePct from `document.title` regex (`name price ▲/▼ change%`) → fallback main legend. OHLC from the main series row: pair up `[class*="valueTitle"]`/`[class*="valueValue"]` spans inside `[data-name="legend-series-item"]` (fallback `[data-name="legend"]` first row). Studies: iterate `[data-name="legend-source-item"]`; row title = textContent of `[data-name="legend-source-title"]` (or the row's title area); match /vwap/i → vwap; /\bema\b|mov.*avg.*exp/i with arg 9 → ema9, arg 20 → ema20 (arg = first integer in row title text); /\brsi|relative strength/i → rsi (first numeric value in row); /^vol/i → volumeText (raw). Every value through `parseNum` (engine.js is also listed before content.js? content script can't import — duplicate `parseNum` locally as `parseNumLocal` matching Task 2 semantics, or list `engine.js` before `content.js` in manifest `content_scripts.js` — choose the manifest ordering approach, no duplication). Missing pieces → push spec'd warning strings.

- [ ] **Step 1: Implement content.js** (manifest `js: ["engine.js", "content.js"]` — update manifest from Task 1).
- [ ] **Step 2: Sanity-check in Node** that the file parses: `node --check content.js`.
- [ ] **Step 3: Commit** — `feat: TradingView legend scraper`.

### Task 5: popup — orchestration + UI

**Files:**
- Create: `tradingview-intraday-assistant/popup.html`, `popup.css`, `popup.js`

**Interfaces:**
- Consumes: `SCRAPE` message (Task 4), `IntradayEngine.analyze` (Task 3, loaded via `<script src="engine.js">`).

Flow in `popup.js`: get active tab → not `tradingview.com` → render guidance state. Else `chrome.tabs.sendMessage(tab.id, {type:'SCRAPE'})`; on `chrome.runtime.lastError` → `chrome.scripting.executeScript({target:{tabId}, files:['engine.js','content.js']})` → retry once → readable error state. On data: `analyze(data, new Date())` → render.

UI (dark, verdict-accented): header symbol • price • change%; verdict banner (green `#16a34a` buy-family / red `#dc2626` short / amber `#d97706` wait / gray no-trade) + confidence chip + headline; levels grid (Entry or Entry zone, Stop, T1, T2, Day ref, R:R, "Size @ ₹1,000 risk"); reasons list ✓ (bull) ✗ (bear) • (note); warnings strip; footer: IST timestamp, hover caveat, "Educational tool — not investment advice."

- [ ] **Step 1: Write popup.html/css/js.** **Step 2:** `node --check popup.js`. **Step 3: Commit** — `feat: verdict popup UI`.

### Task 6: Verify + docs polish

- [ ] **Step 1: Run full test suite** `node tests/engine.test.js` — all pass.
- [ ] **Step 2: Manual check instructions** confirmed present in README (load unpacked, open `https://www.tradingview.com/chart/?symbol=NSE%3AADANIENT`, add indicators, click icon).
- [ ] **Step 3: Final commit** of any remaining files.
