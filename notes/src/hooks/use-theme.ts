'use client'

import { useCallback, useEffect } from 'react'
import { useSettings } from '@/hooks/use-data'
import { setThemePreference } from '@/features/settings/api'
import { track } from '@/lib/analytics'
import { THEME_STORAGE_KEY, resolveTheme } from '@/lib/theme'
import type { ThemePreference } from '@/types'

export { THEME_STORAGE_KEY, resolveTheme }

function apply(preference: ThemePreference): void {
  const resolved = resolveTheme(preference)
  document.documentElement.dataset.theme = resolved
  document.documentElement.style.colorScheme = resolved
  // Mirrored to localStorage purely so the blocking script in <head> can read
  // it before first paint; IndexedDB is async and would flash.
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // Private browsing, storage disabled — the in-memory theme still works.
  }
}

export function useTheme() {
  const settings = useSettings()
  const preference = settings.theme

  useEffect(() => {
    apply(preference)
    if (preference !== 'system') return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => apply('system')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [preference])

  const setTheme = useCallback(async (next: ThemePreference) => {
    apply(next)
    await setThemePreference(next)
    track('theme_changed', { theme: next })
  }, [])

  const toggle = useCallback(() => {
    const next = resolveTheme(preference) === 'dark' ? 'light' : 'dark'
    void setTheme(next)
  }, [preference, setTheme])

  return { preference, resolved: resolveTheme(preference), setTheme, toggle }
}
