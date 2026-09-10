'use client'

import { useState } from 'react'
import { SegmentedControl } from '@/components/common/segmented-control'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { useDeepInsights, useRangeSummary } from '@/hooks/use-data'
import { formatDayLabel, formatDuration, formatHourLabel } from '@/lib/date/format'
import { fromDayKey } from '@/lib/date/day-key'
import type { RangeId } from '@/features/analytics/range'
import { cn } from '@/lib/utils/cn'

const WEEKDAY = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

/** The hour with the most time in it, phrased for the panel's corner. */
function busiestHour(hours: { hour: number; minutes: number }[]): string | undefined {
  const peak = hours.reduce((best, band) => (band.minutes > best.minutes ? band : best), hours[0])
  if (!peak || peak.minutes === 0) return undefined
  return `busiest around ${formatHourLabel(peak.hour)}`
}

/**
 * The day surface answers "where did today go". This one answers the question
 * you can only ask after a few of them: is the shape of my week what I thought
 * it was. Everything here is the same tracked minutes seen from further back —
 * no new data, only a longer lens.
 */
export function InsightsPane() {
  const [range, setRange] = useState<RangeId>('week')
  const summary = useRangeSummary(range)
  const deep = useDeepInsights(range)

  return (
    <div className="mx-auto max-w-[860px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">Insights</h2>
          <p className="mt-1 text-[13px] text-fg-muted">
            {range === 'week' ? 'The last seven days' : 'The last thirty days'}, as they were logged.
          </p>
        </div>

        <SegmentedControl<RangeId>
          aria-label="Range"
          value={range}
          onChange={setRange}
          className="flex w-full [&>button]:flex-1 [&>button]:justify-center sm:w-auto sm:[&>button]:flex-none"
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
          <div className="grid grid-cols-2 gap-2.5 sm:gap-3 lg:grid-cols-4">
            <Stat label="Tracked" value={formatDuration(summary.totalMinutes)} />
            <Stat label="Daily average" value={formatDuration(summary.dailyAverage) || '0m'} />
            <Stat
              label="Days logged"
              value={`${summary.activeDays} of ${summary.days.length}`}
            />
            <Stat
              label="Streak"
              value={deep ? `${deep.streak.current}d` : '—'}
              detail={
                deep && deep.streak.longest > deep.streak.current
                  ? `best ${deep.streak.longest}d`
                  : undefined
              }
            />
          </div>

          <section className="rounded-2xl border border-card-line bg-surface p-4 sm:p-5">
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

          {deep && (
            <Panel
              title="When you work"
              note={busiestHour(deep.hours)}
            >
              {/* Each entry counts toward every hour it covered, so a long
                  block shows as a band rather than a spike at its start. */}
              <ol className="mt-4 flex h-[92px] items-end gap-[2px]">
                {deep.hours.map((band) => (
                  <li key={band.hour} className="flex h-full flex-1 flex-col justify-end">
                    <div
                      title={`${formatHourLabel(band.hour)} · ${formatDuration(band.minutes) || 'nothing'}`}
                      style={{ height: band.minutes === 0 ? '2px' : `${Math.max(band.share * 100, 4)}%` }}
                      className={cn(
                        'w-full rounded-[3px]',
                        band.minutes === 0 ? 'bg-line' : 'bg-accent/45',
                      )}
                    />
                  </li>
                ))}
              </ol>
              <ol className="mt-1.5 flex gap-[2px]" aria-hidden="true">
                {deep.hours.map((band) => (
                  <li
                    key={band.hour}
                    className="flex-1 text-center text-[9px] tabular-nums text-fg-faint"
                  >
                    {band.hour % 6 === 0 ? band.hour : ''}
                  </li>
                ))}
              </ol>
            </Panel>
          )}

          {deep && (
            <Panel title="Your week" note="average per day">
              <ul className="mt-4 space-y-2.5">
                {deep.weekdays.map((day) => {
                  const peak = Math.max(...deep.weekdays.map((d) => d.averageMinutes), 1)
                  return (
                    <li key={day.weekday} className="flex items-center gap-3">
                      <span className="w-9 shrink-0 text-[12px] font-medium text-fg-muted">
                        {day.label}
                      </span>
                      <span className="h-2 flex-1 overflow-hidden rounded-full bg-line">
                        <span
                          style={{ width: `${(day.averageMinutes / peak) * 100}%` }}
                          className="block h-full rounded-full bg-accent/50"
                        />
                      </span>
                      <span className="tnum w-14 shrink-0 text-right text-[12px] text-fg-muted">
                        {formatDuration(day.averageMinutes) || '—'}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </Panel>
          )}

          {deep && deep.trends.length > 0 && (
            <Panel title="What changed" note={`vs the previous ${summary.days.length} days`}>
              <ul className="mt-4 space-y-3">
                {deep.trends.slice(0, 6).map((trend) => (
                  <li
                    key={trend.categoryId ?? 'none'}
                    data-tone={trend.tone}
                    className="flex items-center gap-3"
                  >
                    <span
                      aria-hidden="true"
                      className="size-2 shrink-0 rounded-full bg-[var(--tone-solid)]"
                    />
                    <span className="min-w-0 flex-1 truncate text-[13px] text-fg">{trend.name}</span>
                    <span className="tnum shrink-0 text-[13px] text-fg-muted">
                      {formatDuration(trend.minutes) || '0m'}
                    </span>
                    <span
                      className={cn(
                        'tnum w-20 shrink-0 text-right text-[12px] font-medium',
                        trend.deltaMinutes > 0 && 'text-success',
                        trend.deltaMinutes < 0 && 'text-danger',
                        trend.deltaMinutes === 0 && 'text-fg-faint',
                      )}
                    >
                      {trend.deltaMinutes === 0
                        ? 'no change'
                        : `${trend.deltaMinutes > 0 ? '+' : '−'}${formatDuration(Math.abs(trend.deltaMinutes))}`}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {deep && deep.plan.rate != null && (
            <Panel title="Planned vs done" note={`${deep.plan.completed} of ${deep.plan.planned}`}>
              <div className="mt-4 flex items-center gap-4">
                <span className="tnum text-[26px] font-semibold tracking-[-0.02em] text-fg">
                  {Math.round(deep.plan.rate * 100)}%
                </span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-line">
                  <span
                    style={{ width: `${deep.plan.rate * 100}%` }}
                    className="block h-full rounded-full bg-success"
                  />
                </span>
              </div>
              <p className="mt-2 text-[12px] leading-[1.5] text-fg-faint">
                Of the tasks you gave a day in this window, this many were finished. Tasks with no
                date are not counted — they were never promised to a day.
              </p>
            </Panel>
          )}

          <section className="rounded-2xl border border-card-line bg-surface p-4 sm:p-5">
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

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded-2xl border border-card-line bg-surface p-3.5 sm:p-[18px]">
      <p className="truncate text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-faint sm:text-[11px]">
        {label}
      </p>
      <p className="tnum mt-1 text-[19px] font-semibold tracking-[-0.02em] text-fg sm:mt-1.5 sm:text-[22px]">
        {value}
      </p>
      {detail && <p className="tnum mt-0.5 text-[11.5px] text-fg-faint">{detail}</p>}
    </div>
  )
}

/** A card with a heading and an optional note on the right. */
function Panel({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-card-line bg-surface p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-fg">{title}</h3>
        {note && <p className="text-[12px] text-fg-faint">{note}</p>}
      </div>
      {children}
    </section>
  )
}
