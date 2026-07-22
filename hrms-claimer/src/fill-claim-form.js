import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEFAULT_BILL_AMOUNT } from './config.js';
import { getAllFrames, clickAddRowIfPresent, waitForBillForm } from './page-context.js';
import { billDateCandidates } from './date-formats.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIELD_MAP_PATH = path.join(__dirname, '..', 'hrms-field-map.json');

function loadFieldMap() {
  try {
    if (fs.existsSync(FIELD_MAP_PATH)) {
      return JSON.parse(fs.readFileSync(FIELD_MAP_PATH, 'utf8'));
    }
  } catch { /* ignore */ }
  return {};
}

const FIELD_ALIASES = {
  billNo: ['bill no', 'bill no.', 'bill number', 'billno', 'receipt no', 'invoice no'],
  billDetails: ['bill details', 'details', 'description', 'particulars', 'narration', 'remarks'],
  billDate: ['bill date', 'date of bill', 'receipt date', 'invoice date'],
  billAmount: ['bill amount', 'amount', 'claim amount', 'total amount', 'expense amount'],
};

async function findFieldInFrame(frame, key) {
  const aliases = FIELD_ALIASES[key];
  return frame.evaluate(({ aliases: aliasList, keyName }) => {
    const isField = el => {
      if (!el || el.disabled) return false;
      if (el.tagName === 'TEXTAREA') return true;
      if (el.tagName !== 'INPUT') return false;
      const type = (el.type || 'text').toLowerCase();
      return !['hidden', 'file', 'button', 'submit', 'reset', 'image', 'checkbox', 'radio'].includes(type);
    };
    const score = text => {
      const n = (text || '').replace(/\s+/g, ' ').trim().toLowerCase();
      if (!n) return 0;
      let best = 0;
      for (const alias of aliasList) {
        const a = alias.toLowerCase();
        if (n === a) best = Math.max(best, 100);
        else if (n.includes(a)) best = Math.max(best, 80);
      }
      if (keyName === 'billAmount' && (n.includes('amount') || n.includes('amt'))) best = Math.max(best, 70);
      if (keyName === 'billDate' && n.includes('date')) best = Math.max(best, 70);
      return best;
    };
    const candidates = [];
    const consider = (el, labelText, strategy) => {
      if (!isField(el)) return;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return;
      const labelScore = score(labelText) || score(`${el.id} ${el.name} ${el.placeholder}`);
      if (labelScore < 60) return;
      candidates.push({ strategy, score: labelScore, id: el.id || '', name: el.name || '' });
    };
    for (const label of document.querySelectorAll('label')) {
      const text = label.textContent;
      const forId = label.getAttribute('for');
      if (forId) consider(document.getElementById(forId), text, 'label-for');
      label.querySelectorAll('input, textarea').forEach(el => consider(el, text, 'label-child'));
    }
    for (const cell of document.querySelectorAll('td, th')) {
      const text = cell.textContent.replace(/\s+/g, ' ').trim();
      if (!text || text.length > 50) continue;
      const row = cell.closest('tr');
      if (row) {
        for (const el of row.querySelectorAll('input, textarea')) {
          if (!cell.contains(el)) consider(el, text, 'table-row');
        }
      }
    }
    for (const el of document.querySelectorAll('input, textarea')) {
      const row = el.closest('tr');
      const rowText = row ? [...row.querySelectorAll('td, th')].map(c => c.textContent).join(' ') : '';
      consider(el, rowText, 'row-scan');
    }
    candidates.sort((a, b) => b.score - a.score);
    const best = candidates[0];
    if (!best) return null;
    if (best.id) return { kind: 'id', value: best.id, strategy: best.strategy };
    if (best.name) return { kind: 'name', value: best.name, strategy: best.strategy };
    return null;
  }, { aliases, keyName: key });
}

async function resolveFieldLocator(frame, located, mapSelector) {
  if (mapSelector) return frame.locator(mapSelector).first();
  if (!located) return null;
  if (located.kind === 'id') {
    const id = located.value;
    return /[$:.]/.test(id)
      ? frame.locator(`[id="${id.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`).first()
      : frame.locator(`#${id}`).first();
  }
  const name = located.value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return frame.locator(`[name="${name}"]`).first();
}

async function findFieldLocator(page, key) {
  const map = loadFieldMap();
  const frames = getAllFrames(page);
  if (map[key]) {
    for (const frame of frames) {
      const field = frame.locator(map[key]).first();
      if (await field.count()) return { frame, field, strategy: 'field-map' };
    }
  }
  for (const frame of frames) {
    const located = await findFieldInFrame(frame, key);
    const field = await resolveFieldLocator(frame, located, null);
    if (field && await field.count()) return { frame, field, strategy: located?.strategy || 'auto' };
  }
  return null;
}

