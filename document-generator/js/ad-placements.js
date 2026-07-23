(function (global) {
  'use strict';

  const mountedSlotIds = new Set();

  function ensureMountPoint(container) {
    let mount = container.querySelector('.site-ad-mount');
    if (!mount) {
      if (!container.querySelector('.site-ad-label')) {
        const label = document.createElement('p');
        label.className = 'site-ad-label';
        label.textContent = 'Advertisement';
        container.appendChild(label);
      }
      mount = document.createElement('div');
      mount.className = 'site-ad-mount';
      container.appendChild(mount);
    }
    return mount;
  }

  function initPlacements(root = document) {
    const ads = global.NOOBIUS_ADSENSE;
    if (!ads?.mountSlot || !ads?.getUnit) return;

    root.querySelectorAll('[data-ad-placement]').forEach((container) => {
      const unitKey = container.getAttribute('data-ad-placement');
      const unit = ads.getUnit(unitKey);
      if (!unit) return;

      const slotId = String(unit.slotId || '').trim();
      if (!slotId || mountedSlotIds.has(slotId)) return;
      mountedSlotIds.add(slotId);

      const variant = container.getAttribute('data-ad-variant') || unit.variant || unitKey;
      container.classList.add('site-ad-slot', `site-ad-slot--${variant}`);
      container.setAttribute('role', 'complementary');
      container.setAttribute('aria-label', 'Advertisement');

      const mount = ensureMountPoint(container);
      ads.mountSlot(mount, unit);
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
