import { launchHrmsBrowser } from './browser.js';
import { loadAppConfig } from './app-config.js';
import { performLogin } from './auth.js';

const config = loadAppConfig();

const { context, page } = await launchHrmsBrowser({ headless: false });
try {
  await performLogin(page, {
    loginUrl: config.loginUrl,
    username: config.username,
    password: config.password,
  });
  console.log('Login OK. Run: npm run upload');
  await page.waitForTimeout(3000);
} catch (err) {
  console.error(err.message || err);
  process.exitCode = 1;
} finally {
  await context.close();
}
