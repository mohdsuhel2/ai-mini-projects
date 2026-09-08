import { describe, expect, it } from 'vitest'
import {
  categoryTrends,
  hoursOfDay,
  planVsDone,
  trackingStreak,
  weekdayLoad,
} from './insights'
import { rangeDays } from './range'
import type { Activity, Category, Todo } from '@/types'

// 2026-09-07 is a Monday.
const MON = '2026-09-07'

const category = (id: string, name: string): Category => ({
  id, name, icon: 'briefcase', tone: 'blue', scope: 'both',
  isDefault: true, order: 10, createdAt: 0, updatedAt: 0, deletedAt: null,
})

const activity = (date: string, start: number | null, duration: number | null, cat?: string): Activity => ({
  id: Math.random().toString(),
  title: 'Something',
  categoryId: cat ?? null,
  date,
  startTime: start,
  endTime: start != null && duration != null ? start + duration : null,
  duration,
  source: 'MANUAL', todoId: null, notes: null,
  createdAt: 0, updatedAt: 0, deletedAt: null,
})

const todo = (date: string | null, status: 'OPEN' | 'COMPLETED'): Todo => ({
  id: Math.random().toString(), title: 'T', categoryId: null,
  plannedDate: date, plannedTime: null, estimatedDuration: null,
  status, notes: null, createdAt: 0, completedAt: null,
  actualDuration: null, order: 10, updatedAt: 0, deletedAt: null,
})

describe('hoursOfDay', () => {
  const days = new Set([MON])

  it('counts an entry against every hour it actually covered', () => {
    // 09:30 for 90 minutes touches 9, 10 and 11.
    const bands = hoursOfDay([activity(MON, 9 * 60 + 30, 90)], days)
    expect(bands[9].minutes).toBe(30)
    expect(bands[10].minutes).toBe(60)
    expect(bands[11].minutes).toBe(0)
    expect(bands[8].minutes).toBe(0)
  })

  it('scales the busiest hour to a full bar', () => {
    const bands = hoursOfDay([activity(MON, 9 * 60, 60), activity(MON, 14 * 60, 30)], days)
    expect(bands[9].share).toBe(1)
    expect(bands[14].share).toBe(0.5)
  })

  it('ignores days outside the window and entries with no clock time', () => {
    const bands = hoursOfDay([activity('2026-08-01', 9 * 60, 60), activity(MON, null, 60)], days)
    expect(bands.every((b) => b.minutes === 0)).toBe(true)
  })
})

describe('weekdayLoad', () => {
  it('averages over how many of each weekday the window held', () => {
    const days = rangeDays(14, '2026-09-13') // two full weeks
    const load = weekdayLoad([activity(MON, null, 120), activity('2026-09-14', null, 999)], days)
    const monday = load.find((l) => l.weekday === 1)!
    // Two Mondays in the window, only one of them tracked, and the 14th is outside it.
    expect(monday.occurrences).toBe(2)
    expect(monday.minutes).toBe(120)
    expect(monday.averageMinutes).toBe(60)
  })

  it('reports a weekday with nothing on it rather than dropping it', () => {
    const load = weekdayLoad([], rangeDays(7, MON))
    expect(load).toHaveLength(7)
    expect(load.every((l) => l.averageMinutes === 0)).toBe(true)
  })
})

describe('categoryTrends', () => {
  const cats = [category('work', 'Work'), category('gym', 'Gym')]
  const days = rangeDays(7, MON) // 2026-09-01 .. 09-07

  it('compares each category with the window immediately before', () => {
    const trends = categoryTrends(
      [activity(MON, null, 120, 'work'), activity('2026-08-31', null, 30, 'work')],
      days,
      cats,
    )
    const work = trends.find((t) => t.categoryId === 'work')!
    expect(work).toMatchObject({ minutes: 120, previousMinutes: 30, deltaMinutes: 90 })
  })

  it('keeps a habit that stopped, so a drop to zero is visible', () => {
    const trends = categoryTrends([activity('2026-08-30', null, 60, 'gym')], days, cats)
    const gym = trends.find((t) => t.categoryId === 'gym')!
    expect(gym).toMatchObject({ minutes: 0, previousMinutes: 60, deltaMinutes: -60 })
  })

  it('files uncategorised time under Other', () => {
    const trends = categoryTrends([activity(MON, null, 45)], days, cats)
    expect(trends[0]).toMatchObject({ categoryId: null, name: 'Other' })
  })
})

describe('trackingStreak', () => {
  it('counts back from today', () => {
    const a = ['2026-09-05', '2026-09-06', MON].map((d) => activity(d, null, 30))
    expect(trackingStreak(a, MON).current).toBe(3)
  })

  it('does not call today a broken streak before anything is logged', () => {
    const a = ['2026-09-05', '2026-09-06'].map((d) => activity(d, null, 30))
    expect(trackingStreak(a, MON).current).toBe(2)
  })

  it('reports the longest run even when it is over', () => {
    const a = ['2026-08-01', '2026-08-02', '2026-08-03', '2026-09-06', MON].map((d) =>
      activity(d, null, 30),
    )
    const streak = trackingStreak(a, MON)
    expect(streak.longest).toBe(3)
    expect(streak.current).toBe(2)
  })

  it('ignores days whose entries have no duration', () => {
    expect(trackingStreak([activity(MON, null, null)], MON).current).toBe(0)
  })
})

describe('planVsDone', () => {
  const days = new Set(rangeDays(7, MON))

  it('measures completion against what was planned in the window', () => {
    const result = planVsDone(
      [todo(MON, 'COMPLETED'), todo(MON, 'OPEN'), todo('2026-01-01', 'COMPLETED')],
      days,
    )
    expect(result).toMatchObject({ planned: 2, completed: 1, rate: 0.5 })
  })

  it('has no rate to report when nothing was planned', () => {
    expect(planVsDone([todo(null, 'OPEN')], days).rate).toBeNull()
  })
})
