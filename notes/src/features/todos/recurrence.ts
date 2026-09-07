import { fromDayKey, shiftDay } from '@/lib/date/day-key'
import type { DayKey, Recurrence, Weekday } from '@/types'

/** Sunday first, matching `Date.getDay`. */
export const WEEKDAY_INITIALS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'] as const
export const WEEKDAY_NAMES = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
] as const
export const WEEKDAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

const WORKING_WEEK: Weekday[] = [1, 2, 3, 4, 5]
const WEEKEND: Weekday[] = [0, 6]

export function weekdayOf(day: DayKey): Weekday {
  return fromDayKey(day).getDay() as Weekday
}

/**
 * The chosen days, in week order and without duplicates.
 *
 * Ordering here rather than at every call site means a set built by tapping
 * Friday then Monday still reads, sorts and labels as Monday then Friday.
 */
export function weekdaysOf(recurrence: Recurrence): Weekday[] {
  if (recurrence.kind !== 'weekly') return []
  return [...new Set(recurrence.weekdays)].sort((a, b) => a - b)
}

function sameDays(days: Weekday[], other: Weekday[]): boolean {
  return days.length === other.length && days.every((d, i) => d === other[i])
}

/** The first `weekday` falling on or after `from` — `from` itself if it matches. */
export function weekdayOnOrAfter(from: DayKey, weekday: Weekday): DayKey {
  // Arithmetic on the weekday index rather than on a Date, so a clock change
  // in the middle of the week cannot move the answer.
  return shiftDay(from, (weekday - weekdayOf(from) + 7) % 7)
}

/**
 * The first occurrence of the series on or after `from`.
 *
 * With several days chosen it is the soonest of them, so a Mon/Wed/Fri task
 * asked about on Tuesday answers Wednesday rather than next Monday.
 */
export function occurrenceOnOrAfter(recurrence: Recurrence, from: DayKey): DayKey {
  if (recurrence.kind === 'daily') return from

  const days = weekdaysOf(recurrence)
  // A weekly repeat with nothing selected has no next day. Treating it as
  // "today" keeps a corrupt record from stalling a sweep over every other task.
  if (days.length === 0) return from

  const start = weekdayOf(from)
  const soonest = Math.min(...days.map((day) => (day - start + 7) % 7))
  return shiftDay(from, soonest)
}

/**
 * The occurrence following `after`.
 *
 * Anchored to the day the task was *due*, not the day it was ticked: a weekly
 * Monday task done on Sunday is still due the next Monday, and finishing early
 * should not quietly drag the whole series earlier.
 */
export function occurrenceAfter(recurrence: Recurrence, after: DayKey): DayKey {
  return occurrenceOnOrAfter(recurrence, shiftDay(after, 1))
}

/** "Every day", "Every Monday", "Mon, Wed & Fri" — the phrase, for the picker. */
export function describeRecurrence(recurrence: Recurrence): string {
  if (recurrence.kind === 'daily') return 'Every day'

  const days = weekdaysOf(recurrence)
  if (days.length === 0) return 'Every week'
  if (days.length === 7) return 'Every day'
  if (sameDays(days, WORKING_WEEK)) return 'Every weekday'
  if (sameDays(days, WEEKEND)) return 'Every weekend'
  if (days.length === 1) return `Every ${WEEKDAY_NAMES[days[0]]}`

  const names = days.map((day) => WEEKDAY_SHORT[day])
  return `${names.slice(0, -1).join(', ')} & ${names[names.length - 1]}`
}

/** "Daily", "Mondays", "Mon, Wed, Fri" — the tag, for a row that says enough. */
export function shortRecurrence(recurrence: Recurrence): string {
  if (recurrence.kind === 'daily') return 'Daily'

  const days = weekdaysOf(recurrence)
  if (days.length === 0) return 'Weekly'
  if (days.length === 7) return 'Daily'
  if (sameDays(days, WORKING_WEEK)) return 'Weekdays'
  if (sameDays(days, WEEKEND)) return 'Weekends'
  if (days.length === 1) return `${WEEKDAY_NAMES[days[0]]}s`
  return days.map((day) => WEEKDAY_SHORT[day]).join(', ')
}
