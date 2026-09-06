'use client'

import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils/cn'
import { IconButton } from './icon-button'

interface DialogProps {
  open: boolean
  onClose: () => void
  title: string
  /** Hides the title visually while keeping it for screen readers. */
  hideTitle?: boolean
  description?: string
  children: ReactNode
  /** On narrow screens the dialog becomes a bottom sheet. */
  variant?: 'centered' | 'command'
  className?: string
}

/**
 * Built on the native <dialog> element: focus trapping, Escape handling, the
 * top layer and inertness of the page behind all come from the platform rather
 * than from several hundred lines of our own.
 *
 * On a phone it rises from the bottom edge as a sheet instead of landing in the
 * middle of the screen — that is where a thumb is, and where the platform's own
 * modals come from.
 */
export function Dialog({
  open,
  onClose,
  title,
  hideTitle,
  description,
  children,
  variant = 'centered',
  className,
}: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (open && !node.open) node.showModal()
    if (!open && node.open) node.close()
  }, [open])

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const onCancel = (event: Event) => {
      event.preventDefault()
      onClose()
    }
    node.addEventListener('cancel', onCancel)
    return () => node.removeEventListener('cancel', onCancel)
  }, [onClose])

  return (
    <dialog
      ref={ref}
      aria-label={title}
      onClick={(event) => {
        // The backdrop is part of the dialog element, so a click that lands on
        // the element itself rather than its content is a click outside.
        if (event.target === ref.current) onClose()
      }}
      className={cn(
        'max-w-none bg-transparent p-0 text-fg backdrop:bg-black/25',
        'backdrop:backdrop-blur-[2px] open:animate-fade-in',
        // Phone: full width, flush to the bottom edge. `mt-auto` against the
        // element's own centring margins is what pins it there.
        'max-sm:m-0 max-sm:mt-auto max-sm:w-full',
        'sm:w-[min(100vw-1.5rem,34rem)]',
        variant === 'command' ? 'sm:mx-auto sm:mb-auto sm:mt-[14vh]' : 'sm:m-auto',
        className,
      )}
    >
      <div
        className={cn(
          'flex flex-col overflow-hidden border-line bg-surface shadow-[var(--shadow-pop)]',
          'max-sm:max-h-[88dvh] max-sm:rounded-t-2xl max-sm:border-t max-sm:animate-sheet',
          'max-sm:pb-[env(safe-area-inset-bottom)]',
          'sm:max-h-[80vh] sm:rounded-xl sm:border',
        )}
      >
        {/* The handle is the affordance that says this came up from the edge
            and can go back down to it. */}
        <span
          aria-hidden="true"
          className="mx-auto mt-2.5 block h-1 w-9 shrink-0 rounded-full bg-line sm:hidden"
        />

        {!hideTitle && (
          <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4 max-sm:pt-3">
            <div className="min-w-0">
              <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
              {description && <p className="mt-0.5 text-[13px] text-fg-muted">{description}</p>}
            </div>
            <IconButton label="Close" size="sm" onClick={onClose} className="-mr-1 -mt-0.5">
              <X className="size-4" strokeWidth={2} />
            </IconButton>
          </header>
        )}
        {children}
      </div>
    </dialog>
  )
}
