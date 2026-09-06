'use client'

import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Check } from 'lucide-react'
import {
  blockAt,
  MINUTES_IN_DAY,
  type DayBlock,
  type DaySchedule,
} from '@/features/analytics/summary'
import { formatClock, formatDuration } from '@/lib/date/format'
import { minutesOfDay } from '@/lib/date/day-key'
import { Skeleton } from '@/components/common/skeleton'
import { useNow } from '@/hooks/use-now'
import type { DailySummary as Summary } from '@/types'

interface DailySummaryProps {
  summary: Summary | undefined
  schedule: DaySchedule | undefined
  isToday: boolean
}

/** Quarter-day marks, so the bar reads as a day rather than as a ratio. */
const HOUR_LABELS = [
  { hour: 0, label: '12 AM' },
  { hour: 6, label: '6 AM' },
  { hour: 12, label: '12 PM' },
  { hour: 18, label: '6 PM' },
  { hour: 24, label: '12 AM' },
]

const percent = (minutes: number) => `${(minutes / MINUTES_IN_DAY) * 100}%`

/**
 * A sentence and one bar spanning the whole 24 hours, with each entry drawn at
 * the hour it happened. An hour of YouTube from 1 PM colours 1 PM to 2 PM —
 * a bar packed against the left edge would only say how much, and "where did
 * my day go?" is a question about *when*.
 */
export function DailySummary({ summary, schedule, isToday }: DailySummaryProps) {
  // Ticking only on today, and coarsely: the marker moves a pixel a minute.
  const now = useNow(30_000, isToday)
  const barRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ x: number; block: DayBlock } | null>(null)

  if (!summary || !schedule) {
    return (
      <div className="space-y-3 rounded-2xl border border-card-line bg-surface p-4">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-2.5 w-full rounded-full" />
      </div>
    )
  }

  const { completedCount, trackedMinutes } = summary
  const { blocks, unplacedMinutes, untrackedMinutes } = schedule
  const nowMinutes = isToday ? minutesOfDay(new Date(now)) : null

  // Split so the number carries the weight and the label stays quiet — this
  // line is the answer to "where did my day go", not a status message.
  const parts: Array<{ value: string; label: string }> = []
  if (completedCount > 0) {
    parts.push({
      value: String(completedCount),
      label: completedCount === 1 ? 'task done' : 'tasks done',
    })
  }
  if (trackedMinutes > 0) parts.push({ value: formatDuration(trackedMinutes), label: 'tracked' })

  function trackPointer(event: ReactPointerEvent<HTMLDivElement>) {
    const bar = barRef.current
    if (!bar) return
    const rect = bar.getBoundingClientRect()
    if (rect.width === 0) return
    const x = Math.min(Math.max(event.clientX - rect.left, 0), rect.width)
    // Only entries speak. Empty time is already named once above the bar, and
    // a tooltip that follows the cursor across three hours of nothing is noise.
    const block = blockAt(blocks, (x / rect.width) * MINUTES_IN_DAY)
    setHover(block ? { x, block } : null)
  }

  return (
    <div className="rounded-2xl border border-card-line bg-surface p-[18px]">
      {parts.length > 0 ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-success/12 text-success">
            <Check className="size-3.5" strokeWidth={2.6} aria-hidden="true" />
          </span>
          {parts.map((part, index) => (
            <span key={part.label} className="inline-flex items-baseline gap-1.5">
              {index > 0 && <span className="mr-0.5 text-fg-faint">·</span>}
              <span className="tnum text-[15px] font-semibold tracking-[-0.01em] text-fg">
                {part.value}
              </span>
              <span className="text-fg-muted">{part.label}</span>
            </span>
          ))}
          <span className="ml-auto text-[11.5px] text-fg-faint">
            {formatDuration(untrackedMinutes)} unaccounted
          </span>
        </div>
      ) : (
        <p className="text-[13px] text-fg-subtle">
          {isToday ? 'Nothing tracked yet today.' : 'Nothing was tracked this day.'}
        </p>
      )}

      <div
        className="relative -my-2 mt-1 py-2"
        onPointerMove={trackPointer}
        onPointerLeave={() => setHover(null)}
      >
        {hover && (
          <div
            aria-hidden="true"
            style={{ left: hover.x }}
            className="pointer-events-none absolute bottom-[calc(100%-2px)] z-20 -translate-x-1/2"
          >
            <div className="w-max max-w-[15rem] rounded-lg bg-fg px-2.5 py-1.5 text-bg shadow-[var(--shadow-pop)]">
              <p className="truncate text-[12px] font-semibold leading-tight">
                {hover.block.title}
              </p>
              <p className="tnum mt-0.5 text-[11px] leading-tight opacity-70">
                {formatClock(hover.block.start)} – {formatClock(hover.block.end)} · {hover.block.name}
              </p>
            </div>
          </div>
        )}

      <div
        ref={barRef}
        className="relative h-2.5 w-full overflow-hidden rounded-full bg-line"
        role="img"
        aria-label={
          blocks.length > 0
            ? `Across 24 hours: ${blocks
                .map(
                  (b) => `${b.name}, ${b.title}, ${formatClock(b.start)} to ${formatClock(b.end)}`,
                )
                .join('; ')}. ${formatDuration(untrackedMinutes)} unaccounted`
            : 'Nothing tracked out of 24 hours'
        }
      >
        {blocks.map((block) => (
          <span
            key={block.id}
            data-tone={block.tone}
            style={{ left: percent(block.start), width: percent(block.minutes) }}
            // A ten-minute entry is a third of a percent of the day; without a
            // floor it would round away to nothing at all.
            className="absolute inset-y-0 min-w-[3px] rounded-full bg-[var(--tone-solid)] transition-[left,width] duration-500 ease-[var(--ease-out-soft)]"
          />
        ))}

        {nowMinutes != null && (
          <span
            aria-hidden="true"
            style={{ left: percent(nowMinutes) }}
            className="absolute inset-y-[-2px] -ml-px w-0.5 rounded-full bg-fg/70"
          />
        )}
      </div>
      </div>

      <div className="mt-1.5 flex items-center justify-between text-[10px] tabular-nums text-fg-faint">
        {HOUR_LABELS.map(({ hour, label }) => (
          <span key={hour}>{label}</span>
        ))}
      </div>

      {unplacedMinutes > 0 && (
        <p className="mt-2 text-[11px] text-fg-faint">
          {formatDuration(unplacedMinutes)} logged without a time, so it has no place on the bar.
        </p>
      )}

      {summary.byCategory.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
          {summary.byCategory.slice(0, 5).map((total) => (
            <li
              key={total.categoryId ?? 'none'}
              data-tone={total.tone}
              className="inline-flex items-center gap-1.5 text-[11.5px] font-medium text-fg-subtle"
            >
              <span aria-hidden="true" className="size-2 rounded-full bg-[var(--tone-solid)]" />
              {total.name}
              <span className="tnum font-medium text-fg-faint">
                {formatDuration(total.minutes)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
