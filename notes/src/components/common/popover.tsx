'use client'

import { useRef, type ReactNode } from 'react'
import { useDismiss } from '@/hooks/use-dismiss'
import { cn } from '@/lib/utils/cn'

interface PopoverProps {
  open: boolean
  onClose: () => void
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'end'
  className?: string
}

/**
 * Anchored to its trigger rather than portalled: these popovers are small and
 * always sit next to what they belong to, so the extra machinery of a portal
 * would buy nothing but a positioning bug.
 */
export function Popover({ open, onClose, trigger, children, align = 'start', className }: PopoverProps) {
  const ref = useRef<HTMLDivElement>(null)
  useDismiss(ref, open, onClose)

  return (
    <div ref={ref} className="relative">
      {trigger}
      {open && (
        <div
          role="dialog"
          className={cn(
            'absolute top-[calc(100%+6px)] z-50 min-w-[13rem] rounded-lg border border-line',
            'bg-surface p-1 shadow-[var(--shadow-pop)] animate-pop',
            align === 'end' ? 'right-0' : 'left-0',
            className,
          )}
        >
          {children}
        </div>
      )}
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
        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]',
        'transition-colors duration-100 hover:bg-surface-hover',
        selected ? 'text-accent' : 'text-fg',
        className,
      )}
    >
      {children}
    </button>
  )
}
