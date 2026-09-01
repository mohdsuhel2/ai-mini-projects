'use client'

import { useEffect } from 'react'

export interface Hotkey {
  /** Lower-case `event.key`, e.g. "n", "k", "escape". */
  key: string
  meta?: boolean
  shift?: boolean
  handler: (event: KeyboardEvent) => void
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    target.isContentEditable ||
    target.closest('[role="dialog"]') !== null
  )
}

/**
 * Single-letter shortcuts are deliberately inert while the user is typing or
 * inside a dialog: a shortcut that fires mid-sentence is worse than no
 * shortcut. Modifier combinations are always live.
 */
export function useHotkeys(hotkeys: Hotkey[], enabled = true): void {
  useEffect(() => {
    if (!enabled) return

    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      for (const hotkey of hotkeys) {
        if (hotkey.key !== key) continue
        const wantsMeta = Boolean(hotkey.meta)
        const hasMeta = event.metaKey || event.ctrlKey
        if (wantsMeta !== hasMeta) continue
        if (Boolean(hotkey.shift) !== event.shiftKey) continue
        if (!wantsMeta && key !== 'escape' && isTypingTarget(event.target)) continue
        event.preventDefault()
        hotkey.handler(event)
        return
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [hotkeys, enabled])
}
