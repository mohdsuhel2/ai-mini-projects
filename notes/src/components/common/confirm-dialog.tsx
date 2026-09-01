'use client'

import { useEffect, useRef } from 'react'
import { Dialog } from './dialog'
import { Button } from './button'

interface ConfirmDialogProps {
  open: boolean
  title: string
  body: string
  confirmLabel?: string
  onConfirm: () => void
  onClose: () => void
}

/**
 * Used only where the action is not covered by Undo, or where the blast radius
 * is wider than the thing the user clicked — deleting a folder takes its whole
 * subtree with it, and that deserves a sentence before it happens.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Delete',
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    // Focus the destructive button so Enter confirms and Escape cancels, but
    // only after the dialog is actually showing.
    const id = window.requestAnimationFrame(() => confirmRef.current?.focus())
    return () => window.cancelAnimationFrame(id)
  }, [open])

  return (
    <Dialog open={open} onClose={onClose} title={title} className="sm:w-[min(100vw-1.5rem,24rem)]">
      <div className="px-5 py-4">
        <p className="text-[13.5px] leading-relaxed text-fg-muted">{body}</p>
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          ref={confirmRef}
          variant="danger"
          size="sm"
          onClick={() => {
            onConfirm()
            onClose()
          }}
        >
          {confirmLabel}
        </Button>
      </footer>
    </Dialog>
  )
}
