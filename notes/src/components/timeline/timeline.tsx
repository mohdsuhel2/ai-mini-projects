'use client'

import { AnimatePresence, motion } from 'motion/react'
import { TimelineItem } from './timeline-item'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { deleteActivity } from '@/features/activities/api'
import { useActivitiesForDay, useCategoryMap } from '@/hooks/use-data'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { useUi } from '@/store/ui-context'
import { updateActivity } from '@/features/activities/api'
import type { DayKey } from '@/types'

interface TimelineProps {
  day: DayKey
  isToday: boolean
}

/**
 * A single thin rail down the day. Entries without a recorded time still belong
 * to the day, so they sit below the rail rather than being invented a position.
 */
export function Timeline({ day, isToday }: TimelineProps) {
  const activities = useActivitiesForDay(day)
  const categories = useCategoryMap()
  const reducedMotion = usePrefersReducedMotion()
  const { notify } = useUi()

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

  const timed = activities.filter((a) => (a.startTime ?? a.endTime) != null)
  const untimed = activities.filter((a) => (a.startTime ?? a.endTime) == null)

  async function handleDelete(id: string, title: string) {
    await deleteActivity(id)
    notify(`Removed “${title}”`, {
      label: 'Undo',
      onClick: () => void updateActivity(id, { deletedAt: null }),
    })
  }

  return (
    <div className="space-y-4">
      {timed.length > 0 && (
        <div className="relative">
          <ul className="space-y-0">
            <AnimatePresence initial={false}>
              {timed.map((activity) => (
                <motion.div
                  key={activity.id}
                  layout={!reducedMotion}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, transition: { duration: reducedMotion ? 0 : 0.14 } }}
                  transition={{ duration: reducedMotion ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
                >
                  <TimelineItem
                    activity={activity}
                    category={
                      activity.categoryId ? categories.get(activity.categoryId) : undefined
                    }
                    isLast={activity.id === timed[timed.length - 1]?.id}
                    onDelete={() => void handleDelete(activity.id, activity.title)}
                  />
                </motion.div>
              ))}
            </AnimatePresence>
          </ul>
        </div>
      )}

      {untimed.length > 0 && (
        <div className="space-y-0">
          {timed.length > 0 && (
            <p className="mb-2 px-1 text-[10.5px] font-bold uppercase tracking-[0.1em] text-fg-subtle">
              No time recorded
            </p>
          )}
          <ul>
            {untimed.map((activity) => (
              <TimelineItem
                key={activity.id}
                activity={activity}
                category={activity.categoryId ? categories.get(activity.categoryId) : undefined}
                onDelete={() => void handleDelete(activity.id, activity.title)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
