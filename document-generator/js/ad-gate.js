(function (global) {
  'use strict';

  const SINGLE_DELAY_SEC = 5;
  const BULK_DELAY_SEC = 15;
  const AD_CHECK_WAIT_MS = 2500;

  let modalEl = null;
  let timerId = null;
  let activeResolve = null;
  let activeReject = null;

  function ensureModal() {
    if (modalEl) return modalEl;

    modalEl = document.createElement('div');
    modalEl.id = 'adGateModal';
    modalEl.className = 'ad-gate-modal hidden';
    modalEl.setAttribute('aria-hidden', 'true');
    modalEl.innerHTML = `
      <div class="ad-gate-backdrop" data-ad-gate-close></div>
      <div class="ad-gate-dialog" role="dialog" aria-modal="true" aria-labelledby="adGateTitle">
        <button type="button" class="ad-gate-close" data-ad-gate-close aria-label="Close">&times;</button>
        <div class="ad-gate-dialog-body">
          <p class="ad-gate-kicker">Sponsored</p>
          <h2 class="ad-gate-title" id="adGateTitle">Support us to download</h2>
          <p class="ad-gate-subtitle" id="adGateSubtitle">Please view the ads below. Your download will unlock shortly.</p>
          <div class="ad-gate-slots" id="adGateSlots"></div>
          <p class="ad-gate-timer-note" id="adGateTimerNote"></p>
        </div>
        <div class="ad-gate-actions">
          <button type="button" class="btn btn-secondary" data-ad-gate-close>Cancel</button>
          <button type="button" class="btn btn-primary" id="adGateDownloadBtn" disabled>
            <span id="adGateDownloadBtnLabel">Download</span>
          </button>
        </div>
      </div>`;

    document.body.appendChild(modalEl);

    modalEl.querySelectorAll('[data-ad-gate-close]').forEach((el) => {
      el.addEventListener('click', () => closeModal(false));
    });

    modalEl.querySelector('#adGateDownloadBtn').addEventListener('click', () => {
      const btn = modalEl.querySelector('#adGateDownloadBtn');
      if (btn.disabled) return;
      const task = activeResolve;
      closeModal(true);
      if (typeof task === 'function') {
        Promise.resolve().then(() => task());
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modalEl && !modalEl.classList.contains('hidden')) {
        closeModal(false);
      }
    });

    return modalEl;
  }

  function getDelaySec(tier) {
    return tier === 'bulk' ? BULK_DELAY_SEC : SINGLE_DELAY_SEC;
  }

  async function resolveAdsAvailability() {
    const ads = global.NOOBIUS_ADSENSE;
    const placements = global.NOOBIUS_AD_PLACEMENTS;
    if (!ads?.isConfigured?.()) return false;

    const current = placements?.getAdState?.();
    if (current === 'filled') return true;
    if (current === 'unfilled') return false;

    const resolved = await placements?.waitForAdState?.(AD_CHECK_WAIT_MS);
    return resolved === 'filled';
  }

  function buildAdSlots(container) {
    container.innerHTML = '';
    const slots = global.NOOBIUS_ADSENSE?.getPopupSlots?.() || [];

    if (!slots.length) {
      for (let i = 0; i < 3; i += 1) {
        const placeholder = document.createElement('div');
        placeholder.className = 'ad-gate-slot ad-gate-slot-placeholder';
        placeholder.innerHTML = '<span>Advertisement</span>';
        container.appendChild(placeholder);
      }
      return;
    }

    slots.forEach((slot, index) => {
      const wrap = document.createElement('div');
      const variant = slot.variant || 'auto';
      wrap.className = `ad-gate-slot ad-gate-slot--${variant}`;
      wrap.dataset.adSlotKey = slot.key || `slot-${index}`;
      container.appendChild(wrap);
      global.NOOBIUS_ADSENSE?.mountSlot?.(wrap, slot);
    });
  }

  function startCountdown(btn, labelEl, timerNoteEl, delaySec) {
    let remaining = delaySec;
    btn.disabled = true;

    const render = () => {
      const plural = remaining === 1 ? 'second' : 'seconds';
      labelEl.textContent = `Download in ${remaining}s`;
      timerNoteEl.textContent = `Download unlocks in ${remaining} ${plural}.`;
    };

    render();
    clearInterval(timerId);
    timerId = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(timerId);
        timerId = null;
        btn.disabled = false;
        labelEl.textContent = btn.dataset.downloadLabel || 'Download';
        timerNoteEl.textContent = 'Thank you! You can download now.';
        return;
      }
      render();
    }, 1000);
  }

  function closeModal(confirmed) {
    clearInterval(timerId);
    timerId = null;
    if (!modalEl) return;
    modalEl.classList.add('hidden');
    modalEl.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('ad-gate-open');
    if (!confirmed && typeof activeReject === 'function') {
      activeReject(new Error('cancelled'));
    }
    if (!confirmed) {
      activeResolve = null;
      activeReject = null;
    } else {
      activeReject = null;
    }
  }

  function openModal(options) {
    const {
      tier = 'single',
      title = 'Support us to download',
      subtitle = 'Please view the ads below. Your download will unlock shortly.',
      buttonLabel = 'Download',
      onConfirm,
    } = options || {};

    return new Promise((resolve, reject) => {
      const el = ensureModal();
      const delaySec = getDelaySec(tier);
      const btn = el.querySelector('#adGateDownloadBtn');
      const labelEl = el.querySelector('#adGateDownloadBtnLabel');
      const timerNoteEl = el.querySelector('#adGateTimerNote');
      const slotsEl = el.querySelector('#adGateSlots');

      el.querySelector('#adGateTitle').textContent = title;
      el.querySelector('#adGateSubtitle').textContent = subtitle;
      btn.dataset.downloadLabel = buttonLabel;

      buildAdSlots(slotsEl);

      activeResolve = onConfirm;
      activeReject = reject;

      el.classList.remove('hidden');
      el.setAttribute('aria-hidden', 'false');
      document.body.classList.add('ad-gate-open');
      startCountdown(btn, labelEl, timerNoteEl, delaySec);
    }).catch((err) => {
      if (err?.message === 'cancelled') return;
      throw err;
    });
  }

  function open(options) {
    const { onConfirm } = options || {};

    if (typeof onConfirm !== 'function') {
      return Promise.reject(new Error('onConfirm is required'));
    }

    return resolveAdsAvailability().then((adsWorking) => {
      if (!adsWorking) {
        return Promise.resolve().then(() => onConfirm());
      }
      return openModal(options);
    });
  }

  global.NOOBIUS_AD_GATE = {
    SINGLE_DELAY_SEC,
    BULK_DELAY_SEC,
    open,
    close: () => closeModal(false),
    resolveAdsAvailability,
  };
})(window);
