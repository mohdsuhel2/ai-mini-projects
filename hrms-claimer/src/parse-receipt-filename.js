import fs from 'node:fs';
import path from 'node:path';

const MONTHS = {
  jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5,
  jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11,
};

/**
 * DocForge fuel receipt: {receiptNo}_{ddMonyyyy}.png
 * Example: 2026AA3210_14Jan2026.png
 */
export const DOCFORGE_FUEL_PATTERN = /^(.+)_(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})\.(png|pdf|jpe?g)$/i;

export function parseReceiptFilename(filePath) {
  const base = String(filePath).split(/[/\\]/).pop() || '';
  const match = base.match(DOCFORGE_FUEL_PATTERN);
  if (!match) {
    throw new Error(
      `Cannot parse ID/date from "${base}". Expected format: RECEIPTID_14Jan2026.png (DocForge fuel export).`,
    );
  }

  const [, billId, dayStr, monthStr, yearStr] = match;
  const day = Number(dayStr);
  const month = MONTHS[monthStr.toLowerCase()];
  const year = Number(yearStr);
  const date = new Date(year, month, day);

  if (Number.isNaN(date.getTime()) || date.getDate() !== day) {
    throw new Error(`Invalid date in filename: ${base}`);
  }

  const pad = n => String(n).padStart(2, '0');
  const billDateDisplay = `${pad(day)}/${pad(month + 1)}/${year}`;

  return {
    filename: base,
    billId: billId.trim(),
    billNo: billId.trim(),
    billDate: date,
    billDateDisplay,
    billDateHrms: billDateDisplay,
    billDetails: `${billId.trim()} - ${pad(day)} ${monthStr} ${year}`,
    billAmount: 2800,
  };
}

export function listReceiptFiles(dirPath) {
  const abs = path.resolve(dirPath);
  if (!fs.existsSync(abs) || !fs.statSync(abs).isDirectory()) {
    throw new Error(`Folder not found: ${abs}`);
  }
  return fs.readdirSync(abs, { withFileTypes: true })
    .filter(e => e.isFile() && DOCFORGE_FUEL_PATTERN.test(e.name))
    .map(e => path.join(abs, e.name))
    .sort();
}
