'use client'

import { useState } from 'react'
import { Check, Tag } from 'lucide-react'
import { Chip } from '@/components/common/chip'
import { Popover, PopoverItem } from '@/components/common/popover'
import { categoriesForScope } from '@/features/categories/api'
import { useCategories } from '@/hooks/use-data'
import { CategoryIcon } from '@/lib/icons'
import { cn } from '@/lib/utils/cn'
import type { Id } from '@/types'

interface CategoryPickerProps {
  value: Id | null
  onChange: (id: Id | null) => void
  scope: 'todo' | 'activity'
  align?: 'start' | 'end'
  className?: string
}

export function CategoryPicker({
  value,
  onChange,
  scope,
  align = 'start',
  className,
}: CategoryPickerProps) {
  const [open, setOpen] = useState(false)
  const all = useCategories() ?? []
  const categories = categoriesForScope(all, scope)
  const selected = categories.find((c) => c.id === value)

  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      align={align}
      className="max-h-64 overflow-y-auto"
      trigger={
        <Chip
          active={Boolean(selected)}
          onClick={() => setOpen((v) => !v)}
          aria-haspopup="dialog"
          aria-expanded={open}
          className={className}
          {...(selected ? { 'data-tone': selected.tone } : {})}
        >
          {selected ? (
            <>
              <CategoryIcon icon={selected.icon} className="size-3.5" />
              {selected.name}
            </>
          ) : (
            <>
              <Tag className="size-3.5" strokeWidth={2} aria-hidden="true" />
              Category
            </>
          )}
        </Chip>
      }
    >
      <PopoverItem
        onClick={() => {
          onChange(null)
          setOpen(false)
        }}
        selected={value === null}
      >
        <span className="grid size-4 place-items-center">
          {value === null && <Check className="size-3.5" strokeWidth={2.5} />}
        </span>
        <span className="text-fg-muted">No category</span>
      </PopoverItem>

      <div className="my-1 h-px bg-line" />

      {categories.map((category) => {
        const active = category.id === value
        return (
          <PopoverItem
            key={category.id}
            selected={active}
            onClick={() => {
              onChange(active ? null : category.id)
              setOpen(false)
            }}
          >
            <span className="grid size-4 place-items-center">
              {active && <Check className="size-3.5" strokeWidth={2.5} />}
            </span>
            <span
              data-tone={category.tone}
              className={cn(
                'grid size-5 place-items-center rounded-sm',
                'bg-[var(--tone-bg)] text-[var(--tone-fg)]',
              )}
            >
              <CategoryIcon icon={category.icon} className="size-3" strokeWidth={2.2} />
            </span>
            {category.name}
          </PopoverItem>
        )
      })}
    </Popover>
  )
}
