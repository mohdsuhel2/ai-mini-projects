'use client'

import { useState } from 'react'
import { Check, MoreHorizontal, Trash2 } from 'lucide-react'
import { IconButton } from '@/components/common/icon-button'
import { Popover, PopoverItem } from '@/components/common/popover'
import { CategoryIcon } from '@/lib/icons'
import { formatClock, formatDuration, formatDurationLong } from '@/lib/date/format'
import { cn } from '@/lib/utils/cn'
import type { Activity, Category } from '@/types'

interface TimelineItemProps {
  activity: Activity
  category?: Category
  /** The thread stops at the last entry rather than trailing into nothing. */
  isLast?: boolean
  onDelete: () => void
}

export function TimelineItem({ activity, category, isLast, onDelete }: TimelineItemProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const fromTodo = activity.source === 'TODO_COMPLETION'
  const at = activity.startTime ?? activity.endTime

  return (
    <li className="group relative flex gap-3 pl-1">
      {/* The rail belongs to the entry, not to the list: each segment carries
          its own category tone, so the thread down the day is a picture of how
          the day was actually spent. Rendering it per row also means it always
          matches that row's real height. */}
      {!isLast && (
        <span
          aria-hidden="true"
          data-tone={category?.tone}
          className={cn(
            'absolute left-[13px] top-[6px] h-full w-[2px] rounded-full',
            category ? 'bg-[var(--tone-line)]' : 'bg-line',
          )}
        />
      )}

      {/* The marker sits on the thread, punched out of the background so the
          line appears to pass behind it rather than stopping at it. */}
      <div
        data-tone={category?.tone}
        className={cn(
          'relative z-10 mt-[3px] grid size-7 shrink-0 place-items-center rounded-lg border',
          category
            ? 'border-[var(--tone-line)] bg-[var(--tone-bg)] text-[var(--tone-fg)]'
            : 'border-line bg-surface text-fg-subtle',
        )}
      >
        {fromTodo && !category ? (
          <Check className="size-3.5 text-success" strokeWidth={2.5} aria-hidden="true" />
        ) : (
          <CategoryIcon icon={category?.icon} className="size-3.5" />
        )}
      </div>

      <div className="min-w-0 flex-1 pb-4">
        <div className="flex items-start gap-2">
          <p className="min-w-0 flex-1 text-[13.5px] font-[450] leading-[1.5] text-fg">
            {activity.title}
            {fromTodo && (
              <span className="ml-1.5 align-middle text-[11px] text-success" title="Completed task">
                ✓
              </span>
            )}
          </p>

          <div className="flex shrink-0 items-center gap-1">
            {activity.duration != null && (
              <span
                className="tnum text-[12px] font-medium text-fg-subtle"
                aria-label={formatDurationLong(activity.duration)}
              >
                {formatDuration(activity.duration)}
              </span>
            )}
            <div className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100">
              <Popover
                open={menuOpen}
                onClose={() => setMenuOpen(false)}
                align="end"
                trigger={
                  <IconButton
                    label={`Options for ${activity.title}`}
                    size="sm"
                    onClick={() => setMenuOpen((v) => !v)}
                  >
                    <MoreHorizontal className="size-3.5" strokeWidth={2} />
                  </IconButton>
                }
              >
                <PopoverItem
                  onClick={() => {
                    setMenuOpen(false)
                    onDelete()
                  }}
                  className="text-danger hover:bg-danger-soft"
                >
                  <Trash2 className="size-3.5" strokeWidth={2} />
                  {fromTodo ? 'Remove and reopen task' : 'Delete'}
                </PopoverItem>
              </Popover>
            </div>
          </div>
        </div>

        {at != null && (
          <p className="tnum mt-0.5 text-[11.5px] text-fg-faint">
            {formatClock(at)}
            {activity.endTime != null && activity.startTime != null && activity.duration != null
              ? ` – ${formatClock(activity.endTime)}`
              : ''}
          </p>
        )}
      </div>
    </li>
  )
}
