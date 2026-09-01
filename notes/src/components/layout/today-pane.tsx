'use client'

import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ActivityComposer } from '@/components/activity/activity-composer'
import { DailySummary } from '@/components/dashboard/daily-summary'
import { Timeline } from '@/components/timeline/timeline'
import { IconButton } from '@/components/common/icon-button'
import { useDailySummary } from '@/hooks/use-data'
import { shiftDay, todayKey } from '@/lib/date/day-key'
import { formatDayFull, formatDayLabel } from '@/lib/date/format'
import { track } from '@/lib/analytics'
import type { DayKey } from '@/types'

interface TodayPaneProps {
  day: DayKey
  onDayChange: (day: DayKey) => void
}

export function TodayPane({ day, onDayChange }: TodayPaneProps) {
  const summary = useDailySummary(day)
  const today = todayKey()
  const isToday = day === today

  function step(amount: number) {
    onDayChange(shiftDay(day, amount))
    track('day_navigated', { direction: amount > 0 ? 'forward' : 'back' })
  }

  return (
    <div className="flex flex-col gap-5">
      <header className="space-y-3">
        <div className="flex items-center gap-1">
          <IconButton label="Previous day" size="sm" onClick={() => step(-1)}>
            <ChevronLeft className="size-4" strokeWidth={2} />
          </IconButton>

          <div className="min-w-0 flex-1 text-center">
            <p className="text-[13.5px] font-semibold tracking-[-0.01em] text-fg">{formatDayLabel(day)}</p>
            <p className="text-[11.5px] text-fg-faint">{formatDayFull(day)}</p>
          </div>

          <IconButton
            label="Next day"
            size="sm"
            onClick={() => step(1)}
            disabled={day >= today}
          >
            <ChevronRight className="size-4" strokeWidth={2} />
          </IconButton>
        </div>

        {!isToday && (
          <button
            type="button"
            onClick={() => onDayChange(today)}
            className="mx-auto block rounded-md px-2 py-1 text-[11.5px] text-accent transition-colors hover:bg-accent-soft"
          >
            Back to today
          </button>
        )}

        <div className="border-t border-line pt-3.5">
          <DailySummary summary={summary} isToday={isToday} />
        </div>
      </header>

      <Timeline day={day} isToday={isToday} />

      <div className="sticky bottom-0 -mx-0.5 bg-gradient-to-t from-bg via-bg to-transparent pb-0.5 pt-3">
        <ActivityComposer day={day} />
      </div>
    </div>
  )
}
