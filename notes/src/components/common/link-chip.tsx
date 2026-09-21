'use client'

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils/cn'

export function LinkChip({
  children,
  onClick,
  label,
  className,
}: {
  children: ReactNode
  onClick: () => void
  label: string
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      aria-label={label}
      className={cn(
        'inline-flex cursor-pointer items-center gap-1 rounded-full px-1.5 py-0.5',
        'text-[11px] font-medium leading-none text-fg-subtle',
        'transition-colors hover:bg-accent-soft/60 hover:text-accent',
        className,
      )}
    >
      {children}
    </button>
  )
}
