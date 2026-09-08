'use client'

import { useLiveQuery } from 'dexie-react-hooks'
import { useEffect, useMemo, useRef } from 'react'
import { db } from '@/lib/db/database'
import { useNow } from './use-now'
import { shiftDay, todayKey } from '@/lib/date/day-key'
import { liveOnly } from '@/lib/db/records'
import { listCategories } from '@/features/categories/api'
import { listOpenTodos, rollForwardRecurring } from '@/features/todos/api'
import { sortActivities } from '@/features/activities/api'
import { getSettings, DEFAULT_SETTINGS } from '@/features/settings/api'
import { getTimer } from '@/features/timer/api'
import { daySchedule, summariseDay, type DaySchedule } from '@/features/analytics/summary'
import { rangeDays, summariseRange, RANGE_DAYS, type RangeId, type RangeSummary } from '@/features/analytics/range'
import { listActivitiesBetween } from '@/features/activities/api'
import {
  categoryTrends,
  hoursOfDay,
  planVsDone,
  trackingStreak,
  weekdayLoad,
  type CategoryTrend,
  type HourBand,
  type PlanVsDone,
  type StreakInfo,
  type WeekdayLoad,
} from '@/features/analytics/insights'
import { listFolders, listNotes } from '@/features/notes/api'
import { buildFolderTree, rootNotes } from '@/features/notes/tree'
import type {
  Activity,
  Category,
  DailySummary,
  DayKey,
  Folder,
  FolderNode,
  Note,
  Settings,
  TimerState,
  Todo,
} from '@/types'

/**
 * `useLiveQuery` makes IndexedDB the reactive source of truth directly, which
 * is why there is no global store in this app: a write anywhere re-renders
 * every reader, with no second copy of the data to keep in step.
 */

export function useCategories(): Category[] | undefined {
  return useLiveQuery(() => listCategories(), [])
}

export function useCategoryMap(): Map<string, Category> {
  const categories = useCategories()
  return useMemo(() => new Map((categories ?? []).map((c) => [c.id, c])), [categories])
}

export function useOpenTodos(): Todo[] | undefined {
  return useLiveQuery(() => listOpenTodos(), [])
}

export function useTodosForDay(day: DayKey): Todo[] | undefined {
  return useLiveQuery(async () => {
    const rows = await db().todos.where('plannedDate').equals(day).toArray()
    return liveOnly(rows)
  }, [day])
}

export function useActivitiesForDay(day: DayKey): Activity[] | undefined {
  return useLiveQuery(async () => {
    const rows = await db().activities.where('date').equals(day).toArray()
    return sortActivities(liveOnly(rows))
  }, [day])
}

export function useSettings(): Settings {
  return useLiveQuery(() => getSettings(), [], DEFAULT_SETTINGS)
}

export function useTimer(): TimerState | undefined | null {
  return useLiveQuery(async () => (await getTimer()) ?? null, [])
}

export function useFolders(): Folder[] | undefined {
  return useLiveQuery(() => listFolders(), [])
}

export function useNotes(): Note[] | undefined {
  return useLiveQuery(() => listNotes(), [])
}

export function useNote(id: string | null): Note | undefined {
  return useLiveQuery(async () => (id ? await db().notes.get(id) : undefined), [id])
}

export interface NotesTree {
  roots: FolderNode[]
  unfiled: Note[]
  folders: Folder[]
  notes: Note[]
  loading: boolean
}

export function useNotesTree(): NotesTree {
  const folders = useFolders()
  const notes = useNotes()

  return useMemo(() => {
    if (!folders || !notes) {
      return { roots: [], unfiled: [], folders: [], notes: [], loading: true }
    }
    return {
      roots: buildFolderTree(folders, notes),
      unfiled: rootNotes(notes),
      folders,
      notes,
      loading: false,
    }
  }, [folders, notes])
}

export function useDailySummary(day: DayKey): DailySummary | undefined {
  const todos = useTodosForDay(day)
  const activities = useActivitiesForDay(day)
  const categories = useCategories()

  return useMemo(() => {
    if (!todos || !activities || !categories) return undefined
    return summariseDay(day, todos, activities, categories)
  }, [day, todos, activities, categories])
}

/** The same day laid out on the clock, for the 24-hour bar. */
export function useDaySchedule(day: DayKey): DaySchedule | undefined {
  const activities = useActivitiesForDay(day)
  const categories = useCategories()

  return useMemo(() => {
    if (!activities || !categories) return undefined
    return daySchedule(activities, categories)
  }, [activities, categories])
}

/** A rolling window of days ending today, rolled up for the Insights surface. */
export function useRangeSummary(range: RangeId): RangeSummary | undefined {
  const categories = useCategories()
  const days = useMemo(() => rangeDays(RANGE_DAYS[range]), [range])
  const activities = useLiveQuery(
    () => listActivitiesBetween(days[0], days[days.length - 1]),
    [days],
  )

  return useMemo(() => {
    if (!activities || !categories) return undefined
    return summariseRange(days, activities, categories)
  }, [days, activities, categories])
}

/**
 * Catches repeating tasks up to today, once per day.
 *
 * Runs on mount and again when the date rolls over, so a tab left open
 * overnight wakes up showing the same thing a fresh one would. Guarded by the
 * day it last ran for: the clock ticks far more often than the date changes.
 */
export function useRecurringRollForward(): void {
  const now = useNow(60_000)
  const day = todayKey(new Date(now))
  const ranFor = useRef<DayKey | null>(null)

  useEffect(() => {
    if (ranFor.current === day) return
    ranFor.current = day
    void rollForwardRecurring(day)
  }, [day])
}

export interface DeepInsights {
  hours: HourBand[]
  weekdays: WeekdayLoad[]
  trends: CategoryTrend[]
  streak: StreakInfo
  plan: PlanVsDone
}

/**
 * The second layer of the Insights surface.
 *
 * Reads twice the window so each category can be set against the period before
 * it — a total tells you what happened, the pair tells you whether it is
 * growing. Streaks read the whole history, since a streak is not a fact about
 * the window you happen to be looking at.
 */
export function useDeepInsights(range: RangeId): DeepInsights | undefined {
  const categories = useCategories()
  const days = useMemo(() => rangeDays(RANGE_DAYS[range]), [range])
  const today = days[days.length - 1]

  const activities = useLiveQuery(
    () => listActivitiesBetween(shiftDay(days[0], -days.length), today),
    [days],
  )
  const allActivities = useLiveQuery(() => db().activities.toArray().then(liveOnly), [])
  const todos = useLiveQuery(() => db().todos.toArray().then(liveOnly), [])

  return useMemo(() => {
    if (!activities || !allActivities || !todos || !categories) return undefined
    const window = new Set(days)
    return {
      hours: hoursOfDay(activities, window),
      weekdays: weekdayLoad(activities, days),
      trends: categoryTrends(activities, days, categories),
      streak: trackingStreak(allActivities, today),
      plan: planVsDone(todos, window),
    }
  }, [activities, allActivities, todos, categories, days, today])
}
