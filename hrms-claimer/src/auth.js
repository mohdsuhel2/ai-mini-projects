import { getAllFrames } from './page-context.js';

export async function clickSave(page) {
  const patterns = [/^\s*save\s*$/i, /save\s*&\s*continue/i, /^submit$/i, /add\s*bill/i];
  const frames = getAllFrames(page);

  for (const frame of frames) {
    for (const pattern of patterns) {
      const btn = frame.getByRole('button', { name: pattern }).first();
      if (await btn.isVisible().catch(() => false)) {
        await btn.click();
        await page.waitForLoadState('networkidle').catch(() => {});
        await page.waitForTimeout(1200);
        return true;
      }
      const input = frame.locator('input[type="submit"][value*="Save" i], input[type="button"][value*="Save" i]').first();
      if (await input.isVisible().catch(() => false)) {
        await input.click();
        await page.waitForLoadState('networkidle').catch(() => {});
        await page.waitForTimeout(1200);
        return true;
      }
    }
  }
  return false;
}

export async function performLogin(page, { loginUrl, username, password }) {
  await page.goto(loginUrl, { waitUntil: 'domcontentloaded' });

  const passwordField = page.locator('input[type="password"]').first();
  const needsLogin = await passwordField.isVisible().catch(() => false);
  if (!needsLogin) {
    console.log('✓ Already logged in');
    return;
  }

  if (!username || !password) {
    throw new Error('Not logged in. Set username & password in hrms-config.json');
  }

  const userField = page.locator(
    'input[id*="User" i], input[name*="User" i], input[id*="Login" i], input[type="text"]',
  ).first();

  await userField.waitFor({ state: 'visible', timeout: 15_000 });
  await userField.fill(username);
  await passwordField.fill(password);

  const loginBtn = page.getByRole('button', { name: /login|sign\s*in/i }).first();
  if (await loginBtn.isVisible().catch(() => false)) {
    await loginBtn.click();
  } else {
    await page.locator('input[type="submit"], input[id*="Login" i], input[value*="Login" i]').first().click();
  }

  await page.waitForFunction(
    () => !/login\.aspx/i.test(window.location.href),
    null,
    { timeout: 60_000 },
  );
  console.log('✓ Logged in with config credentials');
}

export async function ensureLoggedIn(page, credentials) {
  if (credentials?.username && credentials?.password) {
    await performLogin(page, credentials);
    return;
  }
  await page.goto(credentials.loginUrl, { waitUntil: 'domcontentloaded' });
  const onLogin = await page.locator('input[type="password"]').first().isVisible().catch(() => false);
  if (!onLogin) return;
  throw new Error('Login required — add username & password to hrms-config.json');
}
