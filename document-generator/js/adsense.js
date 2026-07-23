(function (global) {
  const CONFIG = {
    publisherId: 'ca-pub-2293170892331368',
    loadAdScript: false,
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

  function apply() {
    const publisherId = String(CONFIG.publisherId || '').trim();
    if (!publisherId) return;

    upsertMeta('google-adsense-account', publisherId);

    if (!CONFIG.loadAdScript) return;
    if (document.querySelector('script[src*="adsbygoogle.js"]')) return;

    const script = document.createElement('script');
    script.async = true;
    script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(publisherId)}`;
    script.crossOrigin = 'anonymous';
    script.dataset.adsenseClient = publisherId;
    document.head.appendChild(script);
  }

  global.NOOBIUS_ADSENSE = { CONFIG, apply };
  apply();
})(window);
