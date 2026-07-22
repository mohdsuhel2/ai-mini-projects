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

    function show(message) {
      ensure();
      count += 1;
      if (message && labelEl) labelEl.textContent = message;
      el.classList.add('is-visible');
      el.setAttribute('aria-busy', 'true');
      document.body.classList.add('site-is-loading');
      document.documentElement.classList.remove('site-boot-loading');
    }

    function hide() {
      if (!el || count === 0) return;
      count = Math.max(0, count - 1);
      if (count === 0) {
        el.classList.remove('is-visible');
        el.setAttribute('aria-busy', 'false');
        document.body.classList.remove('site-is-loading');
        document.documentElement.classList.remove('site-boot-loading');
      }
    }

    function reset() {
      count = 0;
      if (!el) return;
      el.classList.remove('is-visible');
      el.setAttribute('aria-busy', 'false');
      document.body.classList.remove('site-is-loading');
      document.documentElement.classList.remove('site-boot-loading');
    }

    async function withAsync(task, message) {
      show(message || 'Loading…');
      try {
        return await (typeof task === 'function' ? task() : task);
      } finally {
        hide();
      }
    }

    function isSameOriginNavigation(href) {
      try {
        const url = new URL(href, location.href);
        if (url.origin !== location.origin) return false;
        if (url.pathname === location.pathname && url.search === location.search && !url.hash) return false;
        return /\.html?$/i.test(url.pathname) || url.pathname.endsWith('/');
      } catch (_) {
        return false;
      }
    }

    function initNavigationLoader() {
      document.addEventListener('click', event => {
        const anchor = event.target.closest('a[href]');
        if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
        if (anchor.dataset.noLoader !== undefined) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const href = anchor.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) return;
        if (!isSameOriginNavigation(href)) return;
        show('Opening…');
      });
    }

    function initPageLoader() {
      if (document.readyState === 'complete') {
        reset();
        return;
      }
      show('Loading…');
      window.addEventListener('load', () => {
        requestAnimationFrame(() => {
          setTimeout(reset, 100);
        });
      }, { once: true });
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

    return { show, hide, reset, withAsync, runDeferred, initNavigationLoader, initPageLoader };
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

  function setActiveNav() {
    const page = document.body.dataset.page;
    if (!page) return;
    document.querySelectorAll('.site-nav-link[data-nav]').forEach(link => {
      link.classList.toggle('active', link.dataset.nav === page);
    });
  }

  function initMobileNav() {
    const btn = document.getElementById('siteMenuBtn');
    const nav = document.querySelector('.site-nav');
    if (!btn || !nav) return;
    btn.innerHTML = svg(ICON.menu, 'icon', 18);
    btn.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    nav.querySelectorAll('.site-nav-link').forEach(link => {
      link.addEventListener('click', () => nav.classList.remove('open'));
    });
  }

  function init() {
    mountThemeIcons();
    setActiveNav();
    initMobileNav();
    SiteLoader.initNavigationLoader();
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
