'use client'

import { useRef, type ReactNode } from 'react'
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
  className?: string
}

/**
 * A menu anchored to its trigger on a pointer screen, and a sheet up from the
 * bottom edge on a phone.
 *
 * The anchored form is not portalled: those popovers are small and always sit
 * next to what they belong to, so a portal would buy nothing but a positioning
 * bug. The sheet is the opposite case — it must escape every ancestor's
 * clipping and stacking to cover the screen, so it goes to the body.
 */
export function Popover({
  open,
  onClose,
  trigger,
  children,
  align = 'start',
  side = 'bottom',
  className,
}: PopoverProps) {
  const ref = useRef<HTMLDivElement>(null)
  const sheetRef = useRef<HTMLDivElement>(null)
  const isPhone = useIsPhone()
  useDismiss([ref, sheetRef], open, onClose)

  return (
    <div ref={ref} className="relative">
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
                  ref={sheetRef}
                  role="dialog"
                  aria-modal="true"
                  className={cn(
                    'relative max-h-[80dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-surface',
                    'p-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-[var(--shadow-pop)]',
                    'animate-sheet',
                    className,
                  )}
                >
                  {/* The handle is the affordance that says this came up from
                      the edge and can go back down to it. */}
                  <span
                    aria-hidden="true"
                    className="mx-auto mb-2 block h-1 w-9 rounded-full bg-line"
                  />
                  {children}
                </div>
              </div>,
              document.body,
            )
          : (
              <div
                role="dialog"
                className={cn(
                  'absolute z-50 min-w-[13rem] rounded-lg border border-line',
                  'bg-surface p-1 shadow-[var(--shadow-pop)] animate-pop',
                  side === 'top' ? 'bottom-[calc(100%+6px)]' : 'top-[calc(100%+6px)]',
                  align === 'end' ? 'right-0' : 'left-0',
                  className,
                )}
              >
                {children}
              </div>
            ))}
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
