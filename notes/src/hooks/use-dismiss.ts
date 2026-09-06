'use client'

import { useEffect, type RefObject } from 'react'

/**
 * Closes a popover on outside pointer-down or Escape.
 *
 * Takes more than one ref because a sheet portalled to the body is not inside
 * its trigger's subtree: measured against the trigger alone, the first tap on
 * the sheet would read as a tap outside and close it.
 */
export function useDismiss(
  ref: RefObject<HTMLElement | null> | Array<RefObject<HTMLElement | null>>,
  open: boolean,
  onClose: () => void,
): void {
  useEffect(() => {
    if (!open) return
    const refs = Array.isArray(ref) ? ref : [ref]

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      const nodes = refs.map((r) => r.current).filter((n): n is HTMLElement => n != null)
      if (nodes.length > 0 && !nodes.some((node) => node.contains(target))) onClose()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }

    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [ref, open, onClose])
}
