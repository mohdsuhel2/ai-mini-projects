(function () {
  const THEME_KEY = 'bpDocGeneratorTheme';

  const SiteLoader = (function () {
    let count = 0;
    let el;
    let labelEl;

    function ensure() {
      if (el) return;
      el = document.createElement('div');
      el.id = 'siteLoader';
      el.className = 'site-loader';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('aria-busy', 'false');
      el.innerHTML = `
        <div class="site-loader-card">
          <div class="site-loader-spinner" aria-hidden="true"></div>
          <p class="site-loader-text">Loading…</p>
        </div>`;
      labelEl = el.querySelector('.site-loader-text');
      document.body.appendChild(el);
    }

    function updateMessage(message) {
      if (!message || !labelEl) return;
      labelEl.textContent = message;
    }

    function show(message) {
      ensure();
      count += 1;
      if (message && labelEl) labelEl.textContent = message;
      el.classList.add('is-visible');
      el.setAttribute('aria-busy', 'true');
      document.body.classList.add('site-is-loading');
    }

    function hide() {
      if (!el || count === 0) return;
      count = Math.max(0, count - 1);
      if (count === 0) {
        el.classList.remove('is-visible');
        el.setAttribute('aria-busy', 'false');
        document.body.classList.remove('site-is-loading');
      }
    }

    function reset() {
      count = 0;
      if (!el) return;
      el.classList.remove('is-visible');
      el.setAttribute('aria-busy', 'false');
      document.body.classList.remove('site-is-loading');
    }

    async function withAsync(task, message) {
      show(message || 'Loading…');
      try {
        return await (typeof task === 'function' ? task() : task);
      } finally {
        hide();
      }
    }

    function initPageLoader() {
      // No boot-time loader — navigation and in-page tabs stay instant.
      // Blank screen is only shown for explicit async work (PDF/ZIP generation).
    }

    function runDeferred(message, fn) {
      show(message || 'Loading…');
      requestAnimationFrame(() => {
        setTimeout(() => {
          try {
            fn();
          } finally {
            hide();
          }
        }, 0);
      });
    }

    return { show, hide, reset, updateMessage, withAsync, runDeferred, initPageLoader };
  })();

  const ICON = {
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    menu: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
    file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  };

  function svg(paths, className, size) {
    return `<svg class="${className}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  }

  function getTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  function applyTheme(theme, persist) {
    const next = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    if (persist !== false) {
      try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
    }
    document.querySelectorAll('.theme-toggle').forEach(btn => {
      const isDark = next === 'dark';
      btn.setAttribute('aria-label', isDark ? 'Switch to light mode' : 'Switch to dark mode');
      btn.title = isDark ? 'Light mode' : 'Dark mode';
    });
  }

  function toggleTheme() {
    applyTheme(getTheme() === 'dark' ? 'light' : 'dark');
  }

  function mountThemeIcons() {
    const markup = svg(ICON.sun, 'theme-icon theme-icon-sun', 18) + svg(ICON.moon, 'theme-icon theme-icon-moon', 18);
    document.querySelectorAll('.theme-toggle').forEach(btn => {
      btn.innerHTML = markup;
      if (!btn.dataset.themeWired) {
        btn.dataset.themeWired = '1';
        btn.addEventListener('click', toggleTheme);
      }
    });
  }

  function initThemeEarly() {
    let theme = null;
    try { theme = localStorage.getItem(THEME_KEY); } catch (_) {}
    if (theme !== 'light' && theme !== 'dark') {
      theme = 'light';
    }
    document.documentElement.setAttribute('data-theme', theme);
  }

  const NAV_PAGE_META = {
    fuel: {
      menu: 'Fuel Receipt',
      single: 'Single Fuel Receipt',
      bulk: 'Bulk Fuel Receipt Generation',
      hasMode: true,
    },
    postpaid: {
      menu: 'Postpaid Bill',
      single: 'Single Postpaid Bill',
      bulk: 'Bulk Postpaid Bill Generation',
      hasMode: true,
    },
    rent: {
      menu: 'Rent Receipt',
      single: 'Single Rent Receipt',
      bulk: 'Bulk Rent Receipt Generation',
      hasMode: true,
    },
    driver: {
      menu: 'Driver Slip',
      single: 'Single Driver Slip',
      bulk: 'Bulk Driver Slip Generation',
      hasMode: true,
    },
    ecommerce: {
      menu: 'Ecommerce Invoice',
      title: 'Ecommerce Invoice',
      hasMode: false,
    },
    about: {
      menu: 'About',
      title: 'About NOOBius',
      hasMode: false,
    },
  };

  function updatePageContextBar(page, mode, options) {
    const bar = document.getElementById('sitePageContext');
    const categoryEl = document.getElementById('sitePageCategory');
    const titleEl = document.getElementById('sitePageTitle');
    if (!bar || !titleEl) return;

    const meta = NAV_PAGE_META[page];
    if (!meta) {
      bar.hidden = true;
      return;
    }

    bar.hidden = false;
    if (meta.hasMode) {
      if (categoryEl) {
        categoryEl.textContent = meta.menu;
        categoryEl.hidden = false;
      }
      titleEl.textContent = (options && options.pageTitle)
        || (mode === 'bulk' ? meta.bulk : meta.single);
    } else {
      if (categoryEl) categoryEl.hidden = true;
      titleEl.textContent = (options && options.pageTitle) || meta.title || meta.menu;
    }
  }

  function setActiveNav(options) {
    const page = document.body.dataset.page;
    if (!page) return;
    const mode = (options && options.mode)
      || (new URLSearchParams(location.search).get('mode') === 'bulk' ? 'bulk' : 'single');

    document.querySelectorAll('.site-navbar .nav-link[data-nav]').forEach(link => {
      const isActive = link.dataset.nav === page;
      link.classList.toggle('active', isActive);
      if (isActive) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });

    document.querySelectorAll('.site-navbar .nav-item[data-nav]').forEach(item => {
      item.classList.toggle('active', item.dataset.nav === page);
    });

    document.querySelectorAll('.site-navbar .dropdown-item[data-nav-gen]').forEach(link => {
      const isActive = link.dataset.navGen === page && link.dataset.navMode === mode;
      link.classList.toggle('active', isActive);
      if (isActive) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });

    updatePageContextBar(page, mode, options);
  }

  function initMobileNav() {
    const collapseEl = document.getElementById('siteMainNav');
    if (!collapseEl || !window.bootstrap) return;
    const collapse = window.bootstrap.Collapse.getOrCreateInstance(collapseEl, { toggle: false });
    collapseEl.querySelectorAll('.dropdown-item, .nav-link:not(.dropdown-toggle)').forEach(link => {
      link.addEventListener('click', () => {
        if (collapseEl.classList.contains('show')) collapse.hide();
      });
    });
  }

  function init() {
    mountThemeIcons();
    setActiveNav();
    initMobileNav();
  }

  initThemeEarly();
  SiteLoader.initPageLoader();
  window.Site = { init, applyTheme, toggleTheme, getTheme, setActiveNav };
  window.SiteLoader = SiteLoader;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
