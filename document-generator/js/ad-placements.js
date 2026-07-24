(function (global) {
  'use strict';

  const PLACEMENT_UNITS = {
    header: 'horizontal',
    'form-end': 'banner',
  };

  const LOAD_TIMEOUT_MS = 12000;
  const AD_STATE = {
    status: 'pending',
    pendingSlots: 0,
    filledSlots: 0,
    waiters: [],
  };

  function notifyAdStateWaiters() {
    if (AD_STATE.status === 'pending') return;
    AD_STATE.waiters.splice(0).forEach((resolve) => resolve(AD_STATE.status));
  }

  function markAdStateFilled() {
    if (AD_STATE.status === 'filled') return;
    AD_STATE.status = 'filled';
    AD_STATE.filledSlots += 1;
    syncDownloadNotes();
    notifyAdStateWaiters();
  }

  function markAdSlotSettled(wasFilled) {
    AD_STATE.pendingSlots = Math.max(0, AD_STATE.pendingSlots - 1);
    if (wasFilled) {
      markAdStateFilled();
      return;
    }
    if (AD_STATE.status === 'filled') return;
    if (AD_STATE.pendingSlots === 0) {
      AD_STATE.status = 'unfilled';
      syncDownloadNotes();
      notifyAdStateWaiters();
    }
  }

  function getAdState() {
    return AD_STATE.status;
  }

  function waitForAdState(maxWaitMs = 2500) {
    if (AD_STATE.status !== 'pending') {
      return Promise.resolve(AD_STATE.status);
    }
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        if (AD_STATE.status === 'pending') {
          AD_STATE.status = 'unfilled';
          syncDownloadNotes();
          notifyAdStateWaiters();
        }
        resolve(AD_STATE.status);
      }, maxWaitMs);

      AD_STATE.waiters.push((status) => {
        window.clearTimeout(timer);
        resolve(status);
      });
    });
  }

  function syncDownloadNotes() {
    const show = AD_STATE.status === 'filled';
    document.querySelectorAll('.ad-download-note').forEach((el) => {
      el.hidden = !show;
    });
  }

  function watchAdLoad(container, ins) {
    container.classList.add('site-ad-slot', 'site-ad-slot--pending');
    container.setAttribute('role', 'complementary');
    container.setAttribute('aria-label', 'Advertisement');

    let settled = false;
    AD_STATE.pendingSlots += 1;

    const settle = (wasFilled) => {
      if (settled) return;
      settled = true;
      markAdSlotSettled(wasFilled);
    };

    const reveal = () => {
      container.classList.remove('site-ad-slot--pending');
      container.classList.add('site-ad-slot--loaded');
      settle(true);
    };

    const remove = () => {
      container.remove();
      settle(false);
    };

    const isFilled = () => {
      const status = ins.getAttribute('data-ad-status');
      if (status === 'filled') return true;
      if (status === 'unfilled') return false;
      const iframe = ins.querySelector('iframe');
      if (!iframe) return false;
      const height = iframe.offsetHeight || parseInt(ins.style.height, 10) || 0;
      return height > 40;
    };

    const evaluate = () => {
      const status = ins.getAttribute('data-ad-status');
      if (status === 'unfilled') {
        remove();
        return true;
      }
      if (isFilled()) {
        reveal();
        return true;
      }
      return false;
    };

    if (evaluate()) return;

    const observer = new MutationObserver(() => {
      if (evaluate()) observer.disconnect();
    });
    observer.observe(ins, { attributes: true, attributeFilter: ['data-ad-status', 'style'], childList: true, subtree: true });

    window.setTimeout(() => {
      observer.disconnect();
      if (!container.classList.contains('site-ad-slot--loaded')) remove();
    }, LOAD_TIMEOUT_MS);
  }

  function initPlacements(root = document) {
    const ads = global.NOOBIUS_ADSENSE;
    if (!ads?.mountSlot || !ads?.getUnit) return 0;

    let mounted = 0;
    root.querySelectorAll('[data-ad-placement]').forEach((container) => {
      const placement = container.getAttribute('data-ad-placement');
      const unitKey = PLACEMENT_UNITS[placement];
      if (!unitKey) return;

      const unit = ads.getUnit(unitKey);
      if (!unit) return;

      const mount = document.createElement('div');
      mount.className = 'site-ad-mount';
      container.appendChild(mount);

      const ins = ads.mountSlot(mount, unit);
      if (ins) {
        mounted += 1;
        watchAdLoad(container, ins);
      }
    });

    return mounted;
  }

  function boot() {
    const mounted = initPlacements();
    if (mounted === 0 && AD_STATE.status === 'pending') {
      AD_STATE.status = 'unfilled';
      syncDownloadNotes();
      notifyAdStateWaiters();
    } else {
      syncDownloadNotes();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  global.NOOBIUS_AD_PLACEMENTS = {
    initPlacements,
    getAdState,
    waitForAdState,
    syncDownloadNotes,
  };
})(window);
