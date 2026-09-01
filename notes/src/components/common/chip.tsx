'use client'

import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils/cn'

export interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean
}

/** The small selectable pill used for dates, durations and categories. */
export const Chip = forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { className, active, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-pressed={active}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] font-medium',
        'transition-[background-color,border-color,color] duration-150',
        active
          ? 'border-accent-line bg-accent-soft text-accent'
          : 'border-line bg-transparent text-fg-muted hover:border-line-strong hover:text-fg',
        className,
      )}
      {...props}
    />
  )
})
