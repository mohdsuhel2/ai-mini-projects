'use client'

import { useCallback, useSyncExternalStore } from 'react'

/**
 * Subscribed rather than mirrored into state: the media query is an external
 * store, and reading it directly avoids a render pass where the value is wrong.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [query],
  )

  const getSnapshot = useCallback(() => window.matchMedia(query).matches, [query])

  // The server cannot know the viewport; the mobile-first layout is the safe
  // assumption, and useMounted keeps it from ever being painted.
  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}

/** Both panes fit side by side from here up. */
export const WIDE_QUERY = '(min-width: 1100px)'

export function useIsWide(): boolean {
  return useMediaQuery(WIDE_QUERY)
}

export function usePrefersReducedMotion(): boolean {
  return useMediaQuery('(prefers-reduced-motion: reduce)')
}
