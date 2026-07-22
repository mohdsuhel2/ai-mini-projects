import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { PATHS } from './config.js';
import { getAllFrames } from './page-context.js';
import { ensureLoggedIn, clickSave } from './auth.js';

export { ensureLoggedIn, clickSave };

export async function launchHrmsBrowser({ headless = false } = {}) {
  fs.mkdirSync(PATHS.profileDir, { recursive: true });

  const context = await chromium.launchPersistentContext(PATHS.profileDir, {
    headless,
    viewport: { width: 1366, height: 900 },
    acceptDownloads: true,
    ignoreHTTPSErrors: true,
  });

  const page = context.pages()[0] ?? (await context.newPage());
  return { context, page };
}

export async function navigateToClaimRequest(page) {
  const { NAV } = await import('./config.js');

  // HRMS is ASP.NET — menus may be links, tree nodes, or top nav. Try common patterns.
  const steps = [
    { name: 'Payroll', pattern: NAV.payroll },
    { name: 'Employee', pattern: NAV.employee },
    { name: 'Reimbursement', pattern: NAV.reimbursement },
    { name: 'Claim Request', pattern: NAV.claimRequest },
  ];

  for (const step of steps) {
    const link = page.getByRole('link', { name: step.pattern }).first();
    const button = page.getByRole('button', { name: step.pattern }).first();
    const text = page.getByText(step.pattern).first();

    if (await link.isVisible().catch(() => false)) {
      await link.click();
    } else if (await button.isVisible().catch(() => false)) {
      await button.click();
    } else {
      await text.click({ timeout: 15_000 });
    }

    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(800);
    console.log(`  · Opened ${step.name}`);
  }
}

export async function pickClaimType(page, claimType) {
  const frames = getAllFrames(page);
  for (const frame of frames) {
    const target = frame.getByRole('link', { name: claimType.menu }).first();
    if (await target.isVisible().catch(() => false)) {
      await target.click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(1500);
      return;
    }
    const text = frame.getByText(claimType.menu).first();
    if (await text.isVisible().catch(() => false)) {
      await text.click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(1500);
      return;
    }
  }
  throw new Error(`Could not find claim type menu: ${claimType.label}`);
}

export async function uploadAttachment(page, filePath) {
  const abs = path.resolve(filePath);
  if (!fs.existsSync(abs)) throw new Error(`File not found: ${abs}`);

  const { getAllFrames } = await import('./page-context.js');
  const frames = getAllFrames(page);

  for (const frame of frames) {
    const input = frame.locator('input[type="file"]').first();
    if (await input.count()) {
      await input.setInputFiles(abs);
      console.log(`  · Attached ${path.basename(abs)}`);
      return;
    }
  }

  throw new Error('No file upload input found on page (check iframe / Add row).');
}
