'use client'

import { useState } from 'react'
import { Clock, MoreHorizontal, Pencil, Play, Trash2 } from 'lucide-react'
import { CategoryBadge } from '@/components/common/category-badge'
import { Checkbox } from '@/components/common/checkbox'
import { IconButton } from '@/components/common/icon-button'
import { Popover, PopoverItem } from '@/components/common/popover'
import { CompleteMenu } from './complete-menu'
import { formatClock, formatDayLabel, formatDuration } from '@/lib/date/format'
import { todayKey } from '@/lib/date/day-key'
import { cn } from '@/lib/utils/cn'
import type { Category, Todo } from '@/types'

interface TodoRowProps {
  todo: Todo
  category?: Category
  /** True when the row's own date is already implied by its group heading. */
  hideDate?: boolean
  onComplete: (duration: number | null) => void
  onReopen: () => void
  onEdit: () => void
  onDelete: () => void
  onStartTimer: () => void
}

export function TodoRow({
  todo,
  category,
  hideDate,
  onComplete,
  onReopen,
  onEdit,
  onDelete,
  onStartTimer,
}: TodoRowProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [completeOpen, setCompleteOpen] = useState(false)
  const done = todo.status === 'COMPLETED'
  const overdue = !done && todo.plannedDate != null && todo.plannedDate < todayKey()

  const dateLabel = !hideDate && todo.plannedDate ? formatDayLabel(todo.plannedDate) : null

  const meta = [
    dateLabel && overdue ? `Due ${dateLabel}` : dateLabel,
    todo.plannedTime != null ? formatClock(todo.plannedTime) : null,
    formatDuration(todo.actualDuration ?? todo.estimatedDuration) || null,
  ].filter(Boolean) as string[]

  return (
    <div
      className={cn(
        'group relative flex items-center gap-2.5 px-3.5 py-2.5',
      )}
    >
      <div className="shrink-0">
        {done ? (
          <Checkbox checked onChange={onReopen} label={`Reopen ${todo.title}`} />
        ) : (
          <CompleteMenu
            open={completeOpen}
            onOpenChange={setCompleteOpen}
            estimated={todo.estimatedDuration ?? null}
            onComplete={(duration) => {
              setCompleteOpen(false)
              onComplete(duration)
            }}
            trigger={
              <Checkbox
                checked={false}
                onChange={() => onComplete(null)}
                label={`Complete ${todo.title}`}
              />
            }
          />
        )}
      </div>

      <button
        type="button"
        onClick={onEdit}
        className="min-w-0 flex-1 text-left"
        aria-label={`Edit ${todo.title}`}
      >
        <p
          className={cn(
            'text-[14px] font-[450] leading-[1.35] transition-[color,opacity] duration-300',
            done ? 'text-fg-subtle line-through decoration-fg-faint' : 'text-fg',
          )}
        >
          {todo.title}
        </p>

        {(category || meta.length > 0) && (
          <div className="mt-[3px] flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] font-medium leading-[19px]">
            <CategoryBadge category={category} />
            {meta.length > 0 && (
              <span className={cn(overdue ? 'text-danger' : 'text-fg-subtle')}>
                {meta.join(' · ')}
              </span>
            )}
          </div>
        )}
      </button>

      <div
        className={cn(
          'flex items-center gap-0.5 opacity-0 transition-opacity duration-150',
          'group-hover:opacity-100 focus-within:opacity-100',
        )}
      >
        {!done && (
          <IconButton label={`Start a timer for ${todo.title}`} size="sm" onClick={onStartTimer}>
            <Play className="size-3.5" strokeWidth={2} />
          </IconButton>
        )}
        <Popover
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          align="end"
          trigger={
            <IconButton
              label={`Options for ${todo.title}`}
              size="sm"
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
            >
              <MoreHorizontal className="size-4" strokeWidth={2} />
            </IconButton>
          }
        >
          <PopoverItem
            onClick={() => {
              setMenuOpen(false)
              onEdit()
            }}
          >
            <Pencil className="size-3.5 text-fg-subtle" strokeWidth={2} />
            Edit details
          </PopoverItem>
          {!done && (
            <PopoverItem
              onClick={() => {
                setMenuOpen(false)
                setCompleteOpen(true)
              }}
            >
              <Clock className="size-3.5 text-fg-subtle" strokeWidth={2} />
              Complete with duration
            </PopoverItem>
          )}
          <div className="my-1 h-px bg-line" />
          <PopoverItem
            onClick={() => {
              setMenuOpen(false)
              onDelete()
            }}
            className="text-danger hover:bg-danger-soft"
          >
            <Trash2 className="size-3.5" strokeWidth={2} />
            Delete
          </PopoverItem>
        </Popover>
      </div>
    </div>
  )
}
