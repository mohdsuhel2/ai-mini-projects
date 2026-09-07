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
  /** The day's longest entry — what the bar under the title is drawn against. */
  longest: number
  /** Tapped: this entry is lit on the day bar above. */
  selected: boolean
  onSelect: () => void
  onDelete: () => void
}

/**
 * One row of the day, built as a grouped-list row rather than a block on a
 * timeline: a filled tile, the thing you did, and what it cost, right aligned.
 *
 * Every row is the same height. The previous version scaled height by duration,
 * which turned a day of mixed entries into a ragged column — the three-pixel
 * bar under the title carries that proportion instead, for a fraction of the
 * visual weight.
 */
export function TimelineItem({
  activity,
  category,
  at,
  end,
  minutes,
  longest,
  selected,
  onSelect,
  onDelete,
}: TimelineItemProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const fromTodo = activity.source === 'TODO_COMPLETION'
  const range =
    at != null && end != null ? `${formatClock(at)} – ${formatClock(end)}` : formatClock(at)
  const share = longest > 0 ? minutes / longest : 0

  return (
    <li className="group relative flex items-center gap-1">
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        aria-label={`Show ${activity.title} on the day bar`}
        className={cn(
          'flex min-w-0 flex-1 items-center gap-3 rounded-xl px-3.5 py-3 text-left',
          'transition-colors duration-150',
          selected ? 'bg-bg-sunk' : 'hover:bg-surface-hover',
        )}
      >
      {/* Saturated fill, white glyph — the tile is the row's colour, so nothing
          else in the row has to carry the category. */}
      <span
        data-tone={category?.tone}
        aria-hidden="true"
        className={cn(
          'grid size-9 shrink-0 place-items-center rounded-[10px]',
          category
            ? 'bg-[var(--tone-solid)] text-[var(--tone-solid-fg)]'
            : 'bg-bg-sunk text-fg-subtle',
        )}
      >
        {fromTodo && !category ? (
          <Check className="size-[18px]" strokeWidth={2.6} />
        ) : (
          <CategoryIcon icon={category?.icon} className="size-[18px]" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-3">
          <p className="min-w-0 flex-1 truncate text-[15px] font-medium leading-tight text-fg">
            {activity.title}
            {fromTodo && (
              <span className="ml-1.5 align-middle text-[11px] text-success" title="Completed task">
                ✓
              </span>
            )}
          </p>
          {activity.duration != null && (
            <span
              className="tnum shrink-0 text-[13px] font-semibold text-fg"
              aria-label={formatDurationLong(activity.duration)}
            >
              {formatDuration(activity.duration)}
            </span>
          )}
        </div>

        <p className="tnum mt-0.5 truncate text-[12.5px] leading-tight text-fg-faint">
          {range || 'No time recorded'}
          {category && ` · ${category.name}`}
        </p>

        {/* No track behind it: an empty grey rail on every row reads as a rule
            across the list, which is the thing the list is trying not to have.
            The filled length alone carries the proportion. */}
        {share > 0 && (
          <div
            data-tone={category?.tone}
            aria-hidden="true"
            // A floor, so the shortest entry of the day is still a mark.
            style={{ width: `${Math.max(share * 100, 3)}%` }}
            className={cn(
              'mt-2 h-[3px] rounded-full transition-[width] duration-500 ease-[var(--ease-out-soft)]',
              category ? 'bg-[var(--tone-solid)]' : 'bg-fg-faint',
            )}
          />
        )}
      </div>
      </button>

      <div className="shrink-0 pr-1.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100">
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
              <MoreHorizontal className="size-4" strokeWidth={2} />
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
    </li>
  )
}
