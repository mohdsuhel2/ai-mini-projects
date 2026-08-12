import { chromium } from 'playwright';

async function run(url) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('dialog', async (d) => { console.log('DIALOG:', d.message()); await d.dismiss(); });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));

  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForSelector('#receiptWrapper', { timeout: 20000 });

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
  await page.waitForTimeout(1500);

  const pre = await page.evaluate(() => ({
    text: document.getElementById('receiptWrapper')?.innerText?.slice(0, 120),
    htmlLen: document.getElementById('receiptWrapper')?.innerHTML?.length || 0,
  }));
  console.log('PREVIEW', pre);

  const exportTest = await page.evaluate(async () => {
    function canvasHasVisiblePixels(canvas) {
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
      let darkPixels = 0;
      for (let i = 0; i < data.length; i += 64) {
        const alpha = data[i + 3];
        if (alpha < 20) continue;
        const luminance = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
        if (luminance < 95) darkPixels += 1;
      }
      return darkPixels;
    }

    const wrapper = document.getElementById('receiptWrapper');
    const exportRoot = wrapper.cloneNode(true);
    exportRoot.querySelectorAll('.preview-watermark-layer').forEach((n) => n.remove());
    exportRoot.classList.remove('preview-doc-shell');
    const styles = [
      'position:fixed;left:-120vw;top:0;opacity:1;visibility:visible;z-index:-1;pointer-events:none;overflow:visible;filter:none;',
      'position:fixed;left:0;top:0;opacity:1;visibility:visible;z-index:2147483647;pointer-events:none;overflow:visible;filter:none;',
      'position:fixed;left:0;top:0;opacity:0.01;visibility:visible;z-index:2147483647;pointer-events:none;overflow:visible;filter:none;',
    ];
    const receipt = exportRoot.querySelector('.receipt');
    const out = [];
    for (const style of styles) {
      exportRoot.style.cssText = `${style}padding:22px 0 28px;display:inline-block;`;
      if (!exportRoot.parentElement) document.body.appendChild(exportRoot);
      document.body.style.overflow = 'visible';
      document.documentElement.style.overflow = 'visible';
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const canvas = await html2canvas(receipt, {
        backgroundColor: '#ececec',
        scale: 3,
        useCORS: true,
        allowTaint: false,
        logging: false,
      });
      out.push({ style: style.slice(0, 40), dark: canvasHasVisiblePixels(canvas), w: canvas.width, h: canvas.height });
    }
    exportRoot.remove();
    return out;
  });

  console.log('EXPORT TEST', JSON.stringify(exportTest, null, 2));
  await browser.close();
}

const url = process.argv[2] || 'https://noobius.in/fuel-receipt.html';
console.log('URL', url);
await run(url);
