import { db } from '@/lib/db/database'
import { liveOnly, newId, now } from '@/lib/db/records'
import { instantToMinutes, todayKey } from '@/lib/date/day-key'
import type { Activity, ActivitySource, DayKey, Id, MinuteOfDay } from '@/types'

export interface NewActivityInput {
  title: string
  date: DayKey
  categoryId?: Id | null
  duration?: number | null
  startTime?: MinuteOfDay | null
  endTime?: MinuteOfDay | null
  notes?: string | null
  source?: ActivitySource
  todoId?: Id | null
}

/** Chronological, with untimed entries last so the day still reads top to bottom. */
export function sortActivities(activities: Activity[]): Activity[] {
  return activities.sort((a, b) => {
    const aAt = a.startTime ?? a.endTime
    const bAt = b.startTime ?? b.endTime
    if (aAt != null && bAt != null && aAt !== bAt) return aAt - bAt
    if (aAt != null && bAt == null) return -1
    if (aAt == null && bAt != null) return 1
    return a.createdAt - b.createdAt
  })
}

export async function listActivitiesForDay(day: DayKey): Promise<Activity[]> {
  const rows = await db().activities.where('date').equals(day).toArray()
  return sortActivities(liveOnly(rows))
}

export async function listActivitiesBetween(from: DayKey, to: DayKey): Promise<Activity[]> {
  const rows = await db().activities.where('date').between(from, to, true, true).toArray()
  return sortActivities(liveOnly(rows))
}

/** The latest moment already accounted for on a day, or null if nothing is. */
export function lastEndOfDay(activities: Activity[]): MinuteOfDay | null {
  let latest: MinuteOfDay | null = null
  for (const activity of activities) {
    const end =
      activity.endTime ??
      (activity.startTime != null && activity.duration != null
        ? activity.startTime + activity.duration
        : null)
    if (end != null && (latest == null || end > latest)) latest = end
  }
  return latest == null ? null : Math.min(latest, 24 * 60 - 1)
}

/**
 * Where a new entry of this length should start.
 *
 * The day is a chain: a new activity picks up where the last one finished, so
 * logging several in a row needs no arithmetic. With nothing logged yet it ends
 * now instead, because the thing being recorded has just been done.
 */
export function suggestedStartTime(
  activities: Activity[],
  durationMinutes: number,
  nowMinutes: number,
): MinuteOfDay {
  const lastEnd = lastEndOfDay(activities)
  const candidate = lastEnd ?? nowMinutes - durationMinutes
  // Never run past the end of the day, never before the start of it.
  return Math.max(0, Math.min(candidate, 24 * 60 - 1 - Math.min(durationMinutes, 24 * 60 - 1)))
}

export async function createActivity(input: NewActivityInput): Promise<Id> {
  const title = input.title.trim()
  if (!title) throw new Error('An activity needs a title')

  const stamp = now()
  const duration = input.duration ?? null

  // An activity logged for today without an explicit time is anchored to the
  // moment it was recorded and backdated by its duration, so the timeline stays
  // chronological instead of collecting a pile of untimed rows.
  let startTime = input.startTime ?? null
  let endTime = input.endTime ?? null
  if (startTime == null && endTime == null && duration != null && input.date === todayKey()) {
    endTime = instantToMinutes(stamp)
    startTime = Math.max(0, endTime - duration)
  }
  if (startTime != null && endTime == null && duration != null) {
    endTime = Math.min(24 * 60 - 1, startTime + duration)
  }

  const id = newId()
  await db().activities.add({
    id,
    title,
    categoryId: input.categoryId ?? null,
    date: input.date,
    startTime,
    endTime,
    duration,
    source: input.source ?? 'MANUAL',
    todoId: input.todoId ?? null,
    notes: input.notes ?? null,
    createdAt: stamp,
    updatedAt: stamp,
    deletedAt: null,
  })
  return id
}

export async function updateActivity(id: Id, patch: Partial<Omit<Activity, 'id'>>): Promise<void> {
  await db().activities.update(id, { ...patch, updatedAt: now() })
}

/**
 * Deleting a todo-derived activity also reopens its todo: the record and the
 * task are two views of one fact, and they must not disagree.
 */
export async function deleteActivity(id: Id): Promise<void> {
  const stamp = now()
  await db().transaction('rw', db().activities, db().todos, async () => {
    const activity = await db().activities.get(id)
    if (!activity) return
    await db().activities.update(id, { deletedAt: stamp, updatedAt: stamp })
    if (activity.source === 'TODO_COMPLETION' && activity.todoId) {
      await db().todos.update(activity.todoId, {
        status: 'OPEN',
        completedAt: null,
        actualDuration: null,
        updatedAt: stamp,
      })
    }
  })
}
