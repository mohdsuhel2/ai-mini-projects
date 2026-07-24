/**
 * Adds in-field shuffle buttons; fills from NOOBIUS_RANDOM_SAMPLES on click.
 */
(function (global) {
  const SHUFFLE_SVG = '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 18h1.4c1.3 0 2.5-.6 3.3-1.7l6.1-8.6c.7-1.1 2-1.7 3.3-1.7H22"/><path d="m18 2 4 4-4 4"/><path d="M2 6h1.9c1.5 0 2.9.9 3.6 2.2"/><path d="M22 18h-5.9c-1.3 0-2.6-.7-3.3-1.8l-3.6-5.2"/></svg>';

  function $(id) {
    return typeof id === 'string' ? document.getElementById(id) : id;
  }

  function pick(key) {
    return global.NOOBIUS_RANDOM_SAMPLES?.pick?.(key) ?? null;
  }

  function fireInput(el) {
    if (!el) return;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function setFieldValue(id, value) {
    const el = $(id);
    if (!el) return;
    el.value = value == null ? '' : String(value);
    fireInput(el);
  }

  function wrapWithRandomBtn(el, onClick) {
    if (!el || el.closest('.input-affix-wrap') || el.closest('.ec-order-id-row')) return;
    const parent = el.parentNode;
    if (!parent) return;

    const wrap = document.createElement('div');
    wrap.className = 'input-affix-wrap';
    if (el.tagName === 'TEXTAREA') wrap.classList.add('is-textarea');
    parent.insertBefore(wrap, el);
    wrap.appendChild(el);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'random-fill-btn';
    btn.setAttribute('aria-label', 'Fill with sample data');
    btn.title = 'Fill with sample data';
    btn.innerHTML = SHUFFLE_SVG;
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      onClick();
    });
    wrap.appendChild(btn);
  }

  function fillSimpleField(id, sampleKey) {
    const sample = pick(sampleKey);
    if (sample == null) return;
    setFieldValue(id, sample);
  }

  function fillStation(line1Id, line2Id) {
    const sample = pick('stationAddresses');
    if (!sample) return;
    setFieldValue(line1Id, sample.line1);
    setFieldValue(line2Id, sample.line2);
  }

  function fillPostpaidPlan(nameId, chargeId) {
    const sample = pick('postpaidPlans');
    if (!sample) return;
    setFieldValue(nameId, sample.name);
    setFieldValue(chargeId, sample.charges);
  }

  const SIMPLE_FIELDS = {
    ecSellerName: 'sellerNames',
    ecSellerAddress: 'sellerDispatchAddresses',
    ecSellerRegisteredAddress: 'sellerRegisteredAddresses',
    rrHouseNo: 'houseNumbers',
    rrBulkHouseNo: 'houseNumbers',
    rrPropertyAddress: 'propertyAddresses',
    rrBulkPropertyAddress: 'propertyAddresses',
    rrLandlordName: 'landlordNames',
    rrBulkLandlordName: 'landlordNames',
  };

  function init() {
    if (!global.NOOBIUS_RANDOM_SAMPLES) return;

    Object.entries(SIMPLE_FIELDS).forEach(([fieldId, sampleKey]) => {
      const el = $(fieldId);
      if (!el) return;
      wrapWithRandomBtn(el, () => fillSimpleField(fieldId, sampleKey));
    });

    const stationSingle = $('stationLine1');
    if (stationSingle) wrapWithRandomBtn(stationSingle, () => fillStation('stationLine1', 'stationLine2'));

    const stationBulk = $('bulkStationLine1');
    if (stationBulk) wrapWithRandomBtn(stationBulk, () => fillStation('bulkStationLine1', 'bulkStationLine2'));

    const planSingle = $('bbPlanName');
    if (planSingle) wrapWithRandomBtn(planSingle, () => fillPostpaidPlan('bbPlanName', 'bbPlanCharges'));

    const planChargesSingle = $('bbPlanCharges');
    if (planChargesSingle) wrapWithRandomBtn(planChargesSingle, () => fillPostpaidPlan('bbPlanName', 'bbPlanCharges'));

    const planBulk = $('bbBulkPlanName');
    if (planBulk) wrapWithRandomBtn(planBulk, () => fillPostpaidPlan('bbBulkPlanName', 'bbBulkPlanCharges'));

    const planChargesBulk = $('bbBulkPlanCharges');
    if (planChargesBulk) wrapWithRandomBtn(planChargesBulk, () => fillPostpaidPlan('bbBulkPlanName', 'bbBulkPlanCharges'));

    const receiptNo = $('receiptNo');
    if (receiptNo) {
      wrapWithRandomBtn(receiptNo, () => {
        if (typeof global.regenerateFuelReceiptNo === 'function') {
          global.regenerateFuelReceiptNo();
        }
      });
    }

    const bulkIdPrefix = $('bulkIdPrefix');
    if (bulkIdPrefix) {
      wrapWithRandomBtn(bulkIdPrefix, () => {
        if (typeof global.regenerateBulkReceiptPrefix === 'function') {
          global.regenerateBulkReceiptPrefix();
        }
      });
    }
  }

  global.NOOBIUS_RANDOM_FIELD_FILL = { init };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}(typeof window !== 'undefined' ? window : globalThis));
