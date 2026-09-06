import { describe, expect, it } from 'vitest'
import { rangeDays, summariseRange } from './range'
import type { Activity, Category } from '@/types'

const END = '2026-09-02'

const category = (id: string, name: string): Category => ({
  id,
  name,
  icon: 'briefcase',
  tone: 'blue',
  scope: 'both',
  isDefault: true,
  order: 10,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

const activity = (date: string, duration: number | null, categoryId?: string): Activity => ({
  id: Math.random().toString(),
  title: 'Something',
  categoryId: categoryId ?? null,
  date,
  startTime: null,
  endTime: null,
  duration,
  source: 'MANUAL',
  todoId: null,
  notes: null,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

const CATEGORIES = [category('work', 'Work'), category('health', 'Health')]

describe('rangeDays', () => {
  it('ends on the given day and runs oldest first', () => {
    expect(rangeDays(3, END)).toEqual(['2026-08-31', '2026-09-01', '2026-09-02'])
  })
})

describe('summariseRange', () => {
  const days = rangeDays(7, END)

  it('keeps empty days in the series rather than compacting them away', () => {
    const summary = summariseRange(days, [activity(END, 60, 'work')], CATEGORIES)
    expect(summary.days).toHaveLength(7)
    expect(summary.days.filter((d) => d.minutes === 0)).toHaveLength(6)
    expect(summary.activeDays).toBe(1)
  })

  it('ignores activities outside the window', () => {
    const summary = summariseRange(
      days,
      [activity('2026-08-01', 120, 'work'), activity(END, 30, 'work')],
      CATEGORIES,
    )
    expect(summary.totalMinutes).toBe(30)
  })

  it('scales the tallest column to full height', () => {
    const summary = summariseRange(
      days,
      [activity(END, 120, 'work'), activity('2026-09-01', 60, 'work')],
      CATEGORIES,
    )
    const byDay = new Map(summary.days.map((d) => [d.day, d]))
    expect(byDay.get(END)!.height).toBe(1)
    expect(byDay.get('2026-09-01')!.height).toBe(0.5)
  })

  it('averages over every day in the window, not just the active ones', () => {
    const summary = summariseRange(days, [activity(END, 70, 'work')], CATEGORIES)
    expect(summary.dailyAverage).toBe(10)
  })

  it('ranks categories by time and gives each a share of the total', () => {
    const summary = summariseRange(
      days,
      [activity(END, 60, 'health'), activity(END, 180, 'work')],
      CATEGORIES,
    )
    expect(summary.byCategory.map((c) => c.name)).toEqual(['Work', 'Health'])
    expect(summary.byCategory[0].share).toBeCloseTo(0.75)
  })

  it('names the busiest day, and none when nothing was tracked', () => {
    expect(summariseRange(days, [], CATEGORIES).busiest).toBeNull()
    const summary = summariseRange(
      days,
      [activity('2026-08-30', 200, 'work'), activity(END, 45, 'work')],
      CATEGORIES,
    )
    expect(summary.busiest?.day).toBe('2026-08-30')
  })

  it('caps a single day at 24 hours so one bad entry cannot flatten the chart', () => {
    const summary = summariseRange(
      days,
      [activity('2026-08-30', 5000, 'work'), activity(END, 720, 'work')],
      CATEGORIES,
    )
    const byDay = new Map(summary.days.map((d) => [d.day, d]))
    expect(byDay.get('2026-08-30')!.height).toBe(1)
    expect(byDay.get(END)!.height).toBe(0.5)
  })

  it('files uncategorised time under Other', () => {
    const summary = summariseRange(days, [activity(END, 30)], CATEGORIES)
    expect(summary.byCategory[0]).toMatchObject({ name: 'Other', categoryId: null })
  })
})
