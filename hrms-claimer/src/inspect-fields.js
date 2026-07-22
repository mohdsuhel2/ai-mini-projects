import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';
import { CLAIM_TYPES } from './config.js';
import { loadAppConfig } from './app-config.js';
import { ensureLoggedIn } from './auth.js';
import {
  launchHrmsBrowser,
  navigateToClaimRequest,
  pickClaimType,
} from './browser.js';
import { discoverInputs, clickAddRowIfPresent, waitForBillForm } from './page-context.js';

const OUT = path.resolve('hrms-fields-dump.json');

async function main() {
  const claimKey = process.argv[2] || 'fuel';
  const claim = CLAIM_TYPES[claimKey];
  if (!claim) throw new Error(`Unknown type "${claimKey}". Use: fuel | driver`);

  const rl = readline.createInterface({ input, output });
  console.log(`\nOpen the **${claim.label}** claim form in the browser when prompted.\n`);
  await rl.question('Press Enter when ready to start… ');
  rl.close();

  const config = loadAppConfig();
  const { context, page } = await launchHrmsBrowser({ headless: false });
  try {
    await ensureLoggedIn(page, {
      loginUrl: config.loginUrl,
      username: config.username,
      password: config.password,
    });
    await navigateToClaimRequest(page);
    await pickClaimType(page, claim);
    await clickAddRowIfPresent(page);
    await waitForBillForm(page, 30_000);

    console.log('\nWaiting 5s — click Add / open bill row if fields are not visible yet…');
    await page.waitForTimeout(5000);

    const inputs = await discoverInputs(page);
    fs.writeFileSync(OUT, JSON.stringify(inputs, null, 2));

    console.log(`\nSaved ${inputs.length} fields → ${OUT}\n`);
    const billFields = inputs.filter(i =>
      /bill|amount|date|detail|receipt|description/i.test(
        `${i.label} ${i.rowLabel} ${i.id} ${i.name} ${i.placeholder}`,
      ),
    );
    console.log('Likely bill fields:');
    for (const f of billFields) {
      console.log(`  id=${f.id || '-'}  name=${f.name || '-'}  label="${f.label || f.rowLabel}"`);
    }

    console.log('\nCreate hrms-field-map.json like:');
    console.log(JSON.stringify({
      billNo: billFields[0]?.id ? `#${billFields[0].id}` : 'input[id*="BillNo"]',
      billDetails: 'input[id*="BillDetails"]',
      billDate: 'input[id*="BillDate"]',
      billAmount: 'input[id*="Amount"]',
    }, null, 2));

    await page.waitForTimeout(120_000);
  } finally {
    await context.close();
  }
}

main().catch(err => {
  console.error(err.message || err);
  process.exitCode = 1;
});
