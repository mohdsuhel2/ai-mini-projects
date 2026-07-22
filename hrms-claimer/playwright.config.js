/** @type {import('@playwright/test').PlaywrightTestConfig} */
export default {
  testDir: './tests',
  timeout: 120_000,
  use: {
    baseURL: 'https://gthrms.wcgt.in',
    headless: false,
    viewport: { width: 1366, height: 900 },
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    trace: 'on-first-retry',
  },
};
