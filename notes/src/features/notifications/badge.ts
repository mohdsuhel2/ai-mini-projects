import { groupIdFor } from '@/features/todos/grouping'
import { todayKey } from '@/lib/date/day-key'
import type { DayKey, Todo } from '@/types'

/**
 * What the number on the app icon means: open tasks that are late or due today.
 *
 * Deliberately not every open task. A badge counting things due next month
 * never reaches zero, and a badge that never reaches zero is one you learn to
 * read as decoration.
 */
export function badgeCount(todos: Todo[], today: DayKey = todayKey()): number {
  return todos.filter((todo) => {
    if (todo.status !== 'OPEN') return false
    const group = groupIdFor(todo.plannedDate, today)
    return group === 'pending' || group === 'today'
  }).length
}

/**
 * Puts the count on the app icon, or takes it off at zero.
 *
 * Every call is guarded twice: the API is missing on most browsers, and where
 * it exists it rejects unless the app is installed. A badge is a nicety, so
 * failing to set one must never surface as an error.
 */
export async function applyBadge(count: number): Promise<void> {
  if (typeof navigator === 'undefined') return
  const nav = navigator as Navigator & {
    setAppBadge?: (count?: number) => Promise<void>
    clearAppBadge?: () => Promise<void>
  }

  try {
    if (count > 0) await nav.setAppBadge?.(count)
    else await nav.clearAppBadge?.()
  } catch {
    // Not installed, or the platform does not badge. Nothing to recover from.
  }
}
