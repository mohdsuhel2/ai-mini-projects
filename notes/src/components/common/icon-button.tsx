'use client'

import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils/cn'

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: these buttons never carry visible text. */
  label: string
  size?: 'sm' | 'md'
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, label, size = 'md', type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-xl text-fg-muted',
        'transition-colors duration-150 hover:bg-surface-hover hover:text-fg active:bg-surface-active',
        'disabled:pointer-events-none disabled:opacity-40',
        size === 'sm' ? 'size-8' : 'size-9',
        className,
      )}
      {...props}
    />
  )
})
