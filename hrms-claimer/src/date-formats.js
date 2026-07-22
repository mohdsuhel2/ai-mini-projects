import { parseReceiptFilename } from './parse-receipt-filename.js';

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function formatBillDate(date, format) {
  const d = date instanceof Date ? date : new Date(date);
  const pad = n => String(n).padStart(2, '0');
  const day = pad(d.getDate());
  const monthNum = pad(d.getMonth() + 1);
  const monthShort = MONTH_NAMES[d.getMonth()];
  const year = d.getFullYear();

  switch (format) {
    case 'dd/MM/yyyy': return `${day}/${monthNum}/${year}`;
    case 'dd-MM-yyyy': return `${day}-${monthNum}-${year}`;
    case 'dd/MMM/yyyy': return `${day}/${monthShort}/${year}`;
    case 'dd-MMM-yyyy': return `${day}-${monthShort}-${year}`;
    default: return `${day}/${monthNum}/${year}`;
  }
}

export function normalizeDateString(value) {
  const m = String(value).trim().match(/(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})/);
  if (!m) return null;
  const year = m[3].length === 2 ? `20${m[3]}` : m[3];
  return `${m[1].padStart(2, '0')}/${m[2].padStart(2, '0')}/${year}`;
}

export function datesEquivalent(expected, actual) {
  const a = normalizeDateString(expected);
  const b = normalizeDateString(actual);
  return Boolean(a && b && a === b);
}

export function normalizeAmountString(value) {
  const cleaned = String(value).replace(/[,\s₹]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  if (Number.isNaN(n)) return null;
  return String(n);
}

export function amountsEquivalent(expected, actual) {
  const a = normalizeAmountString(expected);
  const b = normalizeAmountString(actual);
  return Boolean(a && b && a === b);
}

export function billDateCandidates(meta, formats) {
  const list = formats?.length ? formats : ['dd/MM/yyyy'];
  return [...new Set(list.map(f => formatBillDate(meta.billDate, f)))];
}

export function sortReceiptFiles(files) {
  return [...files].sort((a, b) => {
    const ma = parseReceiptFilename(a);
    const mb = parseReceiptFilename(b);
    return ma.billNo.localeCompare(mb.billNo, undefined, { numeric: true, sensitivity: 'base' });
  });
}
