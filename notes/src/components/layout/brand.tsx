import { cn } from '@/lib/utils/cn'

/**
 * Three strokes settling into one — a day's loose ends resolving. Drawn rather
 * than lettered so it holds up at 20px in a browser tab.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={cn('size-5', className)}>
      <rect x="1" y="1" width="22" height="22" rx="6.5" fill="var(--accent)" />
      <path
        d="M7 8.5h10M7 12h7M7 15.5h4"
        stroke="var(--accent-fg)"
        strokeWidth="1.9"
        strokeLinecap="round"
        opacity="0.9"
      />
      <path
        d="m13.6 15.9 2 2 4-4.6"
        stroke="var(--accent-fg)"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
