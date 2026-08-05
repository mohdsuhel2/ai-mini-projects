'use strict';
// Shared by the popup, the content script and Node tests — keep browser-safe.

const IntradayEngine = (() => {

  const SUFFIX = { K: 1e3, M: 1e6, B: 1e9, L: 1e5, CR: 1e7 };

  function parseNum(text) {
    if (text === null || text === undefined) return null;
    let s = String(text).trim()
      .replace(/−/g, '-')      // Unicode minus
      .replace(/[,%\s ]/g, '')
      .replace(/^\+/, '');
    if (!s) return null;
    let mult = 1;
    const m = s.match(/(CR|K|M|B|L)$/i);
    if (m) {
      mult = SUFFIX[m[1].toUpperCase()];
      s = s.slice(0, -m[1].length);
    }
    if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
    return parseFloat(s) * mult;
  }

  // NSE session, minutes from midnight IST
  const OPEN = 9 * 60 + 15, CLOSE = 15 * 60 + 30;
  const OPENING_END = 9 * 60 + 40, NO_NEW = 14 * 60 + 45, SQUAREOFF = 15 * 60 + 20;
  const STOP_BUFFER = 0.003, MAX_RISK_PCT = 2.5, STRETCH_PCT = 1.0, RISK_RUPEES = 1000;

  function istSession(now) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata', weekday: 'short',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(now);
    const get = t => parts.find(p => p.type === t).value;
    const dow = get('weekday');
    const mins = (parseInt(get('hour'), 10) % 24) * 60 + parseInt(get('minute'), 10);
    if (dow === 'Sat' || dow === 'Sun' || mins < OPEN || mins > CLOSE) {
      return { phase: 'closed', label: 'Market closed' };
    }
    if (mins >= SQUAREOFF) return { phase: 'squareoff', label: 'Square-off window (3:20 PM+) — exit intraday positions' };
    if (mins >= NO_NEW) return { phase: 'no-new', label: 'Past 2:45 PM IST — no fresh intraday entries' };
    if (mins <= OPENING_END) return { phase: 'opening', label: 'Opening window (9:15–9:40) — volatile, size down' };
    return { phase: 'normal', label: 'Regular session' };
  }

  const r2 = x => Math.round(x * 100) / 100;

  function analyze(data, now) {
    const d = data || {};
    const session = istSession(now);
    const warnings = (d.warnings || []).slice();
    const reasons = [];
    let bull = 0, bear = 0;
    const addBull = t => { bull++; reasons.push({ side: 'bull', text: t }); };
    const addBear = t => { bear++; reasons.push({ side: 'bear', text: t }); };
    const note = t => reasons.push({ side: 'note', text: t });

    const { price, open, high, low, vwap, ema9, ema20, rsi, changePct } = d;

    if (price == null) {
      return {
        verdict: 'NO TRADE', confidence: 'low', direction: null,
        headline: "Couldn't read the price from the chart.",
        levels: null, reasons, warnings, score: { bull: 0, bear: 0, net: 0 }, session,
      };
    }

    // --- evidence ---
    if (vwap != null) {
      if (price > vwap) addBull(`Price above VWAP (${r2(price)} vs ${r2(vwap)}) — buyers in control today`);
      else if (price < vwap) addBear(`Price below VWAP (${r2(price)} vs ${r2(vwap)}) — sellers in control today`);
    }
    if (ema9 != null && ema20 != null) {
      if (price > ema9 && ema9 > ema20) addBull('Bullish EMA stack: price > EMA9 > EMA20');
      else if (price < ema9 && ema9 < ema20) addBear('Bearish EMA stack: price < EMA9 < EMA20');
      else note('EMAs not aligned — short-term trend is undecided');
    }
    if (rsi != null) {
      if (rsi > 60) addBull(`RSI ${r2(rsi)} — bullish momentum`);
      else if (rsi < 40) addBear(`RSI ${r2(rsi)} — bearish momentum`);
      else note(`RSI ${r2(rsi)} — neutral zone, no momentum edge`);
    }
    let rangePos = null;
    if (high != null && low != null && high > low) {
      rangePos = (price - low) / (high - low);
      if (rangePos > 0.7) addBull(`Trading in the top ${Math.round((1 - rangePos) * 100)}% of the day's range — strength`);
      else if (rangePos < 0.3) addBear('Trading near the day\'s low — weakness');
    }
    if (changePct != null) {
      if (changePct >= 0.75) addBull(`Up ${r2(changePct)}% on the day`);
      else if (changePct <= -0.75) addBear(`Down ${r2(Math.abs(changePct))}% on the day`);
    }

    // gap context (derived, informational)
    if (changePct != null && open != null) {
      const prevClose = price / (1 + changePct / 100);
      const gapPct = ((open - prevClose) / prevClose) * 100;
      if (Math.abs(gapPct) >= 0.3) note(`${gapPct > 0 ? 'Gap-up' : 'Gap-down'} open of ${r2(Math.abs(gapPct))}% vs previous close`);
    }

    const stretchPct = vwap != null ? ((price - vwap) / vwap) * 100 : null;
    const rsiExtreme = rsi != null && (rsi > 75 || rsi < 25);
    if (rsiExtreme) note(`RSI ${r2(rsi)} is at an extreme — late to the move, expect shakeouts`);

    const missing = ['vwap', 'ema9', 'ema20', 'rsi'].filter(k => d[k] == null).length;
    const net = bull - bear;
    const score = { bull, bear, net };

    let confidence = Math.abs(net) >= 5 ? 'high' : Math.abs(net) >= 3 ? 'medium' : 'low';
    const capMedium = () => { if (confidence === 'high') confidence = 'medium'; };
    if (rsiExtreme) capMedium();
    if (missing >= 2) capMedium();

    // --- session gates ---
    if (session.phase === 'closed') {
      return {
        verdict: 'NO TRADE', confidence: 'low', direction: null,
        headline: 'Market is closed — no intraday trade. Review the setup for the next session.',
        levels: null, reasons, warnings, score, session,
      };
    }
    if (session.phase === 'opening') note('Opening window (9:15–9:40 IST): spreads and whipsaws are worst now — size down');

    const direction = net >= 3 ? 'long' : net <= -3 ? 'short' : null;

    if ((session.phase === 'no-new' || session.phase === 'squareoff') && direction) {
      note(session.label + ' — the edge of a fresh entry is gone; carry decisions only');
      return {
        verdict: 'WAIT', confidence: 'low', direction,
        headline: `${direction === 'long' ? 'Bullish' : 'Bearish'} chart, but it's too late in the session for a fresh intraday entry.`,
        levels: null, reasons, warnings, score, session,
      };
    }

    if (!direction) {
      const wants = net > 0
        ? 'More bullish alignment — price holding above VWAP with EMA9 > EMA20 and RSI pushing above 60 would turn this into a long.'
        : net < 0
          ? 'More bearish alignment — a clean break below VWAP with RSI under 40 would turn this into a short.'
          : 'A decisive move: price taking out VWAP with volume in either direction.';
      return {
        verdict: 'WAIT', confidence, direction: null,
        headline: `Mixed evidence (${bull} bullish vs ${bear} bearish) — no edge here. ${wants}`,
        levels: null, reasons, warnings, score, session,
      };
    }

    // --- levels ---
    let verdict, entry = price, entryZone = null, stop, dayRef;
    if (direction === 'long') {
      const stretched = stretchPct != null && stretchPct > STRETCH_PCT;
      if (stretched) {
        verdict = 'BUY ON PULLBACK';
        const zLo = Math.min(vwap, ema9 != null ? ema9 : vwap);
        const zHi = Math.max(vwap, ema9 != null ? ema9 : vwap);
        entryZone = [zLo, zHi];
        entry = (zLo + zHi) / 2;
        note(`Price is ${r2(stretchPct)}% above VWAP — chasing here is poor R:R; let it come back to the EMA9–VWAP zone`);
      } else {
        verdict = 'BUY NOW';
      }
      const structure = [vwap, ema20].filter(v => v != null && v < entry);
      let base = structure.length ? Math.max(...structure) : low;
      if (base == null) {
        return {
          verdict: 'WAIT', confidence: 'low', direction,
          headline: 'Bullish, but no structure (VWAP/EMA20/day low) to anchor a stop — wait for levels to form.',
          levels: null, reasons, warnings, score, session,
        };
      }
      stop = base * (1 - STOP_BUFFER);
      if (low != null) stop = Math.max(stop, low * (1 - STOP_BUFFER));
      dayRef = high;
    } else {
      verdict = 'SHORT NOW';
      if (stretchPct != null && stretchPct < -STRETCH_PCT) {
        note(`Price is ${r2(Math.abs(stretchPct))}% below VWAP — extended; a weak bounce toward VWAP is the better short entry`);
      }
      const structure = [vwap, ema20].filter(v => v != null && v > entry);
      let base = structure.length ? Math.min(...structure) : high;
      if (base == null) {
        return {
          verdict: 'WAIT', confidence: 'low', direction,
          headline: 'Bearish, but no structure (VWAP/EMA20/day high) to anchor a stop — wait for levels to form.',
          levels: null, reasons, warnings, score, session,
        };
      }
      stop = base * (1 + STOP_BUFFER);
      if (high != null) stop = Math.min(stop, high * (1 + STOP_BUFFER));
      dayRef = low;
    }

    const risk = direction === 'long' ? entry - stop : stop - entry;
    const riskPct = (risk / entry) * 100;
    if (risk <= 0 || riskPct > MAX_RISK_PCT) {
      reasons.push({
        side: 'note',
        text: risk <= 0
          ? 'Stop structure sits on the wrong side of entry — setup is not clean'
          : `Nearest structural stop is ${r2(riskPct)}% away (max ${MAX_RISK_PCT}%) — the risk is too wide from here`,
      });
      return {
        verdict: 'WAIT', confidence: 'low', direction,
        headline: `${direction === 'long' ? 'Bullish' : 'Bearish'} setup, bad location — the stop is too far. Wait for price to come closer to structure.`,
        levels: null, reasons, warnings, score, session,
      };
    }

    const sign = direction === 'long' ? 1 : -1;
    const target1 = entry + sign * 1.5 * risk;
    const target2 = entry + sign * 2.5 * risk;
    const rr = r2(Math.abs(target1 - entry) / risk);
    const levels = {
      entry: r2(entry),
      entryZone: entryZone ? [r2(entryZone[0]), r2(entryZone[1])] : null,
      stop: r2(stop),
      target1: r2(target1),
      target2: r2(target2),
      dayRef: dayRef != null ? r2(dayRef) : null,
      rr,
      qtyAt1kRisk: Math.floor(RISK_RUPEES / risk),
    };

    const headline = verdict === 'BUY ON PULLBACK'
      ? `Uptrend intact but extended — stalk the ${levels.entryZone[0]}–${levels.entryZone[1]} pullback zone instead of chasing.`
      : direction === 'long'
        ? `Buyers control the tape (${bull} bullish signals) and price is at a workable location — long with a stop under ${levels.stop}.`
        : `Sellers control the tape (${bear} bearish signals) — short with a stop above ${levels.stop}.`;

    return { verdict, confidence, direction, headline, levels, reasons, warnings, score, session };
  }

  return { parseNum, analyze };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = IntradayEngine;
if (typeof globalThis !== 'undefined') globalThis.IntradayEngine = IntradayEngine;
