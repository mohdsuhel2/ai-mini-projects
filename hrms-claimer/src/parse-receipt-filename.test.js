import { parseReceiptFilename, listReceiptFiles, DOCFORGE_FUEL_PATTERN } from './parse-receipt-filename.js';
import assert from 'node:assert/strict';

const sample = parseReceiptFilename('2026AA3210_14Jan2026.png');
assert.equal(sample.billNo, '2026AA3210');
assert.equal(sample.billDateDisplay, '14/01/2026');
assert.equal(sample.billDetails, '2026AA3210 - 14 Jan 2026');
assert.equal(sample.billAmount, 2800);

assert.throws(() => parseReceiptFilename('bad-name.pdf'));

assert.match('X_01Jan2026.png', DOCFORGE_FUEL_PATTERN);
console.log('parse-receipt-filename: ok');
