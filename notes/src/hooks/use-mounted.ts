'use client'

import { useSyncExternalStore } from 'react'

/** Nothing ever changes, so the store never notifies. */
const subscribe = () => () => {}
const getSnapshot = () => true
const getServerSnapshot = () => false

/**
 * Everything the app shows is read from IndexedDB, which does not exist on the
 * server. Gating the tree on this flag means no database code ever runs during
 * server rendering, and the first paint is an honest skeleton rather than a
 * guess that has to be corrected.
 */
export function useMounted(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}
