/** GT HRMS fuel/driver claim form lives on RimRequest.aspx (often a popup window). */

export function getRimRequestPage(context) {
  const pages = context.pages();
  return pages.find(p => !p.isClosed() && /RimRequest\.aspx/i.test(p.url())) || null;
}

export async function waitForRimRequestPage(context, timeoutMs = 300_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const rim = getRimRequestPage(context);
    if (rim) return rim;
    await new Promise(r => setTimeout(r, 400));
  }
  return null;
}

export async function promptOpenFuelForm(rl, { isFirst = true } = {}) {
  console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  if (isFirst) {
    console.log('  In the browser, navigate manually:');
    console.log('  Payroll → Employee → Reimbursement → Claim Request → Fuel');
  } else {
    console.log('  Open the Fuel claim form again for the next receipt');
  }
  console.log('  (The Fuel bill popup / RimRequest page must be visible)');
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
  await rl.question('Press Enter when the Fuel form is open… ');
}

export async function resolveRimPage(context, rl, { isFirst = true, manualNavigation = true }) {
  if (manualNavigation) {
    const alreadyOpen = isFirst && getRimRequestPage(context);
    if (!alreadyOpen) {
      await promptOpenFuelForm(rl, { isFirst });
    }
    const rim = await waitForRimRequestPage(context, 30_000);
    if (!rim) {
      throw new Error('Fuel form not found. Look for a page/popup with RimRequest.aspx in the URL.');
    }
    await rim.bringToFront();
    await rim.waitForLoadState('domcontentloaded').catch(() => {});
    console.log(`  · Using Fuel form: ${rim.url().split('/').pop()?.slice(0, 60)}…`);
    return rim;
  }

  const rim = await waitForRimRequestPage(context, 15_000);
  if (!rim) throw new Error('RimRequest.aspx not open. Set manualNavigation: true in config.');
  return rim;
}
