(function (global) {
  'use strict';

  const PLACEMENT_UNITS = {
    header: 'horizontal',
    'form-end': 'banner',
  };

  const LOAD_TIMEOUT_MS = 12000;

  function watchAdLoad(container, ins) {
    container.classList.add('site-ad-slot', 'site-ad-slot--pending');
    container.setAttribute('role', 'complementary');
    container.setAttribute('aria-label', 'Advertisement');

    const reveal = () => {
      container.classList.remove('site-ad-slot--pending');
      container.classList.add('site-ad-slot--loaded');
    };

    const remove = () => {
      container.remove();
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
    if (!ads?.mountSlot || !ads?.getUnit) return;

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
      if (ins) watchAdLoad(container, ins);
    });
  }

  function boot() {
    initPlacements();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  global.NOOBIUS_AD_PLACEMENTS = {
    initPlacements,
  };
})(window);
