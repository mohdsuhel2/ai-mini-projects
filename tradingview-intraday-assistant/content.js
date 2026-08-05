'use strict';
// Scrapes the TradingView chart page. Matches legend rows by *title text*, never
// by TradingView's hashed class names — attribute/substring selectors only.
// engine.js is loaded before this file (manifest order) for IntradayEngine.parseNum.

(() => {
  if (window.__tvIntradayAssistantLoaded) return;
  window.__tvIntradayAssistantLoaded = true;

  const parseNum = globalThis.IntradayEngine.parseNum;

  const ADD_HINTS = {
    vwap: 'VWAP not on chart — add "Volume Weighted Average Price"',
    ema9: 'EMA 9 not on chart — add "Moving Average Exponential" with length 9',
    ema20: 'EMA 20 not on chart — add "Moving Average Exponential" with length 20',
    rsi: 'RSI not on chart — add "Relative Strength Index" (length 14)',
  };

  function symbolFromPage() {
    const param = new URL(location.href).searchParams.get('symbol'); // "NSE:ADANIENT"
    if (param && param.includes(':')) {
      const [exchange, symbol] = param.split(':');
      return { symbol, exchange };
    }
    if (param) return { symbol: param, exchange: null };
    const m = document.title.match(/^([A-Z0-9.&-]+)\b/);
    return { symbol: m ? m[1] : null, exchange: null };
  }

  // Title looks like: "ADANIENT 2,455.30 ▲ +1.2% — TradingView" (arrow/format varies)
  function priceFromTitle() {
    const m = document.title.match(/\s([\d,]+(?:\.\d+)?)\s*[▲▼]?\s*([+\-−]\d+(?:\.\d+)?)%/);
    if (!m) return { price: null, changePct: null };
    return { price: parseNum(m[1]), changePct: parseNum(m[2]) };
  }

  function valueTexts(row) {
    return [...row.querySelectorAll('[class*="valueValue"]')].map(el => el.textContent.trim());
  }

  // The main series row: O/H/L/C labelled value items.
  function scrapeOHLC() {
    const out = { open: null, high: null, low: null, close: null, changePct: null };
    const row = document.querySelector('[data-name="legend-series-item"]')
      || document.querySelector('[data-name="legend"] [class*="item"]');
    if (!row) return out;
    const items = row.querySelectorAll('[class*="valueItem"]');
    const byLabel = { O: 'open', H: 'high', L: 'low', C: 'close' };
    for (const item of items) {
      const label = item.querySelector('[class*="valueTitle"]')?.textContent.trim() || '';
      const valText = item.querySelector('[class*="valueValue"]')?.textContent.trim() || '';
      if (byLabel[label]) out[byLabel[label]] = parseNum(valText);
      else if (/%\s*$/.test(valText)) out.changePct = parseNum(valText);
    }
    return out;
  }

  function studyTitleText(row) {
    const titleEl = row.querySelector('[data-name="legend-source-title"]');
    if (!titleEl) return row.textContent.trim();
    // Include sibling "args" spans (e.g. EMA's "9 close 0") that live next to the title.
    const scope = titleEl.closest('[class*="titles"]') || titleEl.parentElement || titleEl;
    return scope.textContent.trim();
  }

  function scrapeStudies() {
    const out = { vwap: null, ema9: null, ema20: null, rsi: null, volumeText: null };
    for (const row of document.querySelectorAll('[data-name="legend-source-item"]')) {
      const title = studyTitleText(row);
      const values = valueTexts(row);
      const firstNum = values.map(parseNum).find(v => v !== null);
      if (firstNum === undefined && !/^vol/i.test(title)) continue;

      if (/vwap|volume weighted/i.test(title)) {
        if (out.vwap === null) out.vwap = firstNum ?? null;
      } else if (/\bema\b|moving average exp|mov\s*avg\s*exp|\bma exp/i.test(title)) {
        const len = (title.match(/\b(\d+)\b/) || [])[1];
        if (len === '9' && out.ema9 === null) out.ema9 = firstNum ?? null;
        else if (len === '20' && out.ema20 === null) out.ema20 = firstNum ?? null;
      } else if (/\brsi\b|relative strength/i.test(title)) {
        if (out.rsi === null) out.rsi = firstNum ?? null;
      } else if (/^vol/i.test(title)) {
        if (out.volumeText === null) out.volumeText = values[0] || null;
      }
    }
    return out;
  }

  function scrape() {
    const { symbol, exchange } = symbolFromPage();
    const titleData = priceFromTitle();
    const ohlc = scrapeOHLC();
    const studies = scrapeStudies();
    const warnings = [];

    const price = titleData.price ?? ohlc.close;
    const changePct = titleData.changePct ?? ohlc.changePct;

    if (price === null) {
      return { ok: false, error: "Couldn't read the price from this page. Make sure a chart is open and the legend is visible." };
    }
    for (const key of ['vwap', 'ema9', 'ema20', 'rsi']) {
      if (studies[key] === null) warnings.push(ADD_HINTS[key]);
    }
    if (ohlc.open === null || ohlc.high === null || ohlc.low === null) {
      warnings.push("Couldn't read full OHLC from the legend — day-range signals skipped");
    }

    return {
      ok: true,
      data: {
        symbol, exchange, price,
        open: ohlc.open, high: ohlc.high, low: ohlc.low,
        changePct,
        vwap: studies.vwap, ema9: studies.ema9, ema20: studies.ema20, rsi: studies.rsi,
        volumeText: studies.volumeText,
        warnings,
      },
    };
  }

  if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (msg && msg.type === 'SCRAPE') {
        try { sendResponse(scrape()); }
        catch (e) { sendResponse({ ok: false, error: `Scrape failed: ${e.message}` }); }
      }
      return false;
    });
  }
})();
