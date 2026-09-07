'use client'

import { useMemo } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { TimelineItem } from './timeline-item'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { deleteActivity, updateActivity } from '@/features/activities/api'
import { dayRows, groupByDayPart, longestEntry } from '@/features/analytics/day-rows'
import { useActivitiesForDay, useCategoryMap } from '@/hooks/use-data'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { useUi } from '@/store/ui-context'
import { formatDuration } from '@/lib/date/format'
import type { Activity, DayKey, Id } from '@/types'

interface TimelineProps {
  day: DayKey
  isToday: boolean
  /** The entry lit on the day bar, owned by the pane so the bar sees it too. */
  selectedId: Id | null
  onSelect: (id: Id | null) => void
}

/**
 * The day as three grouped lists — morning, afternoon, evening — each with what
 * that stretch cost in its heading.
 *
 * A single flat list makes a day of fifteen entries one undifferentiated run;
 * splitting at the hours people actually plan around means "where did my
 * morning go" is answered by the heading before you read a single row.
 */
export function Timeline({ day, isToday, selectedId, onSelect }: TimelineProps) {
  const activities = useActivitiesForDay(day)
  const categories = useCategoryMap()
  const reducedMotion = usePrefersReducedMotion()
  const { notify } = useUi()

  const { groups, untimed, longest } = useMemo(() => {
    const { rows, untimed: loose } = dayRows(activities ?? [])
    return { groups: groupByDayPart(rows), untimed: loose, longest: longestEntry(rows) }
  }, [activities])

  if (!activities) return <PaneSkeleton rows={3} />

  if (activities.length === 0) {
    return (
      <EmptyState
        glyph="day"
        title={isToday ? 'Nothing recorded yet.' : 'Nothing was recorded this day.'}
        hint={
          isToday
            ? 'Finish a task, or log something you already did — even a coffee counts.'
            : 'Completed tasks and logged activities would appear here.'
        }
      />
    )
  }

  async function handleDelete(id: string, title: string) {
    await deleteActivity(id)
    notify(`Removed “${title}”`, {
      label: 'Undo',
      onClick: () => void updateActivity(id, { deletedAt: null }),
    })
  }

  const transition = { duration: reducedMotion ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] as const }

  const row = (activity: Activity, at: number | null, end: number | null, minutes: number) => (
    <motion.div
      key={activity.id}
      layout={!reducedMotion}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: reducedMotion ? 0 : 0.14 } }}
      transition={transition}
    >
      <TimelineItem
        activity={activity}
        category={activity.categoryId ? categories.get(activity.categoryId) : undefined}
        at={at}
        end={end}
        minutes={minutes}
        longest={longest}
        selected={selectedId === activity.id}
        // Tapping the lit row puts the whole bar back, so there is always a way
        // out of the highlight without hunting for a close affordance.
        onSelect={() => onSelect(selectedId === activity.id ? null : activity.id)}
        onDelete={() => void handleDelete(activity.id, activity.title)}
      />
    </motion.div>
  )

  return (
    <div className="space-y-5">
      {groups.map((group) => (
        <section key={group.part} aria-labelledby={`part-${group.part}`}>
          <div className="mb-1.5 flex items-baseline justify-between gap-3 px-1">
            <h3
              id={`part-${group.part}`}
              className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint"
            >
              {group.label}
            </h3>
            <span className="tnum text-[11.5px] font-medium text-fg-faint">
              {formatDuration(group.minutes)}
            </span>
          </div>

          <ul className="rounded-2xl border border-card-line bg-surface p-1">
            <AnimatePresence initial={false}>
              {group.entries.map((entry) =>
                row(entry.activity, entry.at, entry.end, entry.minutes),
              )}
            </AnimatePresence>
          </ul>
        </section>
      ))}

      {untimed.length > 0 && (
        <section aria-labelledby="part-untimed">
          <div className="mb-1.5 px-1">
            <h3
              id="part-untimed"
              className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint"
            >
              No time recorded
            </h3>
          </div>
          <ul className="rounded-2xl border border-card-line bg-surface p-1">
            {untimed.map((activity) => row(activity, null, null, 0))}
          </ul>
        </section>
      )}
    </div>
  )
}
