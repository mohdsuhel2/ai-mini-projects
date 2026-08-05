'use strict';
const assert = require('node:assert');
const { parseNum } = require('../engine.js');

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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
