import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';
import { CLAIM_TYPES } from './config.js';
import { loadAppConfig, ensureConfigExists } from './app-config.js';
import { parseReceiptFilename, listReceiptFiles } from './parse-receipt-filename.js';
import { sortReceiptFiles } from './date-formats.js';
import { fillRimFuelForm, uploadRimAttachment } from './rim-form.js';
import { resolveRimPage } from './rim-page.js';
import { launchHrmsBrowser } from './browser.js';
import { ensureLoggedIn } from './auth.js';

function expandPath(p) {
  return path.resolve(String(p).replace(/^~(?=$|[/\\])/, process.env.HOME || ''));
}

function parseArgs(argv) {
  const args = { type: null, dir: null, amount: null };
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--type' || a === '-t') args.type = argv[++i];
    else if (a === '--dir' || a === '-d') args.dir = argv[++i];
    else if (a === '--amount' || a === '-a') args.amount = Number(argv[++i]);
    else if (a === '--help' || a === '-h') args.help = true;
  }
  return args;
}

async function prompt(rl, question, defaultValue = '') {
  const hint = defaultValue ? ` [${defaultValue}]` : '';
  const answer = (await rl.question(`${question}${hint}: `)).trim();
  return answer || defaultValue || null;
}

async function resolveClaimType(rl, preset, configDefault) {
  const initial = preset || configDefault;
  if (initial && CLAIM_TYPES[initial]) return { key: initial, ...CLAIM_TYPES[initial] };

  console.log('\nDocument types:');
  Object.entries(CLAIM_TYPES).forEach(([key, cfg]) => {
    console.log(`  ${key.padEnd(8)} — ${cfg.label}`);
  });
  const choice = (await prompt(rl, 'Type (fuel / driver)'))?.toLowerCase();
  if (!choice || !CLAIM_TYPES[choice]) throw new Error(`Unknown type "${choice}"`);
  return { key: choice, ...CLAIM_TYPES[choice] };
}

async function resolveDirectory(rl, preset, configDefault) {
  const dir = await prompt(rl, 'Receipts folder path', preset || configDefault || '');
  if (!dir) throw new Error('Folder path is required.');
  const abs = expandPath(dir);
  if (!fs.existsSync(abs)) throw new Error(`Folder not found: ${abs}`);
  return abs;
}

async function processOneClaim(context, rl, claim, filePath, opts) {
  const meta = parseReceiptFilename(filePath);
  const amount = opts.amount;

  console.log(`\n══════════════════════════════════════`);
  console.log(`  ${meta.filename}`);
  console.log(`  ID: ${meta.billNo}  |  Date: ${meta.billDateDisplay}  |  ₹${amount}`);
  console.log(`══════════════════════════════════════`);

  const rimPage = await resolveRimPage(context, rl, {
    isFirst: opts.isFirst,
    manualNavigation: opts.manualNavigation,
  });

  console.log('\n→ Fill Bill No & Details');
  await fillRimFuelForm(rimPage, meta, {
    phase: 'basic',
    amount,
    dateFormats: opts.dateFormats,
    delayMs: opts.delayMs,
  });

  console.log('\n→ Upload attachment (if field present)');
  const uploaded = await uploadRimAttachment(rimPage, filePath);
  if (uploaded) console.log(`  · Attached ${path.basename(filePath)}`);
  else console.log('  · No file field on this form (skip or attach manually)');
  await rimPage.waitForTimeout(opts.delayMs);

  console.log('\n→ Fill Bill Date & Amount');
  await fillRimFuelForm(rimPage, meta, {
    phase: 'amounts',
    amount,
    dateFormats: opts.dateFormats,
    delayMs: opts.delayMs,
  });

  console.log('\n→ Review the form and click Save yourself when ready');
}

async function main() {
  ensureConfigExists();
  const config = loadAppConfig();
  const args = parseArgs(process.argv);

  if (args.help) {
    console.log(`
Usage: npm run upload

Opens Fuel form on RimRequest.aspx (popup). You navigate menus manually;
script fills Bill No, Details, Date, Amount. You click Save yourself.

Config: hrms-config.json
  manualNavigation: true  (recommended)
`);
    return;
  }

  const rl = readline.createInterface({ input, output });
  const claim = await resolveClaimType(rl, args.type, config.claimType);
  const dir = await resolveDirectory(rl, args.dir, config.receiptsDirectory);

  let files = sortReceiptFiles(listReceiptFiles(dir));
  if (!files.length) throw new Error(`No receipt files in ${dir}`);

  const amount = args.amount ?? config.defaultAmount;
  const manualNavigation = config.manualNavigation !== false;

  console.log(`\nBatch: ${files.length} file(s), sorted by receipt ID`);
  files.forEach((f, i) => {
    const m = parseReceiptFilename(f);
    console.log(`  ${i + 1}. ${m.billNo} — ${path.basename(f)}`);
  });

  const { context, page } = await launchHrmsBrowser({ headless: false });

  try {
    console.log('\n1) Login');
    await ensureLoggedIn(page, {
      loginUrl: config.loginUrl,
      username: config.username,
      password: config.password,
    });

    if (manualNavigation) {
      console.log('\n2) You will open the Fuel form manually for each receipt');
      console.log('   (Payroll → Employee → Reimbursement → Claim Request → Fuel)');
    }

    for (let i = 0; i < files.length; i += 1) {
      await processOneClaim(context, rl, claim, files[i], {
        amount,
        dateFormats: config.dateFormats,
        delayMs: config.delayMs || 600,
        manualNavigation,
        isFirst: i === 0,
      });
    }

    console.log(`\n✓ Done — processed ${files.length} receipt(s).`);
    await page.waitForTimeout(5000);
  } finally {
    rl.close();
    await context.close();
  }
}

main().catch(err => {
  console.error('\nError:', err.message || err);
  process.exitCode = 1;
});
