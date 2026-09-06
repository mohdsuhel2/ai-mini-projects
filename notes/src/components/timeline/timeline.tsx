'use client'

import { useMemo } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { TimelineItem } from './timeline-item'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { deleteActivity, updateActivity } from '@/features/activities/api'
import { dayRows } from '@/features/analytics/day-rows'
import { useActivitiesForDay, useCategoryMap } from '@/hooks/use-data'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { useUi } from '@/store/ui-context'
import { formatDuration } from '@/lib/date/format'
import type { DayKey } from '@/types'

interface TimelineProps {
  day: DayKey
  isToday: boolean
}

/**
 * The day as a column of blocks, tall in proportion to how long each thing took
 * and coloured by what kind of thing it was — so a day reads as a shape before
 * it reads as a list. The hours nothing was logged in collapse to a single
 * line, because an honest empty stretch still should not cost a screen of
 * scrolling to admit.
 */
export function Timeline({ day, isToday }: TimelineProps) {
  const activities = useActivitiesForDay(day)
  const categories = useCategoryMap()
  const reducedMotion = usePrefersReducedMotion()
  const { notify } = useUi()

  const { rows, untimed } = useMemo(() => dayRows(activities ?? []), [activities])

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

  return (
    <div className="space-y-4">
      {rows.length > 0 && (
        <ul className="space-y-1.5">
          <AnimatePresence initial={false}>
            {rows.map((row) =>
              row.kind === 'gap' ? (
                <motion.li
                  key={`gap-${row.from}-${row.to}`}
                  layout={!reducedMotion}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={transition}
                  className="flex items-center gap-2.5 py-0.5"
                >
                  <span className="w-[52px] shrink-0" aria-hidden="true" />
                  <span className="flex flex-1 items-center gap-2.5">
                    <span
                      aria-hidden="true"
                      className="h-px flex-1 border-t border-dashed border-line"
                    />
                    <span className="shrink-0 text-[10.5px] font-medium text-fg-faint">
                      {formatDuration(row.minutes)} unaccounted
                    </span>
                    <span
                      aria-hidden="true"
                      className="h-px flex-1 border-t border-dashed border-line"
                    />
                  </span>
                </motion.li>
              ) : (
                <motion.div
                  key={row.activity.id}
                  layout={!reducedMotion}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, transition: { duration: reducedMotion ? 0 : 0.14 } }}
                  transition={transition}
                >
                  <TimelineItem
                    activity={row.activity}
                    category={
                      row.activity.categoryId ? categories.get(row.activity.categoryId) : undefined
                    }
                    at={row.at}
                    end={row.end}
                    minutes={row.minutes}
                    onDelete={() => void handleDelete(row.activity.id, row.activity.title)}
                  />
                </motion.div>
              ),
            )}
          </AnimatePresence>
        </ul>
      )}

      {untimed.length > 0 && (
        <div>
          {rows.length > 0 && (
            <p className="mb-2 pl-[62px] text-[10.5px] font-bold uppercase tracking-[0.1em] text-fg-subtle">
              No time recorded
            </p>
          )}
          <ul className="space-y-1.5">
            {untimed.map((activity) => (
              <TimelineItem
                key={activity.id}
                activity={activity}
                category={activity.categoryId ? categories.get(activity.categoryId) : undefined}
                at={null}
                end={null}
                minutes={0}
                onDelete={() => void handleDelete(activity.id, activity.title)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
