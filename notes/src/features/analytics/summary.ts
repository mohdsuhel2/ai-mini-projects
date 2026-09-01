import type { Activity, Category, CategoryTotal, DailySummary, DayKey, Todo } from '@/types'

const UNCATEGORISED: Pick<CategoryTotal, 'name' | 'tone' | 'icon'> = {
  name: 'Other',
  tone: 'slate',
  icon: 'circle-dashed',
}

/**
 * Rolls a day's activities up by category. Activities without a duration are
 * counted as events but contribute no minutes — the bar chart must never
 * pretend to know a length it was not told.
 */
export function summariseDay(
  day: DayKey,
  todos: Todo[],
  activities: Activity[],
  categories: Category[],
): DailySummary {
  const byId = new Map(categories.map((c) => [c.id, c]))
  const totals = new Map<string, CategoryTotal>()

  let trackedMinutes = 0

  for (const activity of activities) {
    const minutes = activity.duration ?? 0
    trackedMinutes += minutes
    if (minutes <= 0) continue

    const key = activity.categoryId ?? '__none__'
    const category = activity.categoryId ? byId.get(activity.categoryId) : undefined
    const existing = totals.get(key)
    if (existing) {
      existing.minutes += minutes
    } else {
      totals.set(key, {
        categoryId: activity.categoryId ?? null,
        name: category?.name ?? UNCATEGORISED.name,
        tone: category?.tone ?? UNCATEGORISED.tone,
        icon: category?.icon ?? UNCATEGORISED.icon,
        minutes,
      })
    }
  }

  const dayTodos = todos.filter((t) => t.plannedDate === day)

  return {
    day,
    completedCount: dayTodos.filter((t) => t.status === 'COMPLETED').length,
    openCount: dayTodos.filter((t) => t.status === 'OPEN').length,
    trackedMinutes,
    activityCount: activities.length,
    // Largest first; ties fall back to name so the order never flickers.
    byCategory: [...totals.values()].sort(
      (a, b) => b.minutes - a.minutes || a.name.localeCompare(b.name),
    ),
  }
}

/** Share of tracked time per category, as a 0–1 fraction. */
export function categoryShares(summary: DailySummary): Array<CategoryTotal & { share: number }> {
  const total = summary.byCategory.reduce((sum, c) => sum + c.minutes, 0)
  if (total === 0) return []
  return summary.byCategory.map((c) => ({ ...c, share: c.minutes / total }))
}

export const MINUTES_IN_DAY = 24 * 60

export interface DayFill {
  segments: Array<CategoryTotal & { share: number }>
  /** 0–1 of the whole 24 hours still unaccounted for. */
  untrackedShare: number
  untrackedMinutes: number
}

/**
 * The same totals measured against the whole day rather than against each
 * other. A bar scaled to tracked time always looks full, which is exactly the
 * question the user is asking it not to answer: 40 minutes logged should look
 * like 40 minutes out of 24 hours, not like a complete day.
 */
export function dayFill(summary: DailySummary): DayFill {
  const tracked = Math.min(summary.byCategory.reduce((sum, c) => sum + c.minutes, 0), MINUTES_IN_DAY)
  const untrackedMinutes = Math.max(0, MINUTES_IN_DAY - tracked)

  return {
    segments: summary.byCategory.map((c) => ({ ...c, share: c.minutes / MINUTES_IN_DAY })),
    untrackedShare: untrackedMinutes / MINUTES_IN_DAY,
    untrackedMinutes,
  }
}
