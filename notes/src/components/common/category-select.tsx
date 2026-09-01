'use client'

import { categoriesForScope } from '@/features/categories/api'
import { useCategories } from '@/hooks/use-data'
import { CategoryIcon } from '@/lib/icons'
import { cn } from '@/lib/utils/cn'
import type { Id } from '@/types'

interface CategorySelectProps {
  value: Id | null
  onChange: (id: Id) => void
  scope: 'todo' | 'activity'
  className?: string
}

/**
 * Every category on the surface as a filled chip, rather than hidden behind a
 * popover. Choosing one is required, so the choice has to be visible and
 * one tap away — a required field behind a menu is a required field people
 * abandon.
 *
 * The row scrolls horizontally and its edges fade, so a long category set does
 * not force the composer to grow.
 */
export function CategorySelect({ value, onChange, scope, className }: CategorySelectProps) {
  const all = useCategories()
  const categories = categoriesForScope(all ?? [], scope)

  return (
    <div
      role="radiogroup"
      aria-label="Category"
      aria-required="true"
      className={cn('no-scrollbar edge-fade-x -mx-0.5 flex gap-1.5 overflow-x-auto px-0.5', className)}
    >
      {categories.map((category) => {
        const selected = category.id === value
        return (
          <button
            key={category.id}
            type="button"
            role="radio"
            aria-checked={selected}
            data-tone={category.tone}
            onClick={() => onChange(category.id)}
            className={cn(
              'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2.5',
              'text-[12.5px] font-medium transition-[background-color,border-color,color] duration-150',
              selected
                ? 'border-transparent bg-[var(--tone-solid)] text-[var(--tone-solid-fg)]'
                : 'border-line bg-transparent text-fg-muted hover:border-[var(--tone-line)] hover:bg-[var(--tone-bg)] hover:text-[var(--tone-fg)]',
            )}
          >
            <CategoryIcon icon={category.icon} className="size-3.5" strokeWidth={2.2} />
            {category.name}
          </button>
        )
      })}
    </div>
  )
}
