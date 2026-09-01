/**
 * The active surface lives in the URL as `?tab=`, so a refresh, a bookmark or a
 * shared link all land where the user was.
 *
 * This is read and written directly through the History API rather than through
 * the Next router: the app is a single statically prerendered page, and
 * `useSearchParams` would opt it into dynamic rendering and a Suspense boundary
 * for a value that only ever matters in the browser.
 */

export const TAB_PARAM = 'tab'

export type Tab = 'notes' | 'todos'

/** Notes is the landing surface. */
export const DEFAULT_TAB: Tab = 'notes'

const TABS: Tab[] = ['notes', 'todos']

function isTab(value: string | null): value is Tab {
  return value !== null && (TABS as string[]).includes(value)
}

/**
 * Anything unrecognised falls back to the default rather than erroring — a
 * hand-edited or stale URL should still open the app.
 */
export function parseTab(search: string): Tab {
  try {
    const value = new URLSearchParams(search).get(TAB_PARAM)?.toLowerCase().trim() ?? null
    return isTab(value) ? value : DEFAULT_TAB
  } catch {
    return DEFAULT_TAB
  }
}

/** The URL for a tab, preserving every other parameter already present. */
export function urlForTab(currentUrl: string, tab: Tab, base = 'http://x'): string {
  const url = new URL(currentUrl, base)
  url.searchParams.set(TAB_PARAM, tab)
  return `${url.pathname}${url.search}${url.hash}`
}

export type Mode = 'day' | 'notes'

export function tabToMode(tab: Tab): Mode {
  return tab === 'notes' ? 'notes' : 'day'
}

export function modeToTab(mode: Mode): Tab {
  return mode === 'notes' ? 'notes' : 'todos'
}
