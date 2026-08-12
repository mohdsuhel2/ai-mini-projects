import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('https://noobius.in/fuel-receipt.html', { waitUntil: 'networkidle' });
const fills = { dateTime: '2026-07-23T14:30', vehNo: 'DL01AB1234', rate: '100.5', amount: '1005' };
for (const [id, v] of Object.entries(fills)) await page.locator(`#${id}`).fill(v);
await page.waitForTimeout(1500);

const r = await page.evaluate(async () => {
  const TEAR_HEIGHT = 14;
  const WRAPPER_PADDING = 14;
  const TEAR_CLIP_TOP = 'polygon(0% 100%, 50% 20%, 100% 100%)';
  const TEAR_CLIP_BOTTOM = TEAR_CLIP_TOP;

  function count(canvas) {
    const { data } = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
    let d = 0;
    for (let i = 0; i < data.length; i += 64) {
      const a = data[i + 3];
      if (a < 20) continue;
      const l = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
      if (l < 95) d += 1;
    }
    return d;
  }

  async function load(src) {
    const blob = await fetch(src).then((res) => res.blob());
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = reject;
      fr.readAsDataURL(blob);
    });
  }

  function createExportTear(position, bgDataUrl, paper, bgPosition) {
    const tear = document.createElement('div');
    const bgImage = bgDataUrl ? `url("${bgDataUrl}")` : 'none';
    tear.style.cssText = `position:absolute;left:0;right:0;height:${TEAR_HEIGHT}px;z-index:4;pointer-events:none;background-color:${paper};background-image:${bgImage};background-size:cover;background-position:${bgPosition};background-repeat:no-repeat;clip-path:${position === 'top' ? TEAR_CLIP_TOP : TEAR_CLIP_BOTTOM};`;
    tear.style[position === 'top' ? 'top' : 'bottom'] = `-${TEAR_HEIGHT}px`;
    return tear;
  }

  const logoDataUrl = await load('LogoBP-bnw.png');
  const bgDataUrl = await load('white-crumpled-paper-texture.png');
  const paper = '#f4f4f4';
  const wrapper = document.getElementById('receiptWrapper');
  const exportRoot = wrapper.cloneNode(true);
  exportRoot.querySelectorAll('.preview-watermark-layer').forEach((n) => n.remove());
  exportRoot.removeAttribute('id');
  exportRoot.id = 'bp-export-clone';
  exportRoot.style.cssText = 'position:fixed;left:-120vw;top:0;opacity:1;visibility:visible;z-index:-1;pointer-events:none;overflow:visible;filter:none;padding:14px 0;box-sizing:border-box;display:inline-block;';
  exportRoot.classList.remove('preview-doc-shell');
  const receipt = exportRoot.querySelector('.receipt');
  receipt.style.overflow = 'visible';
  receipt.style.filter = 'none';
  receipt.style.backgroundColor = paper;
  receipt.style.backgroundImage = `url("${bgDataUrl}")`;
  receipt.style.backgroundSize = 'cover';
  receipt.style.backgroundRepeat = 'no-repeat';
  receipt.appendChild(createExportTear('top', bgDataUrl, paper, 'center center'));
  receipt.appendChild(createExportTear('bottom', bgDataUrl, paper, 'center center'));
  exportRoot.querySelectorAll('img').forEach((img) => { img.src = logoDataUrl; });
  const exportStyle = document.createElement('style');
  exportStyle.textContent = '#bp-export-clone .receipt::before,#bp-export-clone .receipt::after{display:none!important;content:none!important;}';
  exportRoot.appendChild(exportStyle);
  document.body.appendChild(exportRoot);

  const clipped = [...document.querySelectorAll('body.site-generator .site-main, body.site-generator .preview-area, body.site-generator .config-panel, body.site-generator .generator-workspace, body.site-generator .app')]
    .map((n) => ({ n, overflow: n.style.overflow }));
  document.documentElement.style.overflow = 'visible';
  document.body.style.overflow = 'visible';
  clipped.forEach(({ n }) => { n.style.overflow = 'visible'; });
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

  const canvas = await html2canvas(receipt, {
    backgroundColor: paper,
    scale: 3,
    useCORS: true,
    allowTaint: false,
    foreignObjectRendering: false,
    logging: false,
    onclone: (doc, clone) => {
      clone.querySelectorAll('img').forEach((img) => {
        if (!img.src.startsWith('data:')) img.src = logoDataUrl;
      });
      const style = doc.createElement('style');
      style.textContent = '.receipt::before,.receipt::after{display:none!important;content:none!important;} .receipt-content{filter:none!important;} .receipt-wrapper{filter:none!important;overflow:visible!important;} .receipt{filter:none!important;overflow:visible!important;}';
      doc.head.appendChild(style);
    },
  });

  exportRoot.remove();
  clipped.forEach(({ n, overflow }) => { n.style.overflow = overflow; });
  const dark = count(canvas);
  return { dark, pass: dark > 48, w: canvas.width, h: canvas.height };
});

console.log('full clone', r);

const alerts = [];
page.on('dialog', async (d) => { alerts.push(d.message()); await d.accept(); });
await page.evaluate(() => {
  window.NOOBIUS_AD_GATE = { open: ({ onConfirm }) => { onConfirm(); return Promise.resolve(); } };
});
await page.evaluate(() => document.getElementById('downloadBtn').click());
await page.waitForTimeout(20000);
console.log('download alerts', alerts);

await browser.close();
