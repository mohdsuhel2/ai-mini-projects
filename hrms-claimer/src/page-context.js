/** Collect main page + all nested frames */
export function getAllFrames(page) {
  const frames = [page.mainFrame()];
  const seen = new Set(frames.map(f => f.url()));
  const queue = [...frames];
  while (queue.length) {
    const frame = queue.shift();
    for (const child of frame.childFrames()) {
      if (!seen.has(child.url())) {
        seen.add(child.url());
        frames.push(child);
        queue.push(child);
      }
    }
  }
  return frames;
}

export async function discoverInputs(page) {
  const frames = getAllFrames(page);
  const results = [];

  for (const frame of frames) {
    let items = [];
    try {
      items = await frame.evaluate(() => {
        const isField = el => {
          if (!el || el.disabled || el.readOnly === true) return false;
          const tag = el.tagName;
          if (tag === 'TEXTAREA') return true;
          if (tag !== 'INPUT') return false;
          const type = (el.type || 'text').toLowerCase();
          return !['hidden', 'file', 'button', 'submit', 'reset', 'image', 'checkbox', 'radio'].includes(type);
        };

        const rowLabel = el => {
          const row = el.closest('tr');
          if (!row) return '';
          const cells = [...row.querySelectorAll('td, th')];
          for (const cell of cells) {
            if (cell.contains(el)) continue;
            const t = cell.textContent.replace(/\s+/g, ' ').trim();
            if (t && t.length < 60) return t;
          }
          return '';
        };

        const labelFor = el => {
          if (el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) return lbl.textContent.replace(/\s+/g, ' ').trim();
          }
          const parentLabel = el.closest('label');
          if (parentLabel) {
            return parentLabel.textContent.replace(/\s+/g, ' ').trim();
          }
          return '';
        };

        return [...document.querySelectorAll('input, textarea, select')].filter(isField).map(el => ({
          id: el.id || '',
          name: el.name || '',
          type: el.type || el.tagName.toLowerCase(),
          placeholder: el.placeholder || '',
          ariaLabel: el.getAttribute('aria-label') || '',
          label: labelFor(el),
          rowLabel: rowLabel(el),
          value: el.value || '',
          visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
        }));
      });
    } catch {
      continue;
    }

    items.forEach(item => {
      results.push({ frameUrl: frame.url(), ...item });
    });
  }

  return results;
}

export async function clickAddRowIfPresent(page) {
  const frames = getAllFrames(page);
  const patterns = [/^\s*add\s*$/i, /add\s*new/i, /new\s*row/i, /add\s*bill/i, /add\s*detail/i, /\+\s*add/i];

  for (const frame of frames) {
    for (const pattern of patterns) {
      const btn = frame.getByRole('button', { name: pattern }).first();
      if (await btn.isVisible().catch(() => false)) {
        await btn.click();
        await page.waitForTimeout(1200);
        return true;
      }
      const link = frame.getByRole('link', { name: pattern }).first();
      if (await link.isVisible().catch(() => false)) {
        await link.click();
        await page.waitForTimeout(1200);
        return true;
      }
    }
  }
  return false;
}

export async function waitForBillForm(page, timeoutMs = 20_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const inputs = await discoverInputs(page);
    const billish = inputs.filter(i =>
      /bill/i.test(`${i.label} ${i.rowLabel} ${i.id} ${i.name} ${i.placeholder}`),
    );
    if (billish.length >= 2) return billish;
    await page.waitForTimeout(500);
  }
  return [];
}
