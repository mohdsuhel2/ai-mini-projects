'use client'

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useDismiss } from '@/hooks/use-dismiss'
import { cn } from '@/lib/utils/cn'

export interface ContextMenuState {
  x: number
  y: number
}

interface ContextMenuProps {
  state: ContextMenuState | null
  onClose: () => void
  children: ReactNode
  label: string
}

const EDGE_PADDING = 8

/**
 * A right-click menu anchored to the pointer. Fixed rather than absolute so it
 * escapes the tree's scroll container, and nudged back inside the viewport when
 * the click lands near an edge.
 */
export function ContextMenu({ state, onClose, children, label }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<ContextMenuState | null>(null)

  useDismiss(ref, state !== null, onClose)

  /**
   * Measured through a callback ref rather than an effect: the size is only
   * knowable once the menu is in the DOM, and this runs during commit, so the
   * clamped position paints without an intermediate frame at the raw point.
   * The callback's identity changes with `state`, so a second right-click while
   * the menu is open re-measures rather than keeping the stale position.
   */
  const measure = useCallback(
    (node: HTMLDivElement | null) => {
      ref.current = node
      if (!node || !state) return
      const { width, height } = node.getBoundingClientRect()
      setPosition({
        x: Math.max(EDGE_PADDING, Math.min(state.x, window.innerWidth - width - EDGE_PADDING)),
        y: Math.max(EDGE_PADDING, Math.min(state.y, window.innerHeight - height - EDGE_PADDING)),
      })
    },
    [state],
  )

  useEffect(() => {
    if (!state) return
    // A menu pinned to a point on screen is wrong the moment the page moves.
    const close = () => onClose()
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [state, onClose])

  if (!state) return null

  return (
    <div
      ref={measure}
      role="menu"
      aria-label={label}
      style={{ left: position?.x ?? state.x, top: position?.y ?? state.y }}
      className={cn(
        'fixed z-[70] min-w-[12rem] rounded-lg border border-line bg-surface p-1',
        'shadow-[var(--shadow-pop)] animate-pop',
      )}
    >
      {children}
    </div>
  )
}

export function ContextMenuItem({
  children,
  onClick,
  danger,
}: {
  children: ReactNode
  onClick: () => void
  danger?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]',
        'transition-colors duration-100',
        danger ? 'text-danger hover:bg-danger-soft' : 'text-fg hover:bg-surface-hover',
      )}
    >
      {children}
    </button>
  )
}

export function ContextMenuSeparator() {
  return <div className="my-1 h-px bg-line" role="separator" />
}
