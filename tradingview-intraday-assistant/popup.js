'use strict';

const app = document.getElementById('app');
const esc = s => String(s).replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-IN', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});

function renderState(title, body) {
  app.innerHTML = `<div class="state"><strong>${esc(title)}</strong>${esc(body)}</div>`;
}

const VERDICT_CLASS = {
  'BUY NOW': 'buy',
  'BUY ON PULLBACK': 'buy',
  'SHORT NOW': 'short',
  'WAIT': 'wait',
  'NO TRADE': 'none',
};
const REASON_ICON = { bull: '✓', bear: '✗', note: '•' };

function render(data, result) {
  const chgClass = (data.changePct ?? 0) >= 0 ? 'up' : 'down';
  const chgText = data.changePct == null ? '' :
    `${data.changePct >= 0 ? '+' : ''}${fmt(data.changePct)}%`;

  const L = result.levels;
  const entryLabel = L && L.entryZone ? 'Entry zone' : 'Entry';
  const entryValue = L ? (L.entryZone ? `${fmt(L.entryZone[0])} – ${fmt(L.entryZone[1])}` : fmt(L.entry)) : null;
  const dayRefLabel = result.direction === 'short' ? 'Day low ref' : 'Day high ref';

  const now = new Date();
  const ist = now.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: true });

  app.innerHTML = `
    <div class="header">
      <span class="sym">${esc(data.symbol || '?')}</span>
      <span class="exch">${esc(data.exchange || '')}</span>
      <span class="px">${fmt(data.price)}</span>
      <span class="chg ${chgClass}">${esc(chgText)}</span>
    </div>

    <div class="verdict ${VERDICT_CLASS[result.verdict] || 'none'}">
      <div class="v-row">
        <span class="v-text">${esc(result.verdict)}</span>
        <span class="conf">${esc(result.confidence)} confidence</span>
      </div>
      <div class="headline">${esc(result.headline)}</div>
    </div>

    ${L ? `
    <div class="levels">
      <div class="kv wide"><span class="k">${entryLabel}</span><span class="v">${esc(entryValue)}</span></div>
      <div class="kv"><span class="k">Stop-loss</span><span class="v">${fmt(L.stop)}</span></div>
      <div class="kv"><span class="k">R:R (T1)</span><span class="v">1 : ${fmt(L.rr)}</span></div>
      <div class="kv"><span class="k">Target 1</span><span class="v">${fmt(L.target1)}</span></div>
      <div class="kv"><span class="k">Target 2</span><span class="v">${fmt(L.target2)}</span></div>
      <div class="kv"><span class="k">${dayRefLabel}</span><span class="v">${fmt(L.dayRef)}</span></div>
      <div class="kv"><span class="k">Size @ ₹1k risk</span><span class="v">${L.qtyAt1kRisk} sh</span></div>
    </div>` : ''}

    <div class="section-title">Why</div>
    <ul class="reasons">
      ${result.reasons.map(r => `
        <li class="${esc(r.side)}"><span class="ic">${REASON_ICON[r.side] || '•'}</span><span>${esc(r.text)}</span></li>
      `).join('')}
    </ul>

    ${result.warnings.length ? `
    <div class="warnings">${result.warnings.map(w => `<div>⚠ ${esc(w)}</div>`).join('')}</div>` : ''}

    <div class="session">${esc(result.session.label)} · ${esc(ist)} IST</div>

    <div class="footer">
      <div>Legend shows the hovered bar — keep the mouse off old candles.</div>
      <div>Educational tool — not investment advice.</div>
    </div>
  `;
}

function sendScrape(tabId) {
  return new Promise(resolve => {
    chrome.tabs.sendMessage(tabId, { type: 'SCRAPE' }, response => {
      if (chrome.runtime.lastError) resolve({ ok: false, injectNeeded: true, error: chrome.runtime.lastError.message });
      else resolve(response || { ok: false, error: 'Empty response from the page.' });
    });
  });
}

async function main() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:\/\/([^/]*\.)?tradingview\.com\//.test(tab.url || '')) {
    renderState('Open a TradingView chart first',
      'Go to tradingview.com, open a chart (e.g. NSE:ADANIENT), then click this icon again.');
    return;
  }

  let response = await sendScrape(tab.id);
  if (!response.ok && response.injectNeeded) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['engine.js', 'content.js'] });
      response = await sendScrape(tab.id);
    } catch (e) {
      response = { ok: false, error: `Couldn't access the page: ${e.message}` };
    }
  }

  if (!response.ok) {
    renderState('Couldn\'t read the chart', response.error || 'Unknown error. Reload the TradingView tab and try again.');
    return;
  }

  const result = IntradayEngine.analyze(response.data, new Date());
  render(response.data, result);
}

main().catch(e => renderState('Something went wrong', e.message));
