'use client'

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils/cn'

interface EmptyStateProps {
  title: string
  hint?: string
  glyph?: 'plan' | 'day'
  className?: string
  children?: ReactNode
}

/**
 * Abstract line marks rather than an illustration — an empty list should feel
 * like a clean surface waiting for something, not like a missing image.
 */
function Glyph({ variant }: { variant: 'plan' | 'day' }) {
  return (
    <svg
      viewBox="0 0 64 44"
      fill="none"
      aria-hidden="true"
      className="mb-4 h-11 w-16 text-fg-faint"
    >
      <defs>
        <linearGradient id={`sn-empty-${variant}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.85" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0.12" />
        </linearGradient>
      </defs>
      {variant === 'plan' ? (
        <g stroke={`url(#sn-empty-plan)`} strokeWidth="1.5" strokeLinecap="round">
          <circle cx="8" cy="10" r="4.25" />
          <path d="M19 10h34M19 22h27M19 34h20" />
          <circle cx="8" cy="22" r="4.25" />
          <circle cx="8" cy="34" r="4.25" />
        </g>
      ) : (
        <g stroke={`url(#sn-empty-day)`} strokeWidth="1.5" strokeLinecap="round">
          <path d="M8 3v38" />
          <circle cx="8" cy="11" r="2.75" fill="var(--bg)" />
          <circle cx="8" cy="24" r="2.75" fill="var(--bg)" />
          <circle cx="8" cy="37" r="2.75" fill="var(--bg)" />
          <path d="M18 11h30M18 24h22M18 37h34" />
        </g>
      )}
    </svg>
  )
}

export function EmptyState({ title, hint, glyph = 'plan', className, children }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 py-12 text-center animate-fade-in',
        className,
      )}
    >
      <Glyph variant={glyph} />
      <p className="text-[14px] font-medium text-fg">{title}</p>
      {hint && <p className="mt-1 max-w-[28ch] text-[13px] leading-relaxed text-fg-subtle">{hint}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  )
}
