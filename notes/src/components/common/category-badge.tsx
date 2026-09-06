'use client'

import { cn } from '@/lib/utils/cn'
import type { Category } from '@/types'

interface CategoryBadgeProps {
  category: Category | undefined
  size?: 'sm' | 'md'
  className?: string
}

/**
 * A category, filled with its own colour and nothing else — no glyph, because
 * the name is already the shortest way to say it and a second mark beside it
 * only competes with the task's own title.
 *
 * Colour comes from the tone (a hue) via CSS custom properties, so light and
 * dark are computed from one rule rather than maintained as pairs; --tone-solid
 * is dark enough for --tone-solid-fg to clear AA on every hue in the set.
 */
export function CategoryBadge({ category, size = 'sm', className }: CategoryBadgeProps) {
  if (!category) return null

  return (
    <span
      data-tone={category.tone}
      className={cn(
        'inline-flex shrink-0 items-center rounded-md font-medium',
        'bg-[var(--tone-solid)] text-[var(--tone-solid-fg)]',
        size === 'sm' ? 'h-[19px] px-1.5 text-[11px]' : 'h-6 px-2 text-[12px]',
        className,
      )}
    >
      {category.name}
    </span>
  )
}

export function CategoryDot({ category, className }: { category?: Category; className?: string }) {
  if (!category) return null
  return (
    <span
      data-tone={category.tone}
      aria-hidden="true"
      className={cn('size-1.5 shrink-0 rounded-full bg-[var(--tone-dot)]', className)}
    />
  )
}
