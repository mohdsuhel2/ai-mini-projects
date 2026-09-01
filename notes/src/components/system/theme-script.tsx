import { THEME_STORAGE_KEY } from '@/lib/theme'

/**
 * Runs synchronously while the browser parses <head>, before the first paint.
 * The theme lives in IndexedDB, which is async — reading it in an effect would
 * flash a light page at anyone using dark mode. localStorage mirrors the value
 * purely so this script has something synchronous to read.
 */
const SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('${THEME_STORAGE_KEY}');
    var dark = stored === 'dark' ||
      ((!stored || stored === 'system') &&
        window.matchMedia('(prefers-color-scheme: dark)').matches);
    var theme = dark ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  } catch (e) {}
})();
`.trim()

export function ThemeScript() {
  return <script dangerouslySetInnerHTML={{ __html: SCRIPT }} />
}
