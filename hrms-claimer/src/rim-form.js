import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEFAULT_BILL_AMOUNT } from './config.js';
import { billDateCandidates, datesEquivalent, amountsEquivalent } from './date-formats.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIELD_MAP_PATH = path.join(__dirname, '..', 'hrms-field-map.json');

const ROW_LABELS = {
  billNo: /^Bill\s*No\.?$/i,
  billDate: /^Bill\s*Date$/i,
  billDetails: /^Bill\s*Details$/i,
  billAmount: /^Bill\s*Amount$/i,
};

const DEFAULT_SELECTORS = {
  billNo: '#MiddleContent_gvRimFields_txtField_Value_0',
  billDate: '#MiddleContent_gvRimFields_calField_Value_1_txtCalendar_1',
  billDetails: '#MiddleContent_gvRimFields_txtField_Value_2',
  billAmount: '#MiddleContent_gvRimFields_numField_Value_3',
};

function loadSelectors() {
  try {
    if (fs.existsSync(FIELD_MAP_PATH)) {
      return { ...DEFAULT_SELECTORS, ...JSON.parse(fs.readFileSync(FIELD_MAP_PATH, 'utf8')) };
    }
  } catch { /* ignore */ }
  return DEFAULT_SELECTORS;
}

async function readFieldValue(field) {
  try {
    return (await field.inputValue()).trim();
  } catch {
    return '';
  }
}

async function clearField(field) {
  await field.click();
  await field.press('ControlOrMeta+A').catch(() => {});
  await field.press('Delete').catch(() => {});
  await field.fill('').catch(() => {});
}

async function setDateFieldValue(field, expected, delayMs = 80) {
  const digits = expected.replace(/\D/g, '');

  const strategies = [
    async () => {
      await clearField(field);
      await field.evaluate((el, value) => {
        el.removeAttribute('readonly');
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
      }, expected);
    },
    async () => {
      await clearField(field);
      await field.fill(expected);
      await field.dispatchEvent('change').catch(() => {});
      await field.dispatchEvent('blur').catch(() => {});
    },
    async () => {
      await clearField(field);
      await field.pressSequentially(digits, { delay: delayMs });
    },
    async () => {
      await clearField(field);
      await field.pressSequentially(expected, { delay: delayMs });
    },
  ];

  for (const run of strategies) {
    await run();
    await field.press('Tab').catch(() => {});
    const actual = await readFieldValue(field);
    if (datesEquivalent(expected, actual)) return { ok: true, actual };
    await field.click();
  }

  return { ok: false, actual: await readFieldValue(field) };
}

async function setAmountFieldValue(field, expected, delayMs = 80) {
  const digits = String(expected).replace(/[^\d.]/g, '');

  const strategies = [
    async () => {
      await clearField(field);
      await field.evaluate((el, value) => {
        el.removeAttribute('readonly');
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
      }, digits);
    },
    async () => {
      await clearField(field);
      await field.fill(digits);
      await field.dispatchEvent('change').catch(() => {});
      await field.dispatchEvent('blur').catch(() => {});
    },
    async () => {
      await clearField(field);
      await field.pressSequentially(digits, { delay: delayMs });
    },
  ];

  for (const run of strategies) {
    await run();
    await field.press('Tab').catch(() => {});
    const actual = await readFieldValue(field);
    if (amountsEquivalent(expected, actual)) return { ok: true, actual };
    await field.click();
  }

  return { ok: false, actual: await readFieldValue(field) };
}

async function fillBySelector(page, selector, value, { kind = 'text', delayMs = 80 } = {}) {
  const field = page.locator(selector).first();
  await field.waitFor({ state: 'visible', timeout: 15_000 });
  await field.scrollIntoViewIfNeeded();

  const text = String(value);

  if (kind === 'date') {
    const result = await setDateFieldValue(field, text, delayMs);
    return result.ok;
  }

  if (kind === 'amount') {
    const result = await setAmountFieldValue(field, text, delayMs);
    return result.ok;
  }

  await clearField(field);
  await field.fill(text);

  await field.press('Tab').catch(() => {});
  await field.dispatchEvent('change').catch(() => {});
  await field.dispatchEvent('blur').catch(() => {});

  const actual = await readFieldValue(field);
  return actual.length > 0;
}

