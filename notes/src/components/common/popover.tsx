'use client'

import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useDismiss } from '@/hooks/use-dismiss'
import { useIsPhone } from '@/hooks/use-media-query'
import { cn } from '@/lib/utils/cn'

interface PopoverProps {
  open: boolean
  onClose: () => void
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'end'
  /** Which way it opens. A trigger near the bottom edge has to open upward. */
  side?: 'bottom' | 'top'
  /** Render the anchored menu in a portal so it escapes overflow clipping. */
  portal?: boolean
  className?: string
}

const GAP = 6
const EDGE = 8

function menuClassName(
  side: 'bottom' | 'top',
  align: 'start' | 'end',
  className?: string,
  portalled = false,
) {
  return cn(
    'min-w-[13rem] rounded-lg border border-line bg-surface p-1 shadow-[var(--shadow-pop)] animate-pop',
    !portalled && 'z-50',
    !portalled && (side === 'top' ? 'bottom-[calc(100%+6px)]' : 'top-[calc(100%+6px)]'),
    !portalled && (align === 'end' ? 'right-0' : 'left-0'),
    className,
  )
}

/**
 * A menu anchored to its trigger on a pointer screen, and a sheet up from the
 * bottom edge on a phone.
 *
 * By default the anchored form is not portalled: those popovers are small and
 * sit next to what they belong to. Pass `portal` when an ancestor clips overflow
 * (e.g. a rounded list card). The sheet always portals — it must escape every
 * ancestor's clipping to cover the screen.
 */
export function Popover({
  open,
  onClose,
  trigger,
  children,
  align = 'start',
  side = 'bottom',
  portal = false,
  className,
}: PopoverProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const isPhone = useIsPhone()
  const usePortal = portal && !isPhone
  const [portalStyle, setPortalStyle] = useState<CSSProperties>({ visibility: 'hidden' })

  useDismiss(usePortal ? [rootRef, menuRef] : rootRef, open, onClose)

  const measurePortal = useCallback(() => {
    const trigger = rootRef.current
    const menu = menuRef.current
    if (!trigger || !menu) return

    const triggerRect = trigger.getBoundingClientRect()
    const menuRect = menu.getBoundingClientRect()
    const width = menuRect.width || menu.offsetWidth
    const height = menuRect.height || menu.offsetHeight

    let left = align === 'end' ? triggerRect.right - width : triggerRect.left
    let top = side === 'bottom' ? triggerRect.bottom + GAP : triggerRect.top - height - GAP

    left = Math.max(EDGE, Math.min(left, window.innerWidth - width - EDGE))
    top = Math.max(EDGE, Math.min(top, window.innerHeight - height - EDGE))

    setPortalStyle({ position: 'fixed', left, top, zIndex: 60, visibility: 'visible' })
  }, [align, side])

  const portalMenuRef = useCallback(
    (node: HTMLDivElement | null) => {
      menuRef.current = node
      if (node && open && usePortal) measurePortal()
    },
    [open, usePortal, measurePortal],
  )

  useEffect(() => {
    if (!open || !usePortal) {
      setPortalStyle({ visibility: 'hidden' })
      return
    }
    measurePortal()
    const close = () => onClose()
    window.addEventListener('resize', measurePortal)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('resize', measurePortal)
      window.removeEventListener('scroll', close, true)
    }
  }, [open, usePortal, measurePortal, onClose])

  const menuPanel = (
    <div
      ref={usePortal ? portalMenuRef : undefined}
      role="dialog"
      style={usePortal ? portalStyle : undefined}
      className={cn(
        usePortal ? 'fixed' : 'absolute',
        menuClassName(side, align, className, usePortal),
      )}
    >
      {children}
    </div>
  )

  return (
    <div ref={rootRef} className="relative">
      {trigger}

      {open &&
        (isPhone
          ? createPortal(
              <div className="fixed inset-0 z-[60] flex flex-col justify-end">
                <div
                  aria-hidden="true"
                  onClick={onClose}
                  className="absolute inset-0 bg-black/25 backdrop-blur-[2px] animate-fade-in"
                />
                <div
                  ref={menuRef}
                  role="dialog"
                  aria-modal="true"
                  className={cn(
                    'relative max-h-[80dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-surface',
                    'p-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-[var(--shadow-pop)]',
                    'animate-sheet',
                    className,
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="mx-auto mb-2 block h-1 w-9 rounded-full bg-line"
                  />
                  {children}
                </div>
              </div>,
              document.body,
            )
          : usePortal
            ? createPortal(menuPanel, document.body)
            : menuPanel)}
    </div>
  )
}

export function PopoverItem({
  children,
  onClick,
  selected,
  className,
}: {
  children: ReactNode
  onClick: () => void
  selected?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        // Taller on a phone, where this is a sheet row rather than a menu line.
        'flex w-full items-center gap-2 rounded-md px-2 py-2.5 text-left text-[14px] sm:py-1.5 sm:text-[13px]',
        'transition-colors duration-100 hover:bg-surface-hover',
        selected ? 'text-accent' : 'text-fg',
        className,
      )}
    >
      {children}
    </button>
  )
}
