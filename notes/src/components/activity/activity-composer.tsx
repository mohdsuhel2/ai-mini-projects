'use client'

import { useRef, useState, type FormEvent } from 'react'
import { Check } from 'lucide-react'
import { CategorySelect } from '@/components/common/category-select'
import { DurationChips } from '@/components/common/duration-chips'
import { createActivity, lastEndOfDay, suggestedStartTime } from '@/features/activities/api'
import { useActivitiesForDay } from '@/hooks/use-data'
import { formatClock } from '@/lib/date/format'
import { minutesOfDay } from '@/lib/date/day-key'
import { parseNaturalInput } from '@/lib/parse/natural-input'
import { useDismiss } from '@/hooks/use-dismiss'
import { track } from '@/lib/analytics'
import { cn } from '@/lib/utils/cn'
import type { DayKey, Id } from '@/types'

interface ActivityComposerProps {
  day: DayKey
}

/**
 * Logging what already happened has to be faster than the thing being logged.
 * One line, a duration if you have it, Enter. "Watched YouTube for 1 hour"
 * fills in the duration on its own.
 */
function toTimeInput(minute: number): string {
  return `${String(Math.floor(minute / 60) % 24).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`
}

function fromTimeInput(value: string): number | null {
  if (!value) return null
  const [h, m] = value.split(':').map(Number)
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : null
}

export function ActivityComposer({ day }: ActivityComposerProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const formRef = useRef<HTMLFormElement>(null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [duration, setDuration] = useState<number | null>(null)
  const [categoryId, setCategoryId] = useState<Id | null>(null)
  const [durationTouched, setDurationTouched] = useState(false)
  const [startTime, setStartTime] = useState<number | null>(null)
  const [startTouched, setStartTouched] = useState(false)
  const dayActivities = useActivitiesForDay(day) ?? []

  const parsed = parseNaturalInput(title)
  const missingCategory = categoryId === null
  const effectiveDuration = durationTouched ? duration : (parsed.durationMinutes ?? duration)
  useDismiss(formRef, panelOpen, () => setPanelOpen(false))

  const expanded = panelOpen || title.length > 0

  // Once a length is known, the entry needs a place on the day. It defaults to
  // where the previous one finished, so logging a run of activities is just
  // duration, duration, duration.
  const previousEnd = lastEndOfDay(dayActivities)
  const autoStart =
    effectiveDuration != null
      ? suggestedStartTime(dayActivities, effectiveDuration, minutesOfDay(new Date()))
      : null
  const effectiveStart = startTouched ? startTime : (parsed.minuteOfDay ?? autoStart)
  const endsAt =
    effectiveStart != null && effectiveDuration != null ? effectiveStart + effectiveDuration : null

  async function submit(event: FormEvent) {
    event.preventDefault()
    const finalTitle = parsed.title.trim() || title.trim()
    if (!finalTitle || !categoryId) return

    await createActivity({
      title: finalTitle,
      date: day,
      categoryId,
      duration: effectiveDuration,
      startTime: effectiveStart,
    })
    track('activity_added', {
      has_duration: effectiveDuration != null,
      has_category: Boolean(categoryId),
      parsed_duration: parsed.durationMinutes != null,
    })

    setTitle('')
    setDuration(null)
    setDurationTouched(false)
    setStartTime(null)
    setStartTouched(false)
    setCategoryId(null)
    inputRef.current?.focus()
  }

  return (
    <form
      ref={formRef}
      onSubmit={submit}
      className={cn(
        'rounded-xl border bg-surface transition-[border-color] duration-200',
        expanded ? 'border-line-strong' : 'border-line',
      )}
    >
      <div className="flex items-center gap-2.5 px-3.5 py-3">
        <Check
          className={cn(
            'size-4 shrink-0 transition-colors duration-200',
            expanded ? 'text-success' : 'text-fg-faint',
          )}
          strokeWidth={2.2}
          aria-hidden="true"
        />
        <input
          ref={inputRef}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onFocus={() => setPanelOpen(true)}
          placeholder="What did you do?"
          aria-label="Log an activity"
          enterKeyHint="done"
          className="min-w-0 flex-1 text-[14px] text-fg outline-none"
        />
        {title.trim().length > 0 && (
          <button
            type="submit"
            disabled={missingCategory}
            title={missingCategory ? 'Pick a category first' : undefined}
            className="inline-flex h-6 items-center rounded-md bg-accent px-2 text-[11.5px] font-medium text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-40 animate-fade-in"
          >
            Log
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2.5 border-t border-line px-3.5 py-2.5 animate-fade-in">
          <DurationChips
            value={effectiveDuration}
            onChange={(next) => {
              setDuration(next)
              setDurationTouched(true)
            }}
          />

          {effectiveDuration != null && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 animate-fade-in">
              <label
                htmlFor="activity-start"
                className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint"
              >
                Started at
              </label>
              <input
                id="activity-start"
                type="time"
                value={effectiveStart != null ? toTimeInput(effectiveStart) : ''}
                onChange={(event) => {
                  setStartTime(fromTimeInput(event.target.value))
                  setStartTouched(true)
                }}
                className="tnum rounded-md border border-line bg-bg px-2 py-1 text-[12.5px] text-fg outline-none transition-colors focus:border-line-strong"
              />
              {endsAt != null && (
                <span className="text-[11.5px] text-fg-faint">
                  ends {formatClock(Math.min(endsAt, 24 * 60 - 1))}
                </span>
              )}
              {!startTouched && previousEnd != null && effectiveStart === previousEnd && (
                <span className="text-[11.5px] text-accent">picks up from your last entry</span>
              )}
            </div>
          )}

          <div className="space-y-1.5">
            <span className="block text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              Category {missingCategory && <span className="text-accent">· required</span>}
            </span>
            <CategorySelect value={categoryId} onChange={setCategoryId} scope="activity" />
          </div>
        </div>
      )}
    </form>
  )
}
