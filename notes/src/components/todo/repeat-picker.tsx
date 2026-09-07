'use client'

import { useState } from 'react'
import { Check, Repeat } from 'lucide-react'
import { Chip } from '@/components/common/chip'
import { Popover, PopoverItem } from '@/components/common/popover'
import {
  describeRecurrence,
  shortRecurrence,
  weekdayOf,
  weekdaysOf,
  WEEKDAY_INITIALS,
  WEEKDAY_NAMES,
} from '@/features/todos/recurrence'
import { todayKey } from '@/lib/date/day-key'
import { cn } from '@/lib/utils/cn'
import type { DayKey, Recurrence, Weekday } from '@/types'

interface RepeatPickerProps {
  value: Recurrence | null
  onChange: (recurrence: Recurrence | null) => void
  /** The task's own date, so "every week" already knows which day it means. */
  anchor: DayKey | null
  className?: string
}

/**
 * Three choices in one list, and a day strip that only appears once it can
 * change anything. Weekly starts on the task's own day, so the common case is
 * a single tap; the strip is there to add days or move it, not to make you
 * state something you already said by scheduling it.
 */
export function RepeatPicker({ value, onChange, anchor, className }: RepeatPickerProps) {
  const [open, setOpen] = useState(false)
  const anchorWeekday = weekdayOf(anchor ?? todayKey())
  const selected = value?.kind === 'weekly' ? weekdaysOf(value) : []

  function choose(next: Recurrence | null) {
    onChange(next)
    setOpen(false)
  }

  /**
   * Toggling leaves the menu open — picking three days should not cost three
   * trips. Turning off the last day turns the repeat off rather than leaving a
   * weekly schedule with no day it could ever fall on.
   */
  function toggleDay(day: Weekday) {
    const next = selected.includes(day)
      ? selected.filter((d) => d !== day)
      : [...selected, day]
    onChange(next.length === 0 ? null : { kind: 'weekly', weekdays: next })
  }

  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      align="start"
      className="sm:min-w-[15rem]"
      trigger={
        <Chip
          active={value != null}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className={className}
        >
          <Repeat className="size-3.5" strokeWidth={2} aria-hidden="true" />
          {value ? shortRecurrence(value) : 'Repeat'}
        </Chip>
      }
    >
      <Option label="Never" selected={value == null} onClick={() => choose(null)} />
      <Option
        label="Every day"
        selected={value?.kind === 'daily'}
        onClick={() => choose({ kind: 'daily' })}
      />
      {/* A fixed word, not a description of the current pick. The row is the
          way in to choosing days; the strip and the caption below say what was
          chosen, and a label that renamed itself as you tapped would make the
          option you are reaching for move. */}
      <Option
        label="Custom"
        selected={value?.kind === 'weekly'}
        onClick={() =>
          onChange({
            kind: 'weekly',
            weekdays: selected.length > 0 ? selected : [anchorWeekday],
          })
        }
      />

      {value?.kind === 'weekly' && (
        <div className="mt-1 border-t border-line px-1 pb-0.5 pt-2 animate-fade-in">
          <div aria-label="Which days" className="flex gap-1">
            {WEEKDAY_INITIALS.map((initial, index) => {
              const day = index as Weekday
              const on = selected.includes(day)
              return (
                <button
                  key={WEEKDAY_NAMES[day]}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  aria-label={WEEKDAY_NAMES[day]}
                  onClick={() => toggleDay(day)}
                  className={cn(
                    'grid h-8 flex-1 place-items-center rounded-md text-[12px] font-semibold',
                    'transition-colors duration-150',
                    on ? 'bg-accent text-accent-fg' : 'bg-bg-sunk text-fg-muted hover:text-fg',
                  )}
                >
                  {initial}
                </button>
              )
            })}
          </div>
          <p className="px-1 pb-0.5 pt-2 text-[11px] text-fg-faint">
            {describeRecurrence(value)}
          </p>
        </div>
      )}
    </Popover>
  )
}

function Option({
  label,
  selected,
  onClick,
}: {
  label: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <PopoverItem onClick={onClick} selected={selected}>
      <Check
        className={cn('size-3.5 shrink-0', selected ? 'opacity-100' : 'opacity-0')}
        strokeWidth={2.4}
        aria-hidden="true"
      />
      {label}
    </PopoverItem>
  )
}