async function fillByRowLabel(page, rowPattern, value, opts) {
  const row = page.locator('tr').filter({ has: page.locator('td, th').filter({ hasText: rowPattern }) }).first();
  if (await row.count()) {
    const input = row.locator('input[type="text"], input:not([type]), textarea').first();
    if (await input.count()) {
      const id = await input.getAttribute('id');
      if (id) return fillBySelector(page, `#${id.replace(/:/g, '\\:')}`, value, opts);
    }
  }
  return false;
}

async function fillField(page, key, value, selectors, opts) {
  const kind = key === 'billDate' ? 'date' : key === 'billAmount' ? 'amount' : 'text';
  const values = kind === 'date' && Array.isArray(value) ? value : [value];

  for (const tryVal of values) {
    if (selectors[key]) {
      const field = page.locator(selectors[key]).first();
      if (kind === 'date') {
        await field.waitFor({ state: 'visible', timeout: 15_000 });
        await field.scrollIntoViewIfNeeded();
        const result = await setDateFieldValue(field, tryVal, opts.delayMs);
        if (result.ok) return { ok: true, value: tryVal, actual: result.actual };
        if (ROW_LABELS[key]) {
          const rowOk = await fillByRowLabel(page, ROW_LABELS[key], tryVal, { ...opts, kind });
          if (rowOk) return { ok: true, value: tryVal };
        }
        return { ok: false, value: tryVal, actual: result.actual };
      }
      if (kind === 'amount') {
        await field.waitFor({ state: 'visible', timeout: 15_000 });
        await field.scrollIntoViewIfNeeded();
        const result = await setAmountFieldValue(field, tryVal, opts.delayMs);
        if (result.ok) return { ok: true, value: tryVal, actual: result.actual };
        if (ROW_LABELS[key]) {
          const rowOk = await fillByRowLabel(page, ROW_LABELS[key], tryVal, { ...opts, kind });
          if (rowOk) return { ok: true, value: tryVal };
        }
        return { ok: false, value: tryVal, actual: result.actual };
      }
      if (await fillBySelector(page, selectors[key], tryVal, { ...opts, kind })) {
        return { ok: true, value: tryVal };
      }
    }
    if (ROW_LABELS[key] && await fillByRowLabel(page, ROW_LABELS[key], tryVal, { ...opts, kind })) {
      return { ok: true, value: tryVal };
    }
  }
  return { ok: false, value: values[0] };
}

/**
 * Fill GT HRMS RimRequest.aspx fuel popup — uses exact field map from inspect.
 */
export async function fillRimFuelForm(rimPage, data, options = {}) {
  const {
    amount = DEFAULT_BILL_AMOUNT,
    dateFormats = ['dd/MM/yyyy'],
    delayMs = 80,
    phase = 'all',
  } = options;

  const selectors = loadSelectors();
  const billAmount = String(amount ?? data.billAmount ?? DEFAULT_BILL_AMOUNT);
  const dateValues = billDateCandidates(data, dateFormats);

  const plan = [];
  if (phase === 'basic' || phase === 'all') {
    plan.push(['billNo', data.billNo], ['billDetails', data.billDetails]);
  }
  if (phase === 'amounts' || phase === 'all') {
    plan.push(['billDate', dateValues], ['billAmount', billAmount]);
  }

  const results = {};
  for (const [key, val] of plan) {
    const res = await fillField(rimPage, key, val, selectors, { delayMs });
    results[key] = res.ok;
    const label = key.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase());
    const display = Array.isArray(val) ? val[0] : val;
    const suffix = res.ok ? ' ✓' : ` ✗ (got "${res.actual ?? 'empty'}")`;
    console.log(`  · ${label}: ${display}${suffix}`);
  }
  return results;
}

export async function uploadRimAttachment(rimPage, filePath) {
  const input = rimPage.locator('input[type="file"]').first();
  if (await input.count()) {
    await input.setInputFiles(filePath);
    return true;
  }
  return false;
}
