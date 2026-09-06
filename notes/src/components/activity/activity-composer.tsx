'use client'

import { useRef, useState, type FormEvent } from 'react'
import { Play, Plus, Timer } from 'lucide-react'
import { CategorySelect } from '@/components/common/category-select'
import { DurationChips } from '@/components/common/duration-chips'
import { SegmentedControl } from '@/components/common/segmented-control'
import { createActivity, lastEndOfDay, suggestedStartTime } from '@/features/activities/api'
import { startTimer } from '@/features/timer/api'
import { useActivitiesForDay, useTimer } from '@/hooks/use-data'
import { formatClock } from '@/lib/date/format'
import { minutesOfDay, todayKey } from '@/lib/date/day-key'
import { parseNaturalInput } from '@/lib/parse/natural-input'
import { useDismiss } from '@/hooks/use-dismiss'
import { track } from '@/lib/analytics'
import { cn } from '@/lib/utils/cn'
import type { DayKey, Id } from '@/types'

interface ActivityComposerProps {
  day: DayKey
}

/**
 * Two things can be true when you open this box: it already happened, or it is
 * about to. `done` records a length you tell it; `starting` hands the entry to
 * the running timer and measures one instead, so the time comes from what
 * actually elapsed rather than from what you remembered afterwards.
 */
type LogMode = 'done' | 'starting'

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
  const [mode, setMode] = useState<LogMode>('done')
  const [duration, setDuration] = useState<number | null>(null)
  const [categoryId, setCategoryId] = useState<Id | null>(null)
  const [durationTouched, setDurationTouched] = useState(false)
  const [startTime, setStartTime] = useState<number | null>(null)
  const [startTouched, setStartTouched] = useState(false)
  const dayActivities = useActivitiesForDay(day) ?? []
  const timer = useTimer()

  const parsed = parseNaturalInput(title)
  const missingCategory = categoryId === null
  const effectiveDuration = durationTouched ? duration : (parsed.durationMinutes ?? duration)
  useDismiss(formRef, panelOpen, () => setPanelOpen(false))

  const expanded = panelOpen || title.length > 0
  // Nothing can be started on a day that has already been and gone, so the
  // choice only appears where it means something.
  const canStart = day === todayKey()
  const starting = mode === 'starting' && canStart
  // One timer at a time: a second would have to either steal the first one's
  // elapsed time or discard it, and neither is a thing to do quietly.
  const timerBusy = timer != null
  const blocked = missingCategory || (starting && timerBusy)

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

  function reset() {
    setTitle('')
    setDuration(null)
    setDurationTouched(false)
    setStartTime(null)
    setStartTouched(false)
    setCategoryId(null)
    inputRef.current?.focus()
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const finalTitle = parsed.title.trim() || title.trim()
    if (!finalTitle || !categoryId || blocked) return

    if (starting) {
      // No activity is written here. The timer owns the entry until it is
      // stopped, and `completeTimer` records it with the measured span — one
      // row, written once, rather than a placeholder patched up later.
      await startTimer({ title: finalTitle, categoryId })
      track('timer_started', { from_todo: false })
      reset()
      return
    }

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
    reset()
  }

  return (
    <form
      ref={formRef}
      onSubmit={submit}
      className={cn(
        'rounded-2xl border bg-surface transition-[border-color] duration-200',
        expanded ? 'border-line-strong' : 'border-card-line',
      )}
    >
      <div className="flex items-center gap-3 px-3 py-3">
        <span
          aria-hidden="true"
          className={cn(
            'grid size-9 shrink-0 place-items-center rounded-full transition-colors duration-200',
            expanded ? 'bg-accent text-accent-fg' : 'bg-accent-soft text-accent',
          )}
        >
          {starting ? (
            <Play className="size-4" strokeWidth={2.6} />
          ) : (
            <Plus className="size-[18px]" strokeWidth={2.4} />
          )}
        </span>
        <input
          ref={inputRef}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onFocus={() => setPanelOpen(true)}
          placeholder={starting ? 'What are you starting?' : 'What did you do?'}
          aria-label={starting ? 'Start an activity' : 'Log an activity'}
          enterKeyHint="done"
          className="min-w-0 flex-1 text-[14.5px] text-fg outline-none placeholder:text-fg-faint"
        />
        {title.length === 0 && (
          <Timer
            className="mr-1.5 size-[18px] shrink-0 text-fg-faint"
            strokeWidth={2}
            aria-hidden="true"
          />
        )}
        {title.trim().length > 0 && (
          <button
            type="submit"
            disabled={blocked}
            title={
              missingCategory
                ? 'Pick a category first'
                : starting && timerBusy
                  ? 'Something else is already running'
                  : undefined
            }
            className="mr-1 inline-flex h-7 items-center rounded-full bg-accent px-3 text-[12px] font-medium text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-40 animate-fade-in"
          >
            {starting ? 'Start' : 'Log'}
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2.5 border-t border-card-line px-4 py-3.5 animate-fade-in">
          {canStart && (
            <SegmentedControl<LogMode>
              aria-label="When this happened"
              value={mode}
              onChange={setMode}
              className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
              options={[
                { value: 'done', label: 'I already did this' },
                { value: 'starting', label: "I'm starting now" },
              ]}
            />
          )}

          {starting ? (
            <p className="text-[12px] leading-[1.5]">
              {timer ? (
                <span className="text-danger">
                  “{timer.title}” is still running. Stop that one first.
                </span>
              ) : (
                <span className="text-fg-muted">
                  The clock starts when you do. Stop it when you are finished and the time is
                  recorded for you.
                </span>
              )}
            </p>
          ) : (
            <>
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
            </>
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
