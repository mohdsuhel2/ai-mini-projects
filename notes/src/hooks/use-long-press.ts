'use client'

import { useCallback, useRef } from 'react'

/** Long enough not to fire on a tap, short enough not to feel stuck. */
const HOLD_MS = 450
/** A hold that wanders this far is a scroll, not a press. */
const MOVE_TOLERANCE_PX = 10

export interface LongPressHandlers {
  onPointerDown: (event: React.PointerEvent) => void
  onPointerMove: (event: React.PointerEvent) => void
  onPointerUp: () => void
  onPointerCancel: () => void
  onContextMenu: (event: React.MouseEvent) => void
}

/**
 * Tap and hold, for the menu a phone has no other way to reach.
 *
 * Only touch and pen fire it — a mouse has right-click, which is wired to the
 * same handler, and a long left-click on a desktop is usually a drag starting.
 * The press is abandoned if the finger travels, so holding still while the list
 * scrolls under it does not open a menu nobody asked for.
 */
export function useLongPress(onTrigger: (at: { x: number; y: number }) => void): LongPressHandlers {
  const timer = useRef<number | null>(null)
  const origin = useRef<{ x: number; y: number } | null>(null)

  const cancel = useCallback(() => {
    if (timer.current != null) window.clearTimeout(timer.current)
    timer.current = null
    origin.current = null
  }, [])

  return {
    onPointerDown: useCallback(
      (event: React.PointerEvent) => {
        if (event.pointerType === 'mouse') return
        origin.current = { x: event.clientX, y: event.clientY }
        const at = { x: event.clientX, y: event.clientY }
        timer.current = window.setTimeout(() => {
          timer.current = null
          onTrigger(at)
        }, HOLD_MS)
      },
      [onTrigger],
    ),
    onPointerMove: useCallback(
      (event: React.PointerEvent) => {
        const start = origin.current
        if (!start || timer.current == null) return
        const travelled = Math.hypot(event.clientX - start.x, event.clientY - start.y)
        if (travelled > MOVE_TOLERANCE_PX) cancel()
      },
      [cancel],
    ),
    onPointerUp: cancel,
    onPointerCancel: cancel,
    onContextMenu: useCallback(
      (event: React.MouseEvent) => {
        // Covers the right-click on a pointer device and suppresses the
        // platform menu that a long press would otherwise raise on touch.
        event.preventDefault()
        onTrigger({ x: event.clientX, y: event.clientY })
      },
      [onTrigger],
    ),
  }
}
