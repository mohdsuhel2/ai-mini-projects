import { shiftDay, todayKey } from '@/lib/date/day-key'
import type { Activity, Category, CategoryTotal, DayKey, Tone } from '@/types'
import { MINUTES_IN_DAY } from './summary'

export type RangeId = 'week' | 'month'

export const RANGE_DAYS: Record<RangeId, number> = { week: 7, month: 30 }

const UNCATEGORISED: Pick<CategoryTotal, 'name' | 'tone' | 'icon'> = {
  name: 'Other',
  tone: 'slate',
  icon: 'circle-dashed',
}

/** One day's tracked total, for the column chart. */
export interface DayBar {
  day: DayKey
  minutes: number
  /** 0–1 against the busiest day in the range, so the tallest bar is full. */
  height: number
  isToday: boolean
}

export interface RangeSummary {
  from: DayKey
  to: DayKey
  days: DayBar[]
  byCategory: Array<CategoryTotal & { share: number }>
  totalMinutes: number
  /** Mean over every day in the window, including the ones with nothing on them. */
  dailyAverage: number
  /** Days with at least one tracked minute. */
  activeDays: number
  busiest: DayBar | null
}

/** The `count` days ending today, oldest first. */
export function rangeDays(count: number, end: DayKey = todayKey()): DayKey[] {
  return Array.from({ length: count }, (_, i) => shiftDay(end, i - (count - 1)))
}

/**
 * A window of days rolled up two ways at once: down the calendar, so the shape
 * of a week is visible, and across categories, so its content is. Days with
 * nothing logged stay in the series — an empty Thursday is a fact about the
 * week, and dropping it would quietly redraw the chart as a denser one.
 */
export function summariseRange(
  days: DayKey[],
  activities: Activity[],
  categories: Category[],
): RangeSummary {
  const byId = new Map(categories.map((c) => [c.id, c]))
  const inRange = new Set(days)
  const perDay = new Map<DayKey, number>(days.map((day) => [day, 0]))
  const totals = new Map<string, CategoryTotal>()
  const today = todayKey()

  let totalMinutes = 0

  for (const activity of activities) {
    if (!inRange.has(activity.date)) continue
    const minutes = activity.duration ?? 0
    if (minutes <= 0) continue

    perDay.set(activity.date, (perDay.get(activity.date) ?? 0) + minutes)
    totalMinutes += minutes

    const key = activity.categoryId ?? '__none__'
    const category = activity.categoryId ? byId.get(activity.categoryId) : undefined
    const existing = totals.get(key)
    if (existing) {
      existing.minutes += minutes
    } else {
      totals.set(key, {
        categoryId: activity.categoryId ?? null,
        name: category?.name ?? UNCATEGORISED.name,
        tone: (category?.tone ?? UNCATEGORISED.tone) as Tone,
        icon: category?.icon ?? UNCATEGORISED.icon,
        minutes,
      })
    }
  }

  // A day cannot hold more than a day; a mis-entered 40-hour activity must not
  // flatten every other column to nothing.
  const peak = Math.max(...days.map((day) => Math.min(perDay.get(day) ?? 0, MINUTES_IN_DAY)), 1)

  const bars: DayBar[] = days.map((day) => {
    const minutes = perDay.get(day) ?? 0
    return {
      day,
      minutes,
      height: Math.min(minutes, MINUTES_IN_DAY) / peak,
      isToday: day === today,
    }
  })

  const busiest = bars.reduce<DayBar | null>(
    (best, bar) => (bar.minutes > 0 && (!best || bar.minutes > best.minutes) ? bar : best),
    null,
  )

  return {
    from: days[0],
    to: days[days.length - 1],
    days: bars,
    byCategory: [...totals.values()]
      .sort((a, b) => b.minutes - a.minutes || a.name.localeCompare(b.name))
      .map((c) => ({ ...c, share: totalMinutes === 0 ? 0 : c.minutes / totalMinutes })),
    totalMinutes,
    dailyAverage: days.length === 0 ? 0 : Math.round(totalMinutes / days.length),
    activeDays: bars.filter((bar) => bar.minutes > 0).length,
    busiest,
  }
}
