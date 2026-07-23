(function () {
  'use strict';

  const COACHMARK_KEY_PREFIX = 'noobius_bulk_coachmark_v1_';
  const BULK_GENERATORS = new Set(['fuel', 'postpaid', 'rent', 'driver']);

  const FIELD_HINTS = {
    amount: 'Total fill amount in rupees. Litres are calculated using the rate per litre.',
    fuelCapacity: 'Fuel quantity in litres. Total amount is calculated using the rate per litre.',
    rate: 'Rate per litre for this receipt. Amount and litres are calculated from each other using this rate.',
    bulkMinTotalAmount: 'Creates as many receipts as needed so the combined total is at least this amount (up to 100 receipts).',
    bulkIdPrefix: 'Text before the numeric part of each receipt ID, e.g. 2026AA.',
    bulkIdStart: 'First 4-digit suffix. Full ID = prefix + suffix, e.g. prefix 2026AA and start 3210 gives 2026AA3210; the next receipt adds a random gap (e.g. 2026AA3248).',
    bulkIdMinGap: 'Smallest random increase between consecutive receipt suffixes.',
    bulkIdMaxGap: 'Largest random increase between consecutive receipt suffixes.',
    bbStatementDate: 'Main billing date. Statement period, due date, and payment history are calculated from this.',
    bbBulkDateFrom: 'Date of the first monthly bill in the batch.',
    bbBulkCount: 'Number of consecutive monthly bills to generate (maximum 24).',
    rrBulkDateFrom: 'Date of the first monthly rent receipt in the batch.',
    rrBulkCount: 'Number of consecutive monthly rent receipts to generate (maximum 24).',
    dsBulkDateFrom: 'Date of the first monthly driver slip in the batch.',
    dsBulkCount: 'Number of consecutive monthly salary slips to generate (maximum 24).',
  };

  const TAB_HINTS = {
    txnModeAmountTab: 'Enter the total rupee amount; litres are derived from the rate.',
    txnModeCapacityTab: 'Enter litres filled; the rupee amount is derived from the rate.',
    bulkValueModeFixedTab: 'Use the same amount or litres on every receipt in the batch.',
    bulkValueModeRandomTab: 'Pick a different random amount or litres for each receipt within your min–max range.',
    bulkTxnModeAmountTab: 'Set fill values in rupees (fixed or random per receipt). Each receipt also uses a slightly random rate within ±₹2 of the base rate.',
    bulkTxnModeCapacityTab: 'Set fill values in litres (fixed or random per receipt). Each receipt also uses a slightly random rate within ±₹2 of the base rate.',
  };

  const SECTION_HINTS = {
    Transaction: 'Choose amount or litres, then set the rate. The other value is calculated automatically.',
  };

  const BUTTON_HINTS = {
    ecAddItemBtn: 'Add a product line with quantity, price, discount, and GST to the invoice.',
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

  const HINT_ICON_BODY = '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>';

  function hintIconMarkup(size = 14) {
    return `<svg class="ux-hint-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${HINT_ICON_BODY}</svg>`;
  }

  function createHintTrigger(text, variant = 'field') {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = variant === 'section' ? 'ux-hint-trigger ux-hint-trigger--section' : 'ux-hint-trigger';
    btn.setAttribute('aria-label', 'More information');
    btn.innerHTML = hintIconMarkup(variant === 'section' ? 16 : 14);
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
    label.classList.add('has-ux-hint', 'has-ux-hint-label');
    label.appendChild(createHintTrigger(text, 'field'));
  }

  function attachHintToElement(el, text, variant = 'field') {
    if (!el || el.querySelector('.ux-hint-trigger')) return;
    el.classList.add('has-ux-hint');
    if (variant === 'section') el.classList.add('has-ux-hint-section');
    el.appendChild(createHintTrigger(text, variant));
  }

  function initFieldHints(root = document) {
    Object.entries(FIELD_HINTS).forEach(([id, text]) => attachHintToLabel(root, id, text));

    root.querySelectorAll('#singleModePanel .fuel-section-label').forEach((section) => {
      const key = getSectionLabelText(section);
      const hint = SECTION_HINTS[key];
      if (hint) attachHintToElement(section, hint, 'section');
    });

    Object.entries(TAB_HINTS).forEach(([id, text]) => {
      const tab = root.getElementById(id);
      if (tab) attachHintToElement(tab, text, 'field');
    });

    Object.entries(BUTTON_HINTS).forEach(([id, text]) => {
      const btn = root.getElementById(id);
      if (!btn) return;
      const wrap = btn.closest('.actions');
      if (wrap && !wrap.querySelector('.ux-hint-trigger')) {
        wrap.classList.add('has-ux-hint-inline');
        wrap.appendChild(createHintTrigger(text));
      }
    });
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
