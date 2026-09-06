'use client'

import { cn } from '@/lib/utils/cn'

interface SegmentedControlProps<T extends string> {
  value: T
  onChange: (value: T) => void
  options: Array<{ value: T; label: string; count?: number }>
  className?: string
  'aria-label': string
}

export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  className,
  'aria-label': ariaLabel,
}: SegmentedControlProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        'inline-flex items-center gap-0.5 rounded-lg bg-bg-sunk p-1',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex h-8 items-center gap-1.5 rounded-md px-4 text-[13px] font-medium',
              'transition-colors duration-150',
              active
                ? 'bg-surface text-accent'
                : 'text-fg-muted hover:text-fg',
            )}
          >
            {option.label}
            {option.count != null && option.count > 0 && (
              <span className={cn('tnum text-[11px]', active ? 'text-fg-subtle' : 'text-fg-faint')}>
                {option.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
