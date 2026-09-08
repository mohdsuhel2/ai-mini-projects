import { shiftDay } from '@/lib/date/day-key'
import { weekdayOf, WEEKDAY_SHORT } from '@/features/todos/recurrence'
import { blockSpan, MINUTES_IN_DAY } from './summary'
import type { Activity, Category, DayKey, Todo, Tone } from '@/types'

/** A stretch of the clock, for the "when do I actually work" band. */
export interface HourBand {
  /** 0–23. */
  hour: number
  minutes: number
  /** 0–1 against the busiest hour, for drawing. */
  share: number
}

export interface WeekdayLoad {
  weekday: number
  label: string
  minutes: number
  /** Days of this weekday inside the window, so the average is honest. */
  occurrences: number
  averageMinutes: number
}

export interface CategoryTrend {
  categoryId: string | null
  name: string
  tone: Tone
  minutes: number
  /** Same-length previous window, for the direction of travel. */
  previousMinutes: number
  /** Percentage points of the window's total, this period vs last. */
  deltaMinutes: number
}

export interface StreakInfo {
  /** Consecutive days ending today (or yesterday) with something tracked. */
  current: number
  longest: number
}

export interface PlanVsDone {
  planned: number
  completed: number
  /** 0–1, or null when nothing was planned in the window. */
  rate: number | null
}

/**
 * Which hours of the day the work actually lands in.
 *
 * Built from each entry's real span rather than its start, so a two-hour block
 * counts toward both hours it covered — the question is "when am I busy", not
 * "when do I press start".
 */
export function hoursOfDay(activities: Activity[], days: Set<DayKey>): HourBand[] {
  const minutes = new Array<number>(24).fill(0)

  for (const activity of activities) {
    if (!days.has(activity.date)) continue
    const span = blockSpan(activity)
    if (!span) continue
    for (let m = span.start; m < span.end && m < MINUTES_IN_DAY; m++) {
      minutes[Math.floor(m / 60)] += 1
    }
  }

  const peak = Math.max(...minutes, 1)
  return minutes.map((value, hour) => ({ hour, minutes: value, share: value / peak }))
}

/** Load per weekday, averaged over how many of each fell in the window. */
export function weekdayLoad(activities: Activity[], days: DayKey[]): WeekdayLoad[] {
  const inRange = new Set(days)
  const totals = new Array<number>(7).fill(0)
  const occurrences = new Array<number>(7).fill(0)

  for (const day of days) occurrences[weekdayOf(day)] += 1
  for (const activity of activities) {
    if (!inRange.has(activity.date)) continue
    totals[weekdayOf(activity.date)] += activity.duration ?? 0
  }

  return totals.map((minutes, weekday) => ({
    weekday,
    label: WEEKDAY_SHORT[weekday],
    minutes,
    occurrences: occurrences[weekday],
    averageMinutes: occurrences[weekday] > 0 ? Math.round(minutes / occurrences[weekday]) : 0,
  }))
}

/**
 * Category totals against the window immediately before this one.
 *
 * A total on its own says what happened; the pair says whether it is growing.
 * Categories absent from either window still appear, so a habit that stopped is
 * as visible as one that started.
 */
export function categoryTrends(
  activities: Activity[],
  days: DayKey[],
  categories: Category[],
): CategoryTrend[] {
  const byId = new Map(categories.map((c) => [c.id, c]))
  const current = new Set(days)
  const previous = new Set(days.map((day) => shiftDay(day, -days.length)))

  const now = new Map<string, number>()
  const before = new Map<string, number>()

  for (const activity of activities) {
    const minutes = activity.duration ?? 0
    if (minutes <= 0) continue
    const key = activity.categoryId ?? '__none__'
    if (current.has(activity.date)) now.set(key, (now.get(key) ?? 0) + minutes)
    else if (previous.has(activity.date)) before.set(key, (before.get(key) ?? 0) + minutes)
  }

  const keys = new Set([...now.keys(), ...before.keys()])
  return [...keys]
    .map((key) => {
      const category = key === '__none__' ? undefined : byId.get(key)
      const minutes = now.get(key) ?? 0
      const previousMinutes = before.get(key) ?? 0
      return {
        categoryId: key === '__none__' ? null : key,
        name: category?.name ?? 'Other',
        tone: (category?.tone ?? 'slate') as Tone,
        minutes,
        previousMinutes,
        deltaMinutes: minutes - previousMinutes,
      }
    })
    .sort((a, b) => b.minutes - a.minutes || a.name.localeCompare(b.name))
}

/**
 * Consecutive tracked days.
 *
 * The current streak may end today or yesterday: a day with nothing logged yet
 * is not a broken streak at nine in the morning.
 */
export function trackingStreak(activities: Activity[], today: DayKey): StreakInfo {
  const tracked = new Set(
    activities.filter((a) => (a.duration ?? 0) > 0).map((a) => a.date),
  )

  let current = 0
  const start = tracked.has(today) ? today : shiftDay(today, -1)
  for (let day = start; tracked.has(day); day = shiftDay(day, -1)) current += 1

  let longest = 0
  let run = 0
  const sorted = [...tracked].sort()
  for (let i = 0; i < sorted.length; i++) {
    run = i > 0 && shiftDay(sorted[i], -1) === sorted[i - 1] ? run + 1 : 1
    longest = Math.max(longest, run)
  }

  return { current, longest }
}

/** How much of what was planned in the window actually got done. */
export function planVsDone(todos: Todo[], days: Set<DayKey>): PlanVsDone {
  const planned = todos.filter((t) => t.plannedDate != null && days.has(t.plannedDate))
  const completed = planned.filter((t) => t.status === 'COMPLETED')
  return {
    planned: planned.length,
    completed: completed.length,
    rate: planned.length > 0 ? completed.length / planned.length : null,
  }
}
