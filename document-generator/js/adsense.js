(function (global) {
  const AD_UNITS = {
    horizontal: {
      key: 'horizontal',
      slotId: '1558430143',
      format: 'auto',
      fullWidthResponsive: true,
      variant: 'horizontal',
    },
    square: {
      key: 'square',
      slotId: '5034374792',
      format: 'auto',
      fullWidthResponsive: true,
      variant: 'square',
    },
    banner: {
      key: 'banner',
      slotId: '1095129785',
      format: 'auto',
      fullWidthResponsive: true,
      variant: 'horizontal',
    },
  };

  const CONFIG = {
    publisherId: 'ca-pub-2293170892331368',
    loadAdScript: false,
    units: AD_UNITS,
    /** Reuses the same ad units inside the download popup. */
    popupSlots: [AD_UNITS.horizontal, AD_UNITS.square, AD_UNITS.banner],
  };

  function upsertMeta(name, content) {
    if (!content) return;
    let el = document.querySelector(`meta[name="${name}"]`);
    if (!el) {
      el = document.createElement('meta');
      el.setAttribute('name', name);
      document.head.appendChild(el);
    }
    el.setAttribute('content', content);
  }

  function ensureAdScript() {
    const publisherId = String(CONFIG.publisherId || '').trim();
    if (!publisherId) return false;
    if (document.querySelector('script[src*="adsbygoogle.js"]')) return true;

    const script = document.createElement('script');
    script.async = true;
    script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(publisherId)}`;
    script.crossOrigin = 'anonymous';
    script.dataset.adsenseClient = publisherId;
    document.head.appendChild(script);
    return true;
  }

  function apply() {
    const publisherId = String(CONFIG.publisherId || '').trim();
    if (!publisherId) return;
    upsertMeta('google-adsense-account', publisherId);
    if (CONFIG.loadAdScript) ensureAdScript();
  }

  function getUnit(unitKey) {
    const unit = CONFIG.units?.[unitKey];
    if (!unit?.slotId) return null;
    return { ...unit };
  }

  function getPopupSlots() {
    return (CONFIG.popupSlots || []).filter((slot) => String(slot.slotId || '').trim());
  }

  function mountSlot(container, slot) {
    if (!container || !slot?.slotId) return;
    const publisherId = String(CONFIG.publisherId || '').trim();
    if (!publisherId) return;

    ensureAdScript();

    const ins = document.createElement('ins');
    ins.className = 'adsbygoogle';
    ins.style.display = 'block';
    ins.setAttribute('data-ad-client', publisherId);
    ins.setAttribute('data-ad-slot', String(slot.slotId).trim());
    if (slot.format) ins.setAttribute('data-ad-format', slot.format);
    if (slot.fullWidthResponsive) ins.setAttribute('data-full-width-responsive', 'true');
    if (slot.layout) ins.setAttribute('data-ad-layout', slot.layout);
    if (slot.layoutKey) ins.setAttribute('data-ad-layout-key', slot.layoutKey);

    container.innerHTML = '';
    container.appendChild(ins);

    try {
      (global.adsbygoogle = global.adsbygoogle || []).push({});
    } catch (_) {}

    return ins;
  }

  function refreshPopupAds() {
    getPopupSlots().forEach(() => {
      try {
        (global.adsbygoogle = global.adsbygoogle || []).push({});
      } catch (_) {}
    });
  }

  global.NOOBIUS_ADSENSE = {
    CONFIG,
    AD_UNITS,
    apply,
    getUnit,
    getPopupSlots,
    mountSlot,
    refreshPopupAds,
    ensureAdScript,
  };

  apply();
})(window);
