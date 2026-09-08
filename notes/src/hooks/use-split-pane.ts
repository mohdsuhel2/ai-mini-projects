'use client'

import { useCallback, useRef, useState } from 'react'

const STORAGE_KEY = 'simply-notes:split'
/** Neither pane may be squeezed below this share of the row. */
const MIN_PCT = 28
const MAX_PCT = 72
const DEFAULT_PCT = 51

function clamp(value: number): number {
  return Math.min(MAX_PCT, Math.max(MIN_PCT, value))
}

/**
 * Read once, as the initial state.
 *
 * The app only ever mounts in the browser, but the guard keeps this safe
 * anywhere — and reading here rather than in an effect means the panes never
 * paint at the default width and then jump to the stored one.
 */
function readStoredSplit(): number {
  if (typeof window === 'undefined') return DEFAULT_PCT
  try {
    const stored = Number(window.localStorage.getItem(STORAGE_KEY))
    return Number.isFinite(stored) && stored > 0 ? clamp(stored) : DEFAULT_PCT
  } catch {
    // Private mode, or storage disabled. The default is a fine answer.
    return DEFAULT_PCT
  }
}

/**
 * A draggable divider between the two day panes.
 *
 * The split is a preference of this browser rather than of the data, so it
 * lives in localStorage and never travels with a backup. Reading it in an
 * initialiser rather than an effect means the panes never paint at the default
 * width and then jump.
 */
export function useSplitPane() {
  const [pct, setPct] = useState(readStoredSplit)
  const rowRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const commit = useCallback((next: number) => {
    const value = clamp(next)
    setPct(value)
    try {
      window.localStorage.setItem(STORAGE_KEY, String(value))
    } catch {
      // Losing the preference is not worth failing a drag over.
    }
  }, [])

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true
    // Captured so the drag survives the pointer leaving the 10px handle.
    event.currentTarget.setPointerCapture(event.pointerId)
  }, [])

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging.current || !rowRef.current) return
      const box = rowRef.current.getBoundingClientRect()
      if (box.width === 0) return
      commit(((event.clientX - box.left) / box.width) * 100)
    },
    [commit],
  )

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false
    event.currentTarget.releasePointerCapture(event.pointerId)
  }, [])

  /** Keyboard: the divider is a real separator, so it moves with arrows. */
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'ArrowLeft') commit(pct - 2)
      if (event.key === 'ArrowRight') commit(pct + 2)
      if (event.key === 'Home') commit(DEFAULT_PCT)
    },
    [commit, pct],
  )

  return { pct, rowRef, handleProps: { onPointerDown, onPointerMove, onPointerUp, onKeyDown } }
}
