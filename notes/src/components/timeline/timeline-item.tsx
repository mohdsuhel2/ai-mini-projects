'use client'

import { useState } from 'react'
import { Check, MoreHorizontal, Trash2 } from 'lucide-react'
import { IconButton } from '@/components/common/icon-button'
import { Popover, PopoverItem } from '@/components/common/popover'
import { CategoryIcon } from '@/lib/icons'
import { formatClock, formatDuration, formatDurationLong } from '@/lib/date/format'
import { cn } from '@/lib/utils/cn'
import type { Activity, Category, MinuteOfDay } from '@/types'

interface TimelineItemProps {
  activity: Activity
  category?: Category
  /** Minutes past midnight, or null for an entry logged without a clock time. */
  at: MinuteOfDay | null
  end: MinuteOfDay | null
  minutes: number
  onDelete: () => void
}

/**
 * How tall a block of `minutes` is drawn.
 *
 * Proportional, then capped. Below the floor an entry would be too small to
 * read or to hit; above the ceiling a four-hour block would push the rest of
 * the day off the screen to make a point the duration already makes in words.
 */
export function blockHeight(minutes: number): number {
  if (minutes <= 0) return 46
  return Math.min(164, Math.max(46, Math.round(minutes * 1.15)))
}

export function TimelineItem({
  activity,
  category,
  at,
  end,
  minutes,
  onDelete,
}: TimelineItemProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const fromTodo = activity.source === 'TODO_COMPLETION'
  const range =
    at != null && end != null ? `${formatClock(at)} – ${formatClock(end)}` : formatClock(at)

  return (
    <li className="group flex gap-2.5">
      {/* Clock time lives in the gutter, once, so the block itself is free to
          carry the thing that was actually done. */}
      <span className="tnum w-[52px] shrink-0 pt-3 text-right text-[11px] leading-none text-fg-faint">
        {at != null ? formatClock(at) : ''}
      </span>

      <div
        data-tone={category?.tone}
        title={`${activity.title}${range ? ` · ${range}` : ''}${category ? ` · ${category.name}` : ''}`}
        style={{ minHeight: blockHeight(minutes) }}
        className={cn(
          'relative flex min-w-0 flex-1 gap-2.5 overflow-hidden rounded-2xl py-3 pl-4 pr-2.5',
          category ? 'bg-[var(--tone-wash)]' : 'bg-bg-sunk',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'absolute inset-y-2.5 left-2 w-[3px] rounded-full',
            category ? 'bg-[var(--tone-solid)]' : 'bg-line-strong',
          )}
        />

        <span
          aria-hidden="true"
          className={cn(
            'mt-px shrink-0',
            category ? 'text-[var(--tone-fg)]' : 'text-fg-subtle',
          )}
        >
          {fromTodo && !category ? (
            <Check className="size-4 text-success" strokeWidth={2.5} />
          ) : (
            <CategoryIcon icon={category?.icon} className="size-4" />
          )}
        </span>

        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium leading-[1.35] text-fg">
            {activity.title}
            {fromTodo && (
              <span className="ml-1.5 align-middle text-[11px] text-success" title="Completed task">
                ✓
              </span>
            )}
          </p>
          {category && (
            <p className="mt-0.5 text-[11.5px] font-medium text-[var(--tone-fg)]">
              {category.name}
            </p>
          )}
          {at == null && (
            <p className="mt-0.5 text-[11.5px] text-fg-faint">No time recorded</p>
          )}
        </div>

        <div className="flex shrink-0 items-start gap-0.5">
          {activity.duration != null && (
            <span
              className="tnum pt-0.5 text-[12px] font-semibold text-fg-muted"
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
    </li>
  )
}
