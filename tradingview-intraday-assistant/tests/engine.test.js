'use strict';
const assert = require('node:assert');
const { parseNum, analyze } = require('../engine.js');

let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); pass++; console.log(`  ok  ${name}`); }
  catch (e) { fail++; console.error(`FAIL  ${name}\n      ${e.message}`); }
}

console.log('parseNum');
t('plain number', () => assert.strictEqual(parseNum('2455.35'), 2455.35));
t('thousands commas', () => assert.strictEqual(parseNum('2,455.35'), 2455.35));
t('unicode minus', () => assert.strictEqual(parseNum('−12.34'), -12.34));
t('ascii negative', () => assert.strictEqual(parseNum('-0.5'), -0.5));
t('K suffix', () => assert.strictEqual(parseNum('12.5K'), 12500));
t('M suffix', () => assert.strictEqual(parseNum('1.23M'), 1230000));
t('B suffix', () => assert.strictEqual(parseNum('2B'), 2e9));
t('L (lakh) suffix', () => assert.strictEqual(parseNum('3.2L'), 320000));
t('Cr (crore) suffix', () => assert.strictEqual(parseNum('1.5Cr'), 15000000));
t('percent sign stripped', () => assert.strictEqual(parseNum('+1.25%'), 1.25));
t('garbage is null', () => assert.strictEqual(parseNum('n/a'), null));
t('empty is null', () => assert.strictEqual(parseNum(''), null));
t('null is null', () => assert.strictEqual(parseNum(null), null));

// ---- analyze() fixtures ----
// 2026-08-04 is a Tuesday.
const TUE_11AM = new Date('2026-08-04T11:00:00+05:30');
const TUE_930AM = new Date('2026-08-04T09:30:00+05:30');
const TUE_3PM = new Date('2026-08-04T15:00:00+05:30');
const SUNDAY = new Date('2026-08-09T11:00:00+05:30');

function base(overrides) {
  return Object.assign({
    symbol: 'ADANIENT', exchange: 'NSE',
    price: 2500, open: 2460, high: 2510, low: 2450, changePct: 1.8,
    vwap: 2482, ema9: 2492, ema20: 2480, rsi: 65,
    volumeText: '1.2M', warnings: [],
  }, overrides);
}

const strongBull = base({});
const stretchedBull = base({ price: 2550, high: 2555, ema9: 2540, rsi: 68 });
const strongBear = base({
  price: 2400, open: 2460, high: 2470, low: 2395, changePct: -1.9,
  vwap: 2430, ema9: 2410, ema20: 2435, rsi: 32,
});
const mixed = base({
  price: 2500, open: 2490, high: 2520, low: 2470, changePct: 0.3,
  vwap: 2490, ema9: 2505, ema20: 2495, rsi: 35,
});
const wideStop = base({
  price: 100, open: 99, high: 100.5, low: 95.8, changePct: 1.2,
  vwap: null, ema9: 99, ema20: 96, rsi: 63,
  warnings: ['VWAP not on chart'],
});

console.log('\nanalyze: verdicts');
t('strong bull mid-session -> BUY NOW', () => {
  const r = analyze(strongBull, TUE_11AM);
  assert.strictEqual(r.verdict, 'BUY NOW');
  assert.strictEqual(r.direction, 'long');
  assert.ok(r.score.net >= 3, `net ${r.score.net}`);
});
t('strong bull has sane long levels', () => {
  const r = analyze(strongBull, TUE_11AM);
  assert.ok(r.levels, 'levels missing');
  assert.strictEqual(r.levels.entry, 2500);
  assert.ok(r.levels.stop < 2500 && r.levels.stop >= 2450, `stop ${r.levels.stop}`);
  assert.ok(r.levels.target1 > 2500 && r.levels.target2 > r.levels.target1);
  assert.ok(r.levels.rr >= 1.2, `rr ${r.levels.rr}`);
  assert.ok(r.levels.qtyAt1kRisk >= 1);
});
t('stretched bull -> BUY ON PULLBACK with entry zone', () => {
  const r = analyze(stretchedBull, TUE_11AM);
  assert.strictEqual(r.verdict, 'BUY ON PULLBACK');
  assert.ok(r.levels && r.levels.entryZone, 'entryZone missing');
  const [lo, hi] = r.levels.entryZone;
  assert.ok(lo < hi && lo === 2482 && hi === 2540, `zone ${lo}-${hi}`);
});
t('strong bear -> SHORT NOW with mirrored levels', () => {
  const r = analyze(strongBear, TUE_11AM);
  assert.strictEqual(r.verdict, 'SHORT NOW');
  assert.strictEqual(r.direction, 'short');
  assert.ok(r.levels.stop > 2400, `stop ${r.levels.stop}`);
  assert.ok(r.levels.target1 < 2400 && r.levels.target2 < r.levels.target1);
});
t('mixed evidence -> WAIT with no levels', () => {
  const r = analyze(mixed, TUE_11AM);
  assert.strictEqual(r.verdict, 'WAIT');
  assert.strictEqual(r.levels, null);
});
t('stop wider than 2.5% downgrades to WAIT', () => {
  const r = analyze(wideStop, TUE_11AM);
  assert.strictEqual(r.verdict, 'WAIT');
  assert.ok(r.reasons.some(x => /stop|risk/i.test(x.text)), 'no risk reason given');
});

console.log('\nanalyze: session gates');
t('Sunday -> NO TRADE (closed)', () => {
  const r = analyze(strongBull, SUNDAY);
  assert.strictEqual(r.verdict, 'NO TRADE');
  assert.strictEqual(r.session.phase, 'closed');
});
t('after 14:45 strong bull -> WAIT (no fresh entries)', () => {
  const r = analyze(strongBull, TUE_3PM);
  assert.strictEqual(r.verdict, 'WAIT');
  assert.strictEqual(r.session.phase, 'no-new');
});
t('opening window keeps verdict but adds caution', () => {
  const r = analyze(strongBull, TUE_930AM);
  assert.strictEqual(r.verdict, 'BUY NOW');
  assert.strictEqual(r.session.phase, 'opening');
  assert.ok(r.reasons.some(x => x.side === 'note' && /opening|volatil/i.test(x.text)));
});

console.log('\nanalyze: degradation');
t('missing indicators still yields verdict, capped confidence', () => {
  const r = analyze(base({ ema20: null, rsi: null, warnings: ['EMA 20 not on chart', 'RSI not on chart'] }), TUE_11AM);
  assert.ok(r.verdict, 'no verdict');
  assert.notStrictEqual(r.confidence, 'high');
  assert.ok(r.warnings.length >= 2, 'warnings dropped');
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
