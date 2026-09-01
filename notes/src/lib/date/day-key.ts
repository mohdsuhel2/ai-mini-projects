import { addDays, format, isValid, parse, startOfDay } from 'date-fns'
import type { DayKey, MinuteOfDay } from '@/types'

/**
 * A DayKey is the local calendar day as `YYYY-MM-DD`.
 *
 * The whole app pivots on "which day is this on", and a Date object answers
 * that question differently depending on the reader's timezone. Storing the
 * day the user meant, as a string, removes the question entirely.
 */

export const DAY_KEY_FORMAT = 'yyyy-MM-dd'

export function toDayKey(date: Date): DayKey {
  return format(date, DAY_KEY_FORMAT)
}

export function fromDayKey(day: DayKey): Date {
  return parse(day, DAY_KEY_FORMAT, new Date())
}

export function isDayKey(value: unknown): value is DayKey {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = parse(value, DAY_KEY_FORMAT, new Date())
  return isValid(parsed) && format(parsed, DAY_KEY_FORMAT) === value
}

export function todayKey(now: Date = new Date()): DayKey {
  return toDayKey(now)
}

export function shiftDay(day: DayKey, amount: number): DayKey {
  return toDayKey(addDays(fromDayKey(day), amount))
}

/** Positive when `day` is in the future relative to `reference`. */
export function daysBetween(day: DayKey, reference: DayKey): number {
  const ms = startOfDay(fromDayKey(day)).getTime() - startOfDay(fromDayKey(reference)).getTime()
  return Math.round(ms / 86_400_000)
}

/** The upcoming Saturday, or today when today is already the weekend. */
export function weekendKey(now: Date = new Date()): DayKey {
  const dow = now.getDay() // 0 Sun … 6 Sat
  if (dow === 6 || dow === 0) return toDayKey(now)
  return toDayKey(addDays(now, 6 - dow))
}

export function minutesOfDay(date: Date): MinuteOfDay {
  return date.getHours() * 60 + date.getMinutes()
}

export function instantToDayKey(instant: number): DayKey {
  return toDayKey(new Date(instant))
}

export function instantToMinutes(instant: number): MinuteOfDay {
  return minutesOfDay(new Date(instant))
}
