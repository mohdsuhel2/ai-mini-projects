import { groupIdFor } from '@/features/todos/grouping'
import { planVsDone, trackingStreak, type PlanVsDone, type StreakInfo } from './insights'
import type { Activity, DayKey, Todo } from '@/types'

export interface WeeklyReview {
  plan: PlanVsDone
  streak: StreakInfo
  trackedMinutes: number
  somedayCount: number
  overdueCount: number
  /** Still-open tasks that were dated inside the window. */
  unfinished: Todo[]
}

export function buildWeeklyReview(
  todos: Todo[],
  activities: Activity[],
  days: DayKey[],
  today: DayKey,
): WeeklyReview {
  const window = new Set(days)
  const open = todos.filter((t) => t.status === 'OPEN')
  const trackedMinutes = activities
    .filter((a) => window.has(a.date))
    .reduce((sum, a) => sum + (a.duration ?? 0), 0)

  return {
    plan: planVsDone(todos, window),
    streak: trackingStreak(activities, today),
    trackedMinutes,
    somedayCount: open.filter((t) => t.plannedDate == null).length,
    overdueCount: open.filter((t) => t.plannedDate != null && t.plannedDate < today).length,
    unfinished: open.filter((t) => t.plannedDate != null && window.has(t.plannedDate)),
  }
}

export function carryForwardCandidates(todos: Todo[], today: DayKey): Todo[] {
  return todos.filter(
    (t) =>
      t.status === 'OPEN' &&
      t.plannedDate != null &&
      t.plannedDate < today &&
      groupIdFor(t.plannedDate, today) === 'pending',
  )
}
