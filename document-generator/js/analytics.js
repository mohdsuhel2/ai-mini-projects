(function (global) {
  'use strict';

  /**
   * Replace with your GA4 Measurement ID from Google Analytics
   * (Admin → Data streams → Web → Measurement ID, format G-XXXXXXXXXX).
   */
  const CONFIG = {
    measurementId: 'G-42LT40CGCN',
  };

  let initialized = false;

  function getMeasurementId() {
    return String(CONFIG.measurementId || '').trim();
  }

  function isPlaceholderId(id) {
    return !id || id === 'G-XXXXXXXXXX' || id === 'G-XXXXXXXX';
  }

  function isConfigured() {
    const id = getMeasurementId();
    return /^G-[A-Z0-9]+$/.test(id) && !isPlaceholderId(id);
  }

  function isDebugMode() {
    if (new URLSearchParams(global.location.search).has('ga_debug')) return true;
    const host = global.location.hostname;
    return host === 'localhost' || host === '127.0.0.1';
  }

  function ensureGtag() {
    if (!isConfigured() || initialized) return isConfigured();

    global.dataLayer = global.dataLayer || [];
    global.gtag = global.gtag || function gtag() {
      global.dataLayer.push(arguments);
    };

    if (!document.querySelector('script[data-noobius-gtag]')) {
      const script = document.createElement('script');
      script.async = true;
      script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(getMeasurementId())}`;
      script.dataset.noobiusGtag = '1';
      document.head.appendChild(script);
    }

    global.gtag('js', new Date());
    global.gtag('config', getMeasurementId(), {
      anonymize_ip: true,
      send_page_view: true,
      debug_mode: isDebugMode(),
      page_path: global.location.pathname + global.location.search,
      page_location: global.location.href,
      page_title: document.title,
    });

    initialized = true;
    return true;
  }

  function inferFormat(filename) {
    const ext = String(filename || '').split('.').pop()?.toLowerCase();
    if (ext === 'pdf' || ext === 'png' || ext === 'zip') return ext;
    return 'unknown';
  }

  function trackDownload(params = {}) {
    if (!ensureGtag()) return;

    const filename = String(params.filename || '');
    const format = params.format || inferFormat(filename);

    global.gtag('event', 'file_download', {
      event_category: 'download',
      file_name: filename,
      file_extension: format,
      generator: String(params.generator || 'unknown'),
      document_mode: String(params.mode || 'unknown'),
      page_path: global.location.pathname,
    });
  }

  function init() {
    ensureGtag();
  }

  global.NOOBIUS_ANALYTICS = {
    CONFIG,
    init,
    isConfigured,
    trackDownload,
    inferFormat,
  };

  init();
})(window);
