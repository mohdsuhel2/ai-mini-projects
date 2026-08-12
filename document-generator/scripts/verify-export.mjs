import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

function bufferHasReceiptContent(buffer) {
  if (!buffer || buffer.length < 5000) return false;
  let darkish = 0;
  for (let i = 100; i < Math.min(buffer.length, 12000); i += 23) {
    if (buffer[i] < 95) darkish += 1;
  }
  return darkish > 80;
}

async function testFuelExport(page) {
  await page.goto('http://127.0.0.1:8765/fuel-receipt.html', { waitUntil: 'networkidle' });
  await page.waitForSelector('#receiptWrapper', { timeout: 15000 });

  const fills = {
    dateTime: '2026-07-23T14:30',
    vehicleNo: 'DL01AB1234',
    customerName: 'Test User',
    mobile: '9876543210',
    fuelRate: '100.5',
    litres: '10',
    amount: '1005',
    stationName: 'Test Fuel Station',
    stationAddress: 'Green Park, New Delhi',
    receiptNo: 'TEST12345',
  };
  for (const [id, value] of Object.entries(fills)) {
    const el = page.locator(`#${id}`);
    if (await el.count()) await el.fill(value);
  }
  await page.click('#updatePreviewBtn').catch(() => {});
  await page.waitForTimeout(1200);

  const result = await page.evaluate(async () => {
    const wrapper = document.getElementById('receiptWrapper');
    if (!wrapper || typeof html2canvas !== 'function') {
      return { ok: false, error: 'missing wrapper or html2canvas' };
    }

    const mountStyle = 'position:fixed;left:-120vw;top:0;opacity:1;visibility:visible;z-index:-1;pointer-events:none;overflow:visible;filter:none;';
    const exportRoot = wrapper.cloneNode(true);
    exportRoot.querySelectorAll('.preview-watermark-layer').forEach((node) => node.remove());
    exportRoot.classList.remove('preview-doc-shell');
    exportRoot.style.cssText = `${mountStyle}padding:22px 0 28px;box-sizing:border-box;display:inline-block;`;
    const receipt = exportRoot.querySelector('.receipt');
    if (!receipt) return { ok: false, error: 'missing receipt node' };
    receipt.style.filter = 'none';
    receipt.style.overflow = 'visible';
    document.body.appendChild(exportRoot);

    const clippedNodes = [...document.querySelectorAll('body.site-generator .site-main, body.site-generator .preview-area, body.site-generator .config-panel, body.site-generator .generator-workspace, body.site-generator .app')]
      .map((node) => ({ node, overflow: node.style.overflow }));
    const htmlOverflow = document.documentElement.style.overflow;
    const bodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = 'visible';
    document.body.style.overflow = 'visible';
    clippedNodes.forEach(({ node }) => { node.style.overflow = 'visible'; });

    try {
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const canvas = await html2canvas(receipt, {
        backgroundColor: '#ececec',
        scale: 2,
        useCORS: true,
        allowTaint: false,
        logging: false,
      });
      const ctx = canvas.getContext('2d');
      const { data } = ctx.getImageData(0, 0, Math.min(220, canvas.width), Math.min(220, canvas.height));
      let darkPixels = 0;
      for (let i = 0; i < data.length; i += 16) {
        const alpha = data[i + 3];
        if (alpha < 20) continue;
        const luminance = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
        if (luminance < 95) darkPixels += 1;
      }
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      const bytes = blob ? new Uint8Array(await blob.arrayBuffer()) : new Uint8Array();
      return {
        ok: darkPixels > 48 && bytes.length > 5000,
        darkPixels,
        size: bytes.length,
        width: canvas.width,
        height: canvas.height,
        bytes: Array.from(bytes),
      };
    } finally {
      exportRoot.remove();
      document.documentElement.style.overflow = htmlOverflow;
      document.body.style.overflow = bodyOverflow;
      clippedNodes.forEach(({ node, overflow }) => { node.style.overflow = overflow; });
    }
  });

  if (result.bytes) {
    writeFileSync(new URL('./fuel-export-test.png', import.meta.url), Buffer.from(result.bytes));
  }
  return { generator: 'fuel', ...result };
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const result = await testFuelExport(page);
await browser.close();
console.log(JSON.stringify({ ...result, bytes: undefined }, null, 2));
process.exit(result.ok ? 0 : 1);
