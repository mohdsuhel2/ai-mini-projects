import { format, isValid } from 'date-fns'
import type { DayKey, MinuteOfDay } from '@/types'
import { daysBetween, fromDayKey, todayKey } from './day-key'

/** "2h 15m", "45m", "1h". Empty string for nothing worth showing. */
export function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes) || minutes <= 0) return ''
  const total = Math.round(minutes)
  const h = Math.floor(total / 60)
  const m = total % 60
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

/** "2 hours 15 minutes" — for screen readers and aria-labels. */
export function formatDurationLong(minutes: number | null | undefined): string {
  if (minutes == null || minutes <= 0) return 'no duration'
  const total = Math.round(minutes)
  const h = Math.floor(total / 60)
  const m = total % 60
  const parts: string[] = []
  if (h > 0) parts.push(`${h} hour${h === 1 ? '' : 's'}`)
  if (m > 0) parts.push(`${m} minute${m === 1 ? '' : 's'}`)
  return parts.join(' ')
}

/** Elapsed milliseconds as `HH:MM:SS` for the running timer. */
export function formatStopwatch(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  const s = totalSeconds % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(s)}`
}

export function formatClock(minute: MinuteOfDay | null | undefined): string {
  if (minute == null) return ''
  const h24 = Math.floor(minute / 60) % 24
  const m = minute % 60
  const suffix = h24 < 12 ? 'AM' : 'PM'
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12
  return `${h12}:${String(m).padStart(2, '0')} ${suffix}`
}

/**
 * "6:44:12 PM" — the wall clock for a display that ticks.
 *
 * Takes a Date rather than a MinuteOfDay because seconds do not survive that
 * type, which is minutes past midnight by definition.
 */
export function formatWallClock(date: Date): string {
  const h24 = date.getHours()
  const suffix = h24 < 12 ? 'AM' : 'PM'
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${h12}:${pad(date.getMinutes())}:${pad(date.getSeconds())} ${suffix}`
}

/** The hour label used down the left of the timeline: "9 AM", "12 PM". */
export function formatHourLabel(hour: number): string {
  const h = ((hour % 24) + 24) % 24
  const suffix = h < 12 ? 'AM' : 'PM'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return `${h12} ${suffix}`
}

/** "Today", "Tomorrow", "Yesterday", "Mon 8 Sep", "8 Sep 2027". */
export function formatDayLabel(day: DayKey, reference: DayKey = todayKey()): string {
  const delta = daysBetween(day, reference)
  if (delta === 0) return 'Today'
  if (delta === 1) return 'Tomorrow'
  if (delta === -1) return 'Yesterday'
  const date = fromDayKey(day)
  if (!isValid(date)) return day
  const sameYear = date.getFullYear() === fromDayKey(reference).getFullYear()
  if (delta > 1 && delta < 7) return format(date, 'EEEE')
  return sameYear ? format(date, 'EEE d MMM') : format(date, 'd MMM yyyy')
}

/** "Tuesday, 1 September" — the subheading under the greeting. */
export function formatDayFull(day: DayKey): string {
  const date = fromDayKey(day)
  return isValid(date) ? format(date, 'EEEE, d MMMM') : day
}

export function greetingFor(date: Date = new Date()): string {
  const h = date.getHours()
  if (h < 5) return 'Still up'
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  if (h < 22) return 'Good evening'
  return 'Winding down'
}
