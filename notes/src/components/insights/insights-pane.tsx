'use client'

import { useState } from 'react'
import { SegmentedControl } from '@/components/common/segmented-control'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { useRangeSummary } from '@/hooks/use-data'
import { formatDayLabel, formatDuration } from '@/lib/date/format'
import { fromDayKey } from '@/lib/date/day-key'
import type { RangeId } from '@/features/analytics/range'
import { cn } from '@/lib/utils/cn'

const WEEKDAY = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

/**
 * The day surface answers "where did today go". This one answers the question
 * you can only ask after a few of them: is the shape of my week what I thought
 * it was. Everything here is the same tracked minutes seen from further back —
 * no new data, only a longer lens.
 */
export function InsightsPane() {
  const [range, setRange] = useState<RangeId>('week')
  const summary = useRangeSummary(range)

  return (
    <div className="mx-auto max-w-[860px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">Insights</h2>
          <p className="mt-1 text-[13px] text-fg-muted">
            {range === 'week' ? 'The last seven days' : 'The last thirty days'}, as they were logged.
          </p>
        </div>

        <SegmentedControl<RangeId>
          aria-label="Range"
          value={range}
          onChange={setRange}
          options={[
            { value: 'week', label: 'Week' },
            { value: 'month', label: 'Month' },
          ]}
        />
      </div>

      {!summary ? (
        <PaneSkeleton rows={4} />
      ) : summary.totalMinutes === 0 ? (
        <EmptyState
          glyph="day"
          title="Nothing tracked in this window."
          hint="Log a few activities and the shape of your week shows up here."
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Tracked" value={formatDuration(summary.totalMinutes)} />
            <Stat label="Daily average" value={formatDuration(summary.dailyAverage) || '0m'} />
            <Stat
              label="Days logged"
              value={`${summary.activeDays} of ${summary.days.length}`}
            />
          </div>

          <section className="rounded-2xl border border-card-line bg-surface p-5">
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-fg">Daily total</h3>
              {summary.busiest && (
                <p className="text-[12px] text-fg-faint">
                  Busiest: {formatDayLabel(summary.busiest.day)} ·{' '}
                  {formatDuration(summary.busiest.minutes)}
                </p>
              )}
            </div>

            {/* Columns, not a line: each day is a discrete amount that was or
                was not logged, and a line between them would imply a reading
                for the hours in between. */}
            <ol className="mt-4 flex h-[150px] items-end gap-1.5">
              {summary.days.map((bar) => (
                <li key={bar.day} className="flex h-full flex-1 flex-col justify-end">
                  <div
                    title={`${formatDayLabel(bar.day)} · ${formatDuration(bar.minutes) || 'nothing tracked'}`}
                    // A day with nothing on it keeps a hairline rather than
                    // vanishing: an empty Thursday is a reading, not a gap.
                    style={
                      bar.minutes === 0
                        ? { height: '3px' }
                        : { height: `${Math.max(bar.height * 100, 2)}%` }
                    }
                    className={cn(
                      'mx-auto w-full max-w-[54px] rounded-md transition-[height] duration-500 ease-[var(--ease-out-soft)]',
                      bar.minutes === 0
                        ? 'bg-line'
                        : bar.isToday
                          ? 'bg-accent'
                          : 'bg-accent/40',
                    )}
                  />
                </li>
              ))}
            </ol>

            <ol className="mt-2 flex gap-1.5" aria-hidden="true">
              {summary.days.map((bar) => (
                <li
                  key={bar.day}
                  className={cn(
                    'flex-1 text-center text-[10px] tabular-nums',
                    bar.isToday ? 'font-semibold text-accent' : 'text-fg-faint',
                  )}
                >
                  {summary.days.length > 10
                    ? fromDayKey(bar.day).getDate() % 5 === 0
                      ? fromDayKey(bar.day).getDate()
                      : ''
                    : WEEKDAY[fromDayKey(bar.day).getDay()]}
                </li>
              ))}
            </ol>
          </section>

          <section className="rounded-2xl border border-card-line bg-surface p-5">
            <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-fg">Where it went</h3>
            <ul className="mt-4 space-y-3.5">
              {summary.byCategory.map((total) => (
                <li key={total.categoryId ?? 'none'} data-tone={total.tone}>
                  <div className="flex items-baseline justify-between gap-3 text-[13px]">
                    <span className="font-medium text-fg">{total.name}</span>
                    <span className="tnum shrink-0 text-fg-muted">
                      {formatDuration(total.minutes)}
                      <span className="ml-1.5 text-fg-faint">
                        {Math.round(total.share * 100)}%
                      </span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line">
                    <div
                      style={{ width: `${total.share * 100}%` }}
                      className="h-full rounded-full bg-[var(--tone-solid)] transition-[width] duration-500 ease-[var(--ease-out-soft)]"
                    />
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-card-line bg-surface p-[18px]">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">{label}</p>
      <p className="tnum mt-1.5 text-[22px] font-semibold tracking-[-0.02em] text-fg">{value}</p>
    </div>
  )
}
