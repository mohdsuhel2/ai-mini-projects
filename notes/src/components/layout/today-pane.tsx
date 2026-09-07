'use client'

import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ActivityComposer } from '@/components/activity/activity-composer'
import { DailySummary } from '@/components/dashboard/daily-summary'
import { Timeline } from '@/components/timeline/timeline'
import { IconButton } from '@/components/common/icon-button'
import { useDailySummary, useDaySchedule } from '@/hooks/use-data'
import { shiftDay, todayKey } from '@/lib/date/day-key'
import { formatDayFull, formatDayLabel } from '@/lib/date/format'
import { track } from '@/lib/analytics'
import type { DayKey, Id } from '@/types'

interface TodayPaneProps {
  day: DayKey
  onDayChange: (day: DayKey) => void
}

/**
 * The record of one day. The picker and the timeline sit straight on the ground
 * — a box around the whole pane only drew a second edge inside the one the
 * layout already has. Only the summary and the composer are cards, because
 * those two are objects you act on rather than a list you read.
 */
export function TodayPane({ day, onDayChange }: TodayPaneProps) {
  const summary = useDailySummary(day)
  const schedule = useDaySchedule(day)
  const today = todayKey()
  const isToday = day === today
  // Held here, not in either child: tapping a row lights the matching block on
  // the bar, so the two have to be reading the same value.
  const [selectedId, setSelectedId] = useState<Id | null>(null)

  function step(amount: number) {
    onDayChange(shiftDay(day, amount))
    track('day_navigated', { direction: amount > 0 ? 'forward' : 'back' })
  }

  return (
    <div>
      <header className="space-y-4">
        <div className="flex items-center gap-2">
          <IconButton
            label="Previous day"
            onClick={() => step(-1)}
            className="size-9 rounded-full border border-card-line bg-surface"
          >
            <ChevronLeft className="size-4" strokeWidth={2} />
          </IconButton>

          <div className="min-w-0 flex-1 text-center">
            <p className="text-[15px] font-semibold tracking-[-0.015em] text-fg">
              {formatDayLabel(day)}
            </p>
            <p className="mt-0.5 text-[12px] text-fg-faint">{formatDayFull(day)}</p>
          </div>

          <IconButton
            label="Next day"
            onClick={() => step(1)}
            disabled={day >= today}
            className="size-9 rounded-full border border-card-line bg-surface"
          >
            <ChevronRight className="size-4" strokeWidth={2} />
          </IconButton>
        </div>

        {!isToday && (
          <button
            type="button"
            onClick={() => onDayChange(today)}
            className="mx-auto block rounded-full px-2.5 py-1 text-[11.5px] text-accent transition-colors hover:bg-accent-soft"
          >
            Back to today
          </button>
        )}

        <DailySummary
          summary={summary}
          schedule={schedule}
          isToday={isToday}
          highlightId={selectedId}
        />
      </header>

      <div className="mt-6">
        <Timeline
          day={day}
          isToday={isToday}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      </div>

      <div className="mt-5">
        <ActivityComposer day={day} />
      </div>
    </div>
  )
}
