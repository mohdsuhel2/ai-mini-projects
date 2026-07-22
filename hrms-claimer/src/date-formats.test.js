import assert from 'node:assert/strict';
import { datesEquivalent, normalizeDateString, amountsEquivalent, normalizeAmountString } from './date-formats.js';

assert.equal(normalizeDateString('14/01/2026'), '14/01/2026');
assert.equal(normalizeDateString('4/01/2026'), '04/01/2026');
assert.ok(datesEquivalent('14/01/2026', '14/01/2026'));
assert.ok(!datesEquivalent('14/01/2026', '4/01/2026'));
assert.ok(!datesEquivalent('14/01/2026', '04/01/2026'));

assert.equal(normalizeAmountString('2800'), '2800');
assert.equal(normalizeAmountString('₹2,800'), '2800');
assert.ok(amountsEquivalent('2800', '2800'));
assert.ok(!amountsEquivalent('2800', '800'));
assert.ok(amountsEquivalent('2800', '2,800'));

console.log('date-formats: ok');
