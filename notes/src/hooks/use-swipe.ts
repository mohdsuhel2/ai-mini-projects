'use client'

import { useCallback, useRef } from 'react'

/** Far enough to be deliberate, short enough to feel like a flick. */
const DISTANCE_PX = 60
/** How much more horizontal than vertical, so a scroll never counts. */
const DIRECTION_RATIO = 1.5

/**
 * True when the gesture began inside something that scrolls sideways.
 *
 * The category picker and the toolbar are horizontal scrollers; a swipe there
 * belongs to them, not to the pane behind them.
 */
function startedInsideHorizontalScroller(target: EventTarget | null): boolean {
  let node = target instanceof Element ? target : null
  while (node) {
    if (node.scrollWidth > node.clientWidth + 4) {
      const overflow = getComputedStyle(node).overflowX
      if (overflow === 'auto' || overflow === 'scroll') return true
    }
    node = node.parentElement
  }
  return false
}

/**
 * Left/right flick, for moving between two panes on a phone.
 *
 * Touch and pen only: a mouse has the tabs, and treating a click-drag as a
 * swipe would fire every time someone selected text.
 */
export function useSwipe(onSwipe: (direction: 'left' | 'right') => void) {
  const start = useRef<{ x: number; y: number } | null>(null)

  return {
    onPointerDown: useCallback((event: React.PointerEvent) => {
      if (event.pointerType === 'mouse' || startedInsideHorizontalScroller(event.target)) {
        start.current = null
        return
      }
      start.current = { x: event.clientX, y: event.clientY }
    }, []),
    onPointerUp: useCallback(
      (event: React.PointerEvent) => {
        const from = start.current
        start.current = null
        if (!from) return

        const dx = event.clientX - from.x
        const dy = event.clientY - from.y
        if (Math.abs(dx) < DISTANCE_PX) return
        if (Math.abs(dx) < Math.abs(dy) * DIRECTION_RATIO) return

        onSwipe(dx < 0 ? 'left' : 'right')
      },
      [onSwipe],
    ),
    onPointerCancel: useCallback(() => {
      start.current = null
    }, []),
  }
}
