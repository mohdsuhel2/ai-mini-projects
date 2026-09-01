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
        'w-[min(100vw-1.5rem,34rem)] max-w-none bg-transparent p-0 text-fg backdrop:bg-black/25',
        'backdrop:backdrop-blur-[2px] open:animate-fade-in',
        variant === 'command'
          ? 'mx-auto mt-[8vh] mb-auto sm:mt-[14vh]'
          : 'm-auto',
        className,
      )}
    >
      <div
        className={cn(
          'flex max-h-[80vh] flex-col overflow-hidden rounded-xl border border-line bg-surface',
          'shadow-[var(--shadow-pop)]',
        )}
      >
        {!hideTitle && (
          <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
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