async function readFieldValue(field) {
  try {
    return (await field.inputValue()).trim();
  } catch {
    return '';
  }
}

async function setFieldValueRobust(field, value, { kind = 'text', delayMs = 50 } = {}) {
  const target = String(value);
  await field.scrollIntoViewIfNeeded().catch(() => {});
  await field.click({ timeout: 5000 });
  await field.click({ clickCount: 3 }).catch(() => {});
  await field.press('ControlOrMeta+A').catch(() => field.press('Meta+A').catch(() => {}));
  await field.press('Backspace').catch(() => {});

  if (kind === 'amount') {
    const digits = target.replace(/[^\d.]/g, '');
    await field.pressSequentially(digits, { delay: delayMs });
  } else if (kind === 'date') {
    await field.pressSequentially(target, { delay: delayMs });
  } else {
    await field.fill(target);
  }

  await field.press('Tab').catch(() => {});
  await field.dispatchEvent('change').catch(() => {});
  await field.dispatchEvent('blur').catch(() => {});
  await field.evaluate(el => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }).catch(() => {});

  let actual = await readFieldValue(field);
  if (kind === 'amount') {
    const norm = s => s.replace(/[,\s₹]/g, '');
    if (norm(actual) === norm(target)) return true;
  } else if (actual.includes(target) || target.includes(actual)) {
    return true;
  }

  await field.evaluate((el, v) => {
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }, target);
  actual = await readFieldValue(field);
  return kind === 'amount'
    ? actual.replace(/[,\s₹]/g, '') === target.replace(/[,\s₹]/g, '')
    : actual.length > 0;
}

async function fillOneField(page, key, value, options) {
  const kind = key === 'billDate' ? 'date' : key === 'billAmount' ? 'amount' : 'text';
  const values = kind === 'date' && Array.isArray(value) ? value : [value];

  for (const tryValue of values) {
    const found = await findFieldLocator(page, key);
    if (!found) break;
    const ok = await setFieldValueRobust(found.field, tryValue, { kind, delayMs: options.delayMs });
    const actual = await readFieldValue(found.field);
    if (ok) {
      return { ok: true, value: tryValue, actual, strategy: found.strategy };
    }
  }
  return { ok: false, value: values[0] };
}

/**
 * Fill bill fields. Use phase:
 *  - 'basic' → Bill No + Details (before file upload)
 *  - 'amounts' → Bill Date + Amount (after file upload — avoids reset)
 *  - 'all' → everything in one go
 */
export async function fillClaimBillFields(page, data, options = {}) {
  const {
    amount = DEFAULT_BILL_AMOUNT,
    dateFormats = ['dd/MM/yyyy', 'dd-MM-yyyy', 'dd-MMM-yyyy'],
    delayMs = 60,
    phase = 'all',
    clickAdd = true,
  } = options;

  if (clickAdd) {
    const added = await clickAddRowIfPresent(page);
    if (added) console.log('  · Clicked Add / New row');
    await waitForBillForm(page);
  }

  const billNo = data.billNo;
  const billDetails = data.billDetails;
  const billAmount = String(amount ?? data.billAmount ?? DEFAULT_BILL_AMOUNT);
  const dateValues = billDateCandidates(data, dateFormats);

  const plan = [];
  if (phase === 'basic' || phase === 'all') {
    plan.push(['billNo', billNo], ['billDetails', billDetails]);
  }
  if (phase === 'amounts' || phase === 'all') {
    plan.push(['billDate', dateValues], ['billAmount', billAmount]);
  }

  const results = {};
  for (const [key, val] of plan) {
    const res = await fillOneField(page, key, val, { delayMs });
    results[key] = res.ok;
    const label = key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase());
    const display = Array.isArray(val) ? val[0] : val;
    const suffix = res.ok ? ` ✓ (${res.actual || display})` : ' (field not found / cleared)';
    console.log(`  · ${label}: ${display}${suffix}`);
  }

  return { results };
}

export async function verifyClaimFields(page, data, amount, dateFormats) {
  const checks = {
    billNo: data.billNo,
    billAmount: String(amount),
  };
  const issues = [];
  for (const [key, expected] of Object.entries(checks)) {
    const found = await findFieldLocator(page, key);
    if (!found) continue;
    const actual = await readFieldValue(found.field);
    if (key === 'billAmount') {
      if (actual.replace(/[,\s₹]/g, '') !== expected) issues.push(key);
    } else if (!actual.includes(expected)) {
      issues.push(key);
    }
  }
  return issues;
}
