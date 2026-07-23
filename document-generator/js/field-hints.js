(function () {
  'use strict';

  const COACHMARK_KEY_PREFIX = 'noobius_bulk_coachmark_v1_';
  const BULK_GENERATORS = new Set(['fuel', 'postpaid', 'rent', 'driver']);

  const FIELD_HINTS = {
    fuelCompany: 'Brand shown on the receipt header and styling (BP, IndianOil, or HP).',
    fuelReceiptTemplate: 'Visual layout and paper style for the single receipt preview.',
    stationLine1: 'First line of the petrol pump address printed on the receipt.',
    stationLine2: 'City or locality line shown below the station name.',
    vehNo: 'Vehicle registration number printed on the fuel slip.',
    customerName: 'Customer or driver name shown on the receipt.',
    vehType: 'Fuel type label (Petrol, Diesel, or CNG) on the receipt.',
    amount: 'Total rupee amount for this fill. Litres are calculated from rate.',
    fuelCapacity: 'Litres dispensed. Total amount is calculated from rate.',
    rate: 'Price per litre used to convert between amount and volume.',
    dateTime: 'Date and time printed on the receipt.',
    receiptNo: 'Unique receipt number. Leave blank for an auto-generated value.',
    bulkDateFrom: 'Earliest date/time for receipts in this batch.',
    bulkDateTo: 'Latest date/time for receipts in this batch.',
    bulkMinTotalAmount: 'Target combined total across all receipts. The app estimates how many receipts are needed (max 100).',
    bulkIdPrefix: 'Prefix for receipt numbers, e.g. year + series code.',
    bulkIdStart: 'Starting numeric suffix before gaps are applied.',
    bulkIdMinGap: 'Smallest random jump between consecutive receipt suffixes.',
    bulkIdMaxGap: 'Largest random jump between consecutive receipt suffixes.',
    bulkFuelCompany: 'Fuel brand applied to every receipt in the batch.',
    bulkFuelReceiptTemplate: 'Template style used for all bulk fuel receipts.',
    bulkStationLine1: 'Station address line 1 shared across the batch.',
    bulkStationLine2: 'Station address line 2 shared across the batch.',
    bulkVehNo: 'Vehicle number printed on each receipt.',
    bulkCustomerName: 'Customer name printed on each receipt.',
    bulkVehType: 'Fuel type label on every receipt in the batch.',
    bulkAmount: 'Same fill amount (₹) on every receipt when using fixed mode.',
    bulkCapacity: 'Same litres on every receipt when using fixed mode.',
    bulkAmountMin: 'Lowest random amount per receipt.',
    bulkAmountMax: 'Highest random amount per receipt.',
    bulkCapacityMin: 'Lowest random litres per receipt.',
    bulkCapacityMax: 'Highest random litres per receipt.',
    bulkRate: 'Rate per litre used to calculate amount or volume in bulk mode.',
    ecOrderDate: 'Order placed date. Invoice date and number are derived from this.',
    ecOrderId: 'Marketplace order ID. Click New to regenerate.',
    ecInvoiceDate: 'Auto-calculated invoice date from the order date.',
    ecInvoiceNo: 'Auto-generated invoice number. Click New to regenerate.',
    ecSellerName: 'Legal or trading name of the seller on the invoice.',
    ecSellerAddress: 'Dispatch / warehouse address printed on the invoice.',
    ecSellerRegisteredAddress: 'Registered office address of the seller.',
    ecCustomerName: 'Buyer name on the invoice.',
    ecBillingAddress: 'Billing address block on the invoice.',
    ecShippingAddress: 'Delivery address block on the invoice.',
    ecOrderedThrough: 'Marketplace or channel name (e.g. Flipkart, Amazon).',
    ecShippingCharges: 'Shipping and handling fee added to the invoice total.',
    bbCustomerName: 'Subscriber name on the postpaid bill.',
    bbAddress: 'Billing address on the statement.',
    bbEmail: 'Email address printed on the bill.',
    bbPhone: 'Registered mobile number on the bill.',
    bbPlanName: 'Active plan or pack name shown on the statement.',
    bbPlanCharges: 'Monthly plan charges before tax.',
    bbGstRate: 'GST percentage applied to plan charges.',
    bbLateFee: 'Late payment fee if applicable on this bill.',
    bbStatementDate: 'Statement issue date. Period, due date, and history follow this.',
    bbStatementPeriod: 'Billing cycle period (auto-calculated).',
    bbDueDate: 'Payment due date (auto-calculated).',
    bbBillRefId: 'Unique bill reference ID. Click New to regenerate.',
    bbLastBillAmount: 'Previous bill total for the payment summary section.',
    bbPaymentMade: 'Amount paid against the previous bill.',
    bbCredits: 'Account credits or adjustments applied.',
    bbBulkDateFrom: 'Date of the first monthly bill in the batch.',
    bbBulkCount: 'How many consecutive monthly bills to generate (max 24).',
    bbBulkCustomerName: 'Subscriber name on every bill in the batch.',
    bbBulkAddress: 'Address on every bill in the batch.',
    bbBulkEmail: 'Email on every bill in the batch.',
    bbBulkPhone: 'Phone number on every bill in the batch.',
    bbBulkPlanName: 'Plan name shared across all bills.',
    bbBulkPlanCharges: 'Plan charges on each monthly bill.',
    bbBulkGstRate: 'GST rate applied on each bill.',
    bbBulkLateFee: 'Late fee amount on each bill.',
    rrReceiptDate: 'Date printed on the rent receipt.',
    rrRentAmount: 'Rent amount in words and figures for this receipt.',
    rrMonthlyRent: 'Monthly rent rate shown on the receipt.',
    rrPeriodFromYear: 'Start year of the rent period (April).',
    rrPeriodToYear: 'End year of the rent period (March).',
    rrTenantSalutation: 'Title before the tenant name (Mr., Ms., etc.).',
    rrTenantName: 'Tenant name receiving the receipt.',
    rrHouseNo: 'Flat or house number of the rented property.',
    rrPropertyAddress: 'Full address of the rented property.',
    rrLandlordName: 'Owner / landlord name printed on the receipt.',
    rrLandlordPan: 'Landlord PAN printed on the receipt where required.',
    rrLandlordSignatureMode: 'Type a script signature or upload an image.',
    rrLandlordSignatureText: 'Handwriting-style signature text on the receipt.',
    rrLandlordSignature: 'Upload a PNG/JPG signature image.',
    rrRevenueStamp: 'Optional revenue stamp image on the receipt.',
    rrBulkDateFrom: 'Date of the first monthly rent receipt.',
    rrBulkCount: 'Number of monthly receipts to generate (max 24).',
    rrBulkRentAmount: 'Rent amount on each receipt in the batch.',
    rrBulkMonthlyRent: 'Monthly rent rate on each receipt.',
    rrBulkTenantSalutation: 'Tenant title on every receipt.',
    rrBulkTenantName: 'Tenant name on every receipt.',
    rrBulkHouseNo: 'House number on every receipt.',
    rrBulkPropertyAddress: 'Property address on every receipt.',
    rrBulkLandlordName: 'Landlord name on every receipt.',
    rrBulkLandlordPan: 'Landlord PAN on every receipt.',
    rrBulkLandlordSignatureMode: 'Signature style for all receipts in the batch.',
    rrBulkLandlordSignatureText: 'Typed signature text for all receipts.',
    rrBulkLandlordSignature: 'Signature image used on all receipts.',
    rrBulkRevenueStamp: 'Revenue stamp image used on all receipts.',
    dsReceivedFrom: 'Employer or vehicle owner who paid the salary.',
    dsSalaryAmount: 'Monthly salary amount in INR.',
    dsVehicleNo: 'Vehicle registration linked to this driver slip.',
    dsSalaryMonth: 'Month and year for this salary (auto from receipt date).',
    dsDriverName: 'Driver name on the salary receipt.',
    dsLicenseNo: 'Driving licence number on the slip.',
    dsSlipDate: 'Date printed on the driver salary receipt.',
    dsSignatureMode: 'Type a script signature or upload an image.',
    dsSignatureText: 'Handwriting-style driver signature on the slip.',
    dsSignature: 'Upload a driver signature image.',
    dsRevenueStamp: 'Revenue stamp image on the slip.',
    dsBulkDateFrom: 'Date of the first monthly driver slip.',
    dsBulkCount: 'Number of monthly slips to generate (max 24).',
    dsBulkSalaryAmount: 'Salary amount on each slip in the batch.',
    dsBulkReceivedFrom: 'Employer name on every slip.',
    dsBulkDriverName: 'Driver name on every slip.',
    dsBulkLicenseNo: 'Licence number on every slip.',
    dsBulkVehicleNo: 'Vehicle number on every slip.',
    dsBulkSignatureMode: 'Signature style for all slips in the batch.',
    dsBulkSignatureText: 'Typed signature on every slip.',
    dsBulkSignature: 'Signature image used on all slips.',
    dsBulkRevenueStamp: 'Revenue stamp used on all slips.',
  };

  const SECTION_HINTS = {
    Appearance: 'Choose brand, template, and visual style for the receipt.',
    'Station & customer': 'Petrol pump location and vehicle / customer details.',
    Transaction: 'Enter fill by amount or litres, then set the rate per litre.',
    'Batch & dates': 'Date range and target total for the fuel receipt batch.',
    'Receipt details': 'Date, rent amounts, and financial year for this receipt.',
    'Tenant & property': 'Who paid rent and which property it is for.',
    'House owner': 'Landlord details printed on the rent receipt.',
    'Sign & stamp': 'Signature and optional revenue stamp on the document.',
    'Order & invoice': 'Order date drives invoice date and invoice number.',
    'Seller details': 'Seller name and addresses on the ecommerce invoice.',
    'Customer & delivery': 'Buyer and shipping details on the invoice.',
    'Line items': 'Products or services with GST on the invoice.',
    Customer: 'Subscriber identity and contact on the postpaid bill.',
    'Plan & charges': 'Plan name, charges, GST, and late fee.',
    'Billing dates': 'Statement date, period, due date, and reference ID.',
    'Payment summary': 'Previous balance, payments, and credits.',
    'Batch settings': 'How many documents to generate and from which start date.',
    'Customer & plan': 'Shared customer and plan details for every bill.',
    'Salary receipt': 'Salary amount, vehicle, and month on the driver slip.',
    'Driver details': 'Driver name, licence, and receipt date.',
    'Shared details': 'Details reused on every slip in the batch.',
  };

  const TAB_HINTS = {
    txnModeAmountTab: 'Enter total rupee amount; litres are calculated from rate.',
    txnModeCapacityTab: 'Enter litres dispensed; amount is calculated from rate.',
    bulkValueModeFixedTab: 'Same amount or litres on every receipt in the batch.',
    bulkValueModeRandomTab: 'Random amount or litres within your min–max range per receipt.',
    bulkTxnModeAmountTab: 'Bulk receipts use a rupee amount (fixed or random range).',
    bulkTxnModeCapacityTab: 'Bulk receipts use litres (fixed or random range).',
    genMobileEditTab: 'Edit form fields on small screens.',
    genMobilePreviewTab: 'Switch to live receipt preview on mobile.',
  };

  const EC_SECTION_HINTS = {
    ecSecOrder: 'Order ID, dates, and invoice numbers.',
    ecSecSeller: 'Seller name and dispatch / registered addresses.',
    ecSecCustomer: 'Customer name, billing, and shipping addresses.',
    ecSecItems: 'Add products, quantities, prices, and GST.',
  };

  const BUTTON_HINTS = {
    downloadBtn: 'Download the current fuel receipt as a PNG image.',
    openBulkPreviewBtnSticky: 'Open a grid preview of all bulk fuel receipts before downloading.',
    ecDownloadPdfBtn: 'Download the ecommerce invoice as a PDF.',
    ecAddItemBtn: 'Add a new product line to the invoice.',
    ecRegenOrderIdBtn: 'Generate a new random order ID.',
    ecRegenInvoiceNoBtn: 'Generate a new invoice number from the order date.',
    bbDownloadPdfBtn: 'Download the postpaid bill as a PDF.',
    bbOpenBulkPreviewBtn: 'Preview all monthly bills before downloading as ZIP.',
    bbRegenBillRefBtn: 'Generate a new bill reference ID.',
    rrDownloadPdfBtn: 'Download the rent receipt as a PDF.',
    rrOpenBulkPreviewBtn: 'Preview all monthly rent receipts before downloading.',
    dsDownloadPdfBtn: 'Download the driver salary slip as a PDF.',
    dsOpenBulkPreviewBtn: 'Preview all monthly driver slips before downloading.',
  };

  const BULK_COACHMARK_COPY = {
    fuel: {
      title: 'Need many fuel receipts?',
      body: 'Open Fuel Receipt in the top menu and choose Bulk Fuel Receipt Generation to create dozens of receipts with different dates, amounts, and IDs in one go.',
      bulkLabel: 'Bulk Fuel Receipt Generation',
    },
    postpaid: {
      title: 'Generate monthly bills in bulk',
      body: 'Open Postpaid Bill in the top menu and choose Bulk Postpaid Bill Generation to create multiple monthly statements at once.',
      bulkLabel: 'Bulk Postpaid Bill Generation',
    },
    rent: {
      title: 'Generate rent receipts in bulk',
      body: 'Open Rent Receipt in the top menu and choose Bulk Rent Receipt Generation to create monthly rent receipts for a full period.',
      bulkLabel: 'Bulk Rent Receipt Generation',
    },
    driver: {
      title: 'Generate driver slips in bulk',
      body: 'Open Driver Slip in the top menu and choose Bulk Driver Slip Generation to create monthly salary slips in one batch.',
      bulkLabel: 'Bulk Driver Slip Generation',
    },
  };

  let activePopover = null;
  let activeAnchor = null;
  let coachmarkEl = null;
  let coachmarkTimer = null;
  let coachmarkResizeHandler = null;

  function getSectionLabelText(section) {
    const inner = section.querySelector('.section-label-inner span:last-child');
    if (inner) return inner.textContent.trim();
    return section.textContent.replace(/\s+/g, ' ').trim();
  }

  function createHintTrigger(text) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ux-hint-trigger';
    btn.setAttribute('aria-label', 'What is this?');
    btn.innerHTML = '<span aria-hidden="true">i</span>';
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      togglePopover(btn, text);
    });
    btn.addEventListener('mouseenter', () => {
      if (window.matchMedia('(hover: hover)').matches) showPopover(btn, text);
    });
    btn.addEventListener('mouseleave', () => {
      if (window.matchMedia('(hover: hover)').matches) hidePopover();
    });
    return btn;
  }

  function ensurePopover() {
    if (activePopover) return activePopover;
    const el = document.createElement('div');
    el.className = 'ux-hint-popover';
    el.setAttribute('role', 'tooltip');
    el.hidden = true;
    document.body.appendChild(el);
    activePopover = el;
    document.addEventListener('click', onDocumentClick);
    document.addEventListener('keydown', onDocumentKeydown);
    window.addEventListener('scroll', onPopoverReposition, true);
    window.addEventListener('resize', onPopoverReposition);
    return el;
  }

  function onDocumentClick(e) {
    if (!activePopover || activePopover.hidden) return;
    if (activePopover.contains(e.target) || activeAnchor?.contains(e.target)) return;
    hidePopover();
  }

  function onDocumentKeydown(e) {
    if (e.key === 'Escape') hidePopover();
  }

  function onPopoverReposition() {
    if (!activePopover || activePopover.hidden || !activeAnchor) return;
    positionPopover(activeAnchor);
  }

  function positionPopover(anchor) {
    const pop = ensurePopover();
    const rect = anchor.getBoundingClientRect();
    const margin = 10;
    pop.style.visibility = 'hidden';
    pop.hidden = false;
    const popRect = pop.getBoundingClientRect();
    let top = rect.bottom + margin;
    let left = rect.left + rect.width / 2 - popRect.width / 2;
    if (top + popRect.height > window.innerHeight - margin) {
      top = rect.top - popRect.height - margin;
      pop.dataset.placement = 'top';
    } else {
      pop.dataset.placement = 'bottom';
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - popRect.width - margin));
    pop.style.top = `${Math.round(top)}px`;
    pop.style.left = `${Math.round(left)}px`;
    pop.style.visibility = '';
  }

  function showPopover(anchor, text) {
    const pop = ensurePopover();
    activeAnchor = anchor;
    pop.textContent = text;
    positionPopover(anchor);
    pop.hidden = false;
    anchor.setAttribute('aria-describedby', 'uxHintPopover');
    pop.id = 'uxHintPopover';
  }

  function hidePopover() {
    if (!activePopover) return;
    activePopover.hidden = true;
    activeAnchor?.removeAttribute('aria-describedby');
    activeAnchor = null;
  }

  function togglePopover(anchor, text) {
    if (!activePopover?.hidden && activeAnchor === anchor) {
      hidePopover();
      return;
    }
    showPopover(anchor, text);
  }

  function attachHintToLabel(root, id, text) {
    const label = root.querySelector(`label[for="${id}"]`);
    if (!label || label.querySelector('.ux-hint-trigger')) return;
    label.classList.add('has-ux-hint');
    label.appendChild(createHintTrigger(text));
  }

  function attachHintToElement(el, text) {
    if (!el || el.querySelector('.ux-hint-trigger')) return;
    el.classList.add('has-ux-hint');
    el.appendChild(createHintTrigger(text));
  }

  function initFieldHints(root = document) {
    Object.entries(FIELD_HINTS).forEach(([id, text]) => attachHintToLabel(root, id, text));

    root.querySelectorAll('.fuel-section-label').forEach((section) => {
      const key = getSectionLabelText(section);
      const hint = SECTION_HINTS[key];
      if (!hint) return;
      attachHintToElement(section, hint);
    });

    Object.entries(TAB_HINTS).forEach(([id, text]) => {
      const tab = root.getElementById(id);
      if (!tab) return;
      attachHintToElement(tab, text);
    });

    root.querySelectorAll('[data-ec-section]').forEach((btn) => {
      const hint = EC_SECTION_HINTS[btn.dataset.ecSection];
      if (!hint) return;
      attachHintToElement(btn, hint);
    });

    Object.entries(BUTTON_HINTS).forEach(([id, text]) => {
      const btn = root.getElementById(id);
      if (!btn) return;
      btn.setAttribute('title', text);
      const wrap = btn.closest('.download-action-wrap, .actions, .ec-order-id-row');
      if (wrap && !wrap.querySelector('.ux-hint-trigger')) {
        const hintBtn = createHintTrigger(text);
        hintBtn.classList.add('ux-hint-trigger--btn');
        wrap.classList.add('has-ux-hint-inline');
        wrap.appendChild(hintBtn);
      }
    });

    const pageTitle = root.getElementById('sitePageTitle');
    if (pageTitle && !pageTitle.querySelector('.ux-hint-trigger')) {
      attachHintToElement(
        pageTitle,
        'Shows whether you are on single or bulk mode. Use the matching item in the top navigation menu to switch.'
      );
    }
  }

  function coachmarkStorageKey(gen) {
    return `${COACHMARK_KEY_PREFIX}${gen}`;
  }

  function markBulkCoachmarkSeen(gen) {
    if (!BULK_GENERATORS.has(gen)) return;
    try {
      localStorage.setItem(coachmarkStorageKey(gen), '1');
    } catch (_) {}
  }

  function hasSeenBulkCoachmark(gen) {
    try {
      return localStorage.getItem(coachmarkStorageKey(gen)) === '1';
    } catch (_) {
      return true;
    }
  }

  function dismissBulkCoachmark() {
    if (coachmarkTimer) {
      clearTimeout(coachmarkTimer);
      coachmarkTimer = null;
    }
    if (coachmarkResizeHandler) {
      window.removeEventListener('resize', coachmarkResizeHandler);
      coachmarkResizeHandler = null;
    }
    coachmarkEl?.remove();
    coachmarkEl = null;
    document.querySelectorAll('.bulk-coachmark-target').forEach((el) => {
      el.classList.remove('bulk-coachmark-target');
    });
    document.querySelectorAll('.site-navbar .dropdown.show').forEach((dd) => {
      dd.classList.remove('show');
      const toggle = dd.querySelector('.dropdown-toggle');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
      dd.querySelector('.dropdown-menu')?.classList.remove('show');
    });
    document.body.classList.remove('bulk-coachmark-active');
  }

  function ensureNavExpanded() {
    const collapse = document.getElementById('siteMainNav');
    if (!collapse) return;
    if (window.innerWidth < 992 && !collapse.classList.contains('show') && window.bootstrap?.Collapse) {
      window.bootstrap.Collapse.getOrCreateInstance(collapse, { toggle: false }).show();
    }
  }

  function openNavDropdown(gen) {
    const navItem = document.querySelector(`.site-navbar .nav-item.dropdown[data-nav="${gen}"]`);
    if (!navItem) return null;
    navItem.classList.add('show');
    const toggle = navItem.querySelector('.dropdown-toggle');
    const menu = navItem.querySelector('.dropdown-menu');
    if (toggle) toggle.setAttribute('aria-expanded', 'true');
    menu?.classList.add('show');
    return navItem;
  }

  function positionCoachmarkCard(card, targetRect) {
    const margin = 12;
    const arrow = card.querySelector('.bulk-coachmark-arrow');
    card.style.visibility = 'hidden';
    card.hidden = false;
    const cardRect = card.getBoundingClientRect();
    let top = targetRect.bottom + margin + 8;
    let left = targetRect.left + targetRect.width / 2 - cardRect.width / 2;
    if (top + cardRect.height > window.innerHeight - margin) {
      top = targetRect.top - cardRect.height - margin - 8;
      card.dataset.placement = 'top';
    } else {
      card.dataset.placement = 'bottom';
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - cardRect.width - margin));
    card.style.top = `${Math.round(top)}px`;
    card.style.left = `${Math.round(left)}px`;
    card.style.visibility = '';
    if (arrow) {
      const arrowLeft = targetRect.left + targetRect.width / 2 - left;
      arrow.style.left = `${Math.round(Math.max(18, Math.min(cardRect.width - 18, arrowLeft)))}px`;
    }
  }

  function showBulkCoachmark(gen) {
    if (!BULK_GENERATORS.has(gen) || hasSeenBulkCoachmark(gen)) return;

    const copy = BULK_COACHMARK_COPY[gen];
    if (!copy) return;

    dismissBulkCoachmark();
    ensureNavExpanded();
    const navItem = openNavDropdown(gen);
    if (!navItem) return;

    const bulkLink = navItem.querySelector(`.dropdown-item[data-nav-mode="bulk"]`);
    if (!bulkLink) return;

    bulkLink.classList.add('bulk-coachmark-target');
    document.body.classList.add('bulk-coachmark-active');

    const overlay = document.createElement('div');
    overlay.className = 'bulk-coachmark-overlay';
    overlay.innerHTML = `
      <div class="bulk-coachmark-backdrop" data-coachmark-dismiss></div>
      <div class="bulk-coachmark-card" role="dialog" aria-labelledby="bulkCoachmarkTitle" aria-modal="true">
        <div class="bulk-coachmark-arrow" aria-hidden="true"></div>
        <p class="bulk-coachmark-kicker">Tip</p>
        <h3 class="bulk-coachmark-title" id="bulkCoachmarkTitle"></h3>
        <p class="bulk-coachmark-body"></p>
        <p class="bulk-coachmark-point">Look for <strong class="bulk-coachmark-label"></strong> in the menu above.</p>
        <div class="bulk-coachmark-actions">
          <button type="button" class="btn btn-primary bulk-coachmark-got-it" data-btn-label="Got it">Got it</button>
        </div>
      </div>`;

    const titleEl = overlay.querySelector('.bulk-coachmark-title');
    const bodyEl = overlay.querySelector('.bulk-coachmark-body');
    const labelEl = overlay.querySelector('.bulk-coachmark-label');
    titleEl.textContent = copy.title;
    bodyEl.textContent = copy.body;
    labelEl.textContent = copy.bulkLabel;

    overlay.querySelector('[data-coachmark-dismiss]')?.addEventListener('click', () => {
      markBulkCoachmarkSeen(gen);
      dismissBulkCoachmark();
    });
    overlay.querySelector('.bulk-coachmark-got-it')?.addEventListener('click', () => {
      markBulkCoachmarkSeen(gen);
      dismissBulkCoachmark();
    });

    document.body.appendChild(overlay);
    coachmarkEl = overlay;

    const card = overlay.querySelector('.bulk-coachmark-card');
    const reposition = () => {
      const rect = bulkLink.getBoundingClientRect();
      positionCoachmarkCard(card, rect);
    };
    reposition();
    coachmarkResizeHandler = reposition;
    window.addEventListener('resize', reposition);
    requestAnimationFrame(reposition);
  }

  function scheduleBulkCoachmark(getState) {
    if (typeof getState !== 'function') return;
    if (coachmarkTimer) clearTimeout(coachmarkTimer);
    coachmarkTimer = setTimeout(() => {
      coachmarkTimer = null;
      const state = getState();
      if (!state || state.mode === 'bulk') return;
      if (!BULK_GENERATORS.has(state.generator)) return;
      showBulkCoachmark(state.generator);
    }, 900);
  }

  window.NOOBIUS_FIELD_HINTS = {
    initFieldHints,
    scheduleBulkCoachmark,
    markBulkCoachmarkSeen,
    dismissBulkCoachmark,
  };

  function retryPendingUxInit() {
    if (typeof window.__noobiusUxGetState !== 'function') return;
    initFieldHints(document);
    scheduleBulkCoachmark(window.__noobiusUxGetState);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', retryPendingUxInit);
  } else {
    queueMicrotask(retryPendingUxInit);
  }
})();
