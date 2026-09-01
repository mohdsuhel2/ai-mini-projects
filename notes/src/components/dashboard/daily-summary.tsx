'use client'

import { dayFill, MINUTES_IN_DAY } from '@/features/analytics/summary'
import { formatDuration } from '@/lib/date/format'
import { Skeleton } from '@/components/common/skeleton'
import { cn } from '@/lib/utils/cn'
import type { DailySummary as Summary } from '@/types'

interface DailySummaryProps {
  summary: Summary | undefined
  isToday: boolean
}

/** Every sixth hour, so the bar reads as a day rather than as a ratio. */
const HOUR_TICKS = [6, 12, 18]

/**
 * A sentence and one bar spanning the whole 24 hours. Scaling to tracked time
 * instead would make forty logged minutes look like a full day, which is the
 * opposite of what "where did my day go?" is asking.
 */
export function DailySummary({ summary, isToday }: DailySummaryProps) {
  if (!summary) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-2.5 w-full rounded-full" />
      </div>
    )
  }

  const { completedCount, trackedMinutes } = summary
  const fill = dayFill(summary)

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

  return (
    <div className="space-y-2.5">
      {parts.length > 0 ? (
        <p className="flex flex-wrap items-baseline gap-x-1.5 text-[13px]">
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
            {formatDuration(fill.untrackedMinutes)} unaccounted
          </span>
        </p>
      ) : (
        <p className="text-[13px] text-fg-subtle">
          {isToday ? 'Nothing tracked yet today.' : 'Nothing was tracked this day.'}
        </p>
      )}

      <div
        className="relative h-2.5 w-full overflow-hidden rounded-full bg-line"
        role="img"
        aria-label={
          fill.segments.length > 0
            ? `Of 24 hours: ${fill.segments
                .map((s) => `${s.name} ${formatDuration(s.minutes)}`)
                .join(', ')}, ${formatDuration(fill.untrackedMinutes)} unaccounted`
            : 'Nothing tracked out of 24 hours'
        }
      >
        <div className="flex h-full w-full">
          {fill.segments.map((segment) => (
            <span
              key={segment.categoryId ?? 'none'}
              data-tone={segment.tone}
              title={`${segment.name} · ${formatDuration(segment.minutes)}`}
              style={{ width: `${segment.share * 100}%` }}
              className="h-full bg-[var(--tone-solid)] transition-[width] duration-500 ease-[var(--ease-out-soft)]"
            />
          ))}
        </div>

        {/* Hour marks sit above the fill so the scale stays legible either way. */}
        {HOUR_TICKS.map((hour) => (
          <span
            key={hour}
            aria-hidden="true"
            style={{ left: `${((hour * 60) / MINUTES_IN_DAY) * 100}%` }}
            className="absolute inset-y-0 w-px bg-bg/45"
          />
        ))}
      </div>

      <div className="flex items-center justify-between text-[10px] tabular-nums text-fg-faint">
        <span>12 AM</span>
        <span>6</span>
        <span>12 PM</span>
        <span>6</span>
        <span>12 AM</span>
      </div>

      {fill.segments.length > 0 && (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 pt-0.5">
          {fill.segments.slice(0, 5).map((segment) => (
            <li
              key={segment.categoryId ?? 'none'}
              data-tone={segment.tone}
              className="inline-flex items-center gap-1.5 text-[11.5px] font-medium text-fg-subtle"
            >
              <span
                aria-hidden="true"
                className={cn('size-2 rounded-[3px] bg-[var(--tone-solid)]')}
              />
              {segment.name}
              <span className="tnum font-medium text-fg-faint">
                {formatDuration(segment.minutes)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
