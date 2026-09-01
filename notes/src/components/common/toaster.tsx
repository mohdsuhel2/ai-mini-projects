'use client'

import { useUi } from '@/store/ui-context'

/**
 * One quiet line at the bottom of the screen. It exists mainly to carry Undo,
 * which is what makes a destructive click safe to make without a dialog.
 */
export function Toaster() {
  const { toasts, dismissToast } = useUi()
  if (toasts.length === 0) return null

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] z-[60] flex flex-col items-center gap-2 px-4 sm:bottom-6"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className="pointer-events-auto flex items-center gap-3 rounded-lg border border-line bg-surface py-2 pl-3.5 pr-2 text-[13px] shadow-[var(--shadow-pop)] animate-rise"
        >
          <span className="text-fg">{toast.message}</span>
          {toast.action && (
            <button
              type="button"
              onClick={() => {
                toast.action?.onClick()
                dismissToast(toast.id)
              }}
              className="rounded-md px-2 py-1 text-[13px] font-medium text-accent transition-colors hover:bg-accent-soft"
            >
              {toast.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  )
}
