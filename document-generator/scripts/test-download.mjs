import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
let alertMsg = '';
page.on('dialog', async (d) => { alertMsg = d.message(); await d.accept(); });

await page.addInitScript(() => {
  window.NOOBIUS_AD_GATE = {
    SINGLE_DELAY_SEC: 0,
    open: ({ onConfirm }) => { onConfirm(); return Promise.resolve(); },
  };
});

await page.goto('https://noobius.in/fuel-receipt.html', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForSelector('#receiptWrapper');

const fills = {
  dateTime: '2026-07-23T14:30',
  vehNo: 'DL01AB1234',
  rate: '100.5',
  amount: '1005',
};
for (const [id, value] of Object.entries(fills)) {
  const el = page.locator(`#${id}`);
  if (await el.count()) await el.fill(value);
}
await page.waitForTimeout(1000);

const [download] = await Promise.all([
  page.waitForEvent('download', { timeout: 120000 }).catch(() => null),
  page.click('#downloadBtn'),
]);

console.log('ALERT:', alertMsg || '(none)');
if (download) {
  const path = await download.path();
  console.log('DOWNLOAD:', download.suggestedFilename(), path);
} else {
  console.log('NO DOWNLOAD');
}

await browser.close();
