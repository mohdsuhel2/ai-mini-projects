import type { ThemePreference } from '@/types'

/**
 * Deliberately not a client module. The blocking script in <head> is rendered
 * on the server and interpolates this key into its source; importing it from a
 * `'use client'` file makes Next substitute a client-reference stub, which
 * lands in the HTML as a syntax error and silently disables the script.
 */
export const THEME_STORAGE_KEY = 'simply-notes-theme'

export function resolveTheme(preference: ThemePreference): 'light' | 'dark' {
  if (preference !== 'system') return preference
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}
