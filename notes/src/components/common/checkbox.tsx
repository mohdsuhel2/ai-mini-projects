'use client'

import { cn } from '@/lib/utils/cn'

interface CheckboxProps {
  checked: boolean
  onChange: () => void
  label: string
  className?: string
}

/**
 * The tick is drawn rather than swapped in, so completing a task reads as one
 * continuous gesture instead of an icon appearing out of nowhere.
 */
export function Checkbox({ checked, onChange, label, className }: CheckboxProps) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      className={cn(
        'group relative grid size-[18px] shrink-0 place-items-center rounded-full border',
        'transition-[background-color,border-color,transform] duration-200 ease-[var(--ease-out-soft)]',
        'active:scale-90',
        checked
          ? 'border-accent bg-accent'
          : 'border-control-line bg-transparent hover:border-accent hover:bg-accent-soft',
        className,
      )}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
        className={cn(
          'size-[11px] transition-opacity duration-150',
          checked ? 'opacity-100' : 'opacity-0',
        )}
      >
        <path
          d="M4 12.5 9.5 18 20 6.5"
          stroke="var(--accent-fg)"
          strokeWidth="3.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength={22}
          strokeDasharray={22}
          className={checked ? 'animate-[sn-check_260ms_var(--ease-out-soft)_forwards]' : ''}
          style={checked ? undefined : { strokeDashoffset: 22 }}
        />
      </svg>
    </button>
  )
}
