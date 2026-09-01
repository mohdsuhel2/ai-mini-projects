'use client'

import { useRef } from 'react'
import { CalendarDays } from 'lucide-react'
import { Chip } from '@/components/common/chip'
import { shiftDay, todayKey, weekendKey } from '@/lib/date/day-key'
import { formatDayLabel } from '@/lib/date/format'
import { cn } from '@/lib/utils/cn'
import type { DayKey } from '@/types'

interface DateChipsProps {
  value: DayKey | null
  onChange: (day: DayKey | null) => void
  /** Offers "Someday" — a task worth keeping with no date on it. */
  allowNone?: boolean
  className?: string
}

/**
 * Three chips cover almost every scheduling decision. The full picker is the
 * browser's own, opened from the fourth chip, so it never occupies the surface
 * until someone actually needs a calendar.
 */
export function DateChips({ value, onChange, allowNone, className }: DateChipsProps) {
  const dateInput = useRef<HTMLInputElement>(null)
  const today = todayKey()
  const tomorrow = shiftDay(today, 1)
  const weekend = weekendKey()

  const presets: Array<{ label: string; day: DayKey }> = [
    { label: 'Today', day: today },
    { label: 'Tomorrow', day: tomorrow },
  ]
  // The weekend chip is noise on a Saturday, when it just means "today".
  if (weekend !== today && weekend !== tomorrow) {
    presets.push({ label: 'Weekend', day: weekend })
  }

  const isCustom = value != null && !presets.some((preset) => preset.day === value)

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {presets.map((preset) => (
        <Chip key={preset.label} active={value === preset.day} onClick={() => onChange(preset.day)}>
          {preset.label}
        </Chip>
      ))}

      {allowNone && (
        <Chip active={value === null} onClick={() => onChange(null)}>
          Someday
        </Chip>
      )}

      <div className="relative">
        <Chip
          active={isCustom}
          onClick={() => {
            const input = dateInput.current
            if (!input) return
            // showPicker opens the native calendar without the design having
            // to render a date field. Older engines fall back to a plain click.
            try {
              input.showPicker()
            } catch {
              input.click()
            }
          }}
        >
          <CalendarDays className="size-3.5" strokeWidth={2} aria-hidden="true" />
          {isCustom && value ? formatDayLabel(value) : 'Pick date'}
        </Chip>
        <input
          ref={dateInput}
          type="date"
          value={value ?? ''}
          aria-label="Pick a date"
          onChange={(event) => event.target.value && onChange(event.target.value)}
          className="pointer-events-none absolute inset-0 size-full opacity-0"
          tabIndex={-1}
        />
      </div>
    </div>
  )
}
