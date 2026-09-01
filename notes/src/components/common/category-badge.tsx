'use client'

import { CategoryIcon } from '@/lib/icons'
import { cn } from '@/lib/utils/cn'
import type { Category } from '@/types'

interface CategoryBadgeProps {
  category: Category | undefined
  size?: 'sm' | 'md'
  className?: string
}

/**
 * Colour comes from the category's tone (a hue) via CSS custom properties, so
 * light and dark are computed from one rule rather than maintained as pairs.
 */
export function CategoryBadge({ category, size = 'sm', className }: CategoryBadgeProps) {
  if (!category) return null

  return (
    <span
      data-tone={category.tone}
      className={cn(
        'inline-flex items-center gap-1 rounded-sm font-medium',
        'bg-[var(--tone-bg)] text-[var(--tone-fg)]',
        size === 'sm' ? 'h-[19px] px-1.5 text-[11px]' : 'h-6 px-2 text-xs',
        className,
      )}
    >
      <CategoryIcon icon={category.icon} className={size === 'sm' ? 'size-3' : 'size-3.5'} />
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
