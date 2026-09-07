import type { DayKey, Id, MinuteOfDay, Todo } from '@/types'

/**
 * How late a reminder may still fire.
 *
 * Without a window, opening the app in the evening would replay every task the
 * morning had scheduled. A reminder is only useful near the moment it names.
 */
export const REMINDER_WINDOW_MINUTES = 5

/**
 * The tasks whose time has just come round.
 *
 * Reminders live entirely in the page: with no server there is nobody to
 * deliver one while the app is shut, so this answers "what should fire right
 * now", and the caller runs it while the app is alive.
 */
export function dueReminders(
  todos: Todo[],
  nowMinutes: MinuteOfDay,
  today: DayKey,
  alreadyNotified: ReadonlySet<Id>,
  windowMinutes: number = REMINDER_WINDOW_MINUTES,
): Todo[] {
  return todos.filter((todo) => {
    if (todo.status !== 'OPEN') return false
    if (todo.plannedDate !== today) return false
    if (todo.plannedTime == null) return false
    if (alreadyNotified.has(todo.id)) return false

    const late = nowMinutes - todo.plannedTime
    return late >= 0 && late <= windowMinutes
  })
}
