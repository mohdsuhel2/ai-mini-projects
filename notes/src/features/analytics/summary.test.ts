import { describe, expect, it } from 'vitest'
import { categoryShares, dayFill, summariseDay, MINUTES_IN_DAY } from './summary'
import type { Activity, Category, Todo } from '@/types'

const DAY = '2026-09-01'

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

const activity = (partial: Partial<Activity>): Activity => ({
  id: partial.id ?? Math.random().toString(),
  title: partial.title ?? 'Something',
  categoryId: partial.categoryId ?? null,
  date: partial.date ?? DAY,
  startTime: partial.startTime ?? null,
  endTime: partial.endTime ?? null,
  duration: partial.duration ?? null,
  source: partial.source ?? 'MANUAL',
  todoId: partial.todoId ?? null,
  notes: null,
  createdAt: partial.createdAt ?? 0,
  updatedAt: 0,
  deletedAt: null,
})

const todo = (partial: Partial<Todo>): Todo => ({
  id: partial.id ?? Math.random().toString(),
  title: partial.title ?? 'Task',
  categoryId: null,
  plannedDate: partial.plannedDate ?? DAY,
  plannedTime: null,
  estimatedDuration: null,
  status: partial.status ?? 'OPEN',
  notes: null,
  createdAt: 0,
  completedAt: null,
  actualDuration: null,
  order: 10,
  updatedAt: 0,
  deletedAt: null,
})

const CATEGORIES = [category('work', 'Work'), category('health', 'Health')]

describe('summariseDay', () => {
  it('reports an empty day without inventing numbers', () => {
    const summary = summariseDay(DAY, [], [], CATEGORIES)
    expect(summary).toMatchObject({
      completedCount: 0,
      openCount: 0,
      trackedMinutes: 0,
      activityCount: 0,
      byCategory: [],
    })
  })

  it('totals minutes per category', () => {
    const summary = summariseDay(
      DAY,
      [],
      [
        activity({ categoryId: 'work', duration: 120 }),
        activity({ categoryId: 'work', duration: 60 }),
        activity({ categoryId: 'health', duration: 40 }),
      ],
      CATEGORIES,
    )
    expect(summary.trackedMinutes).toBe(220)
    expect(summary.byCategory.map((c) => [c.name, c.minutes])).toEqual([
      ['Work', 180],
      ['Health', 40],
    ])
  })

  it('groups uncategorised time under Other', () => {
    const summary = summariseDay(DAY, [], [activity({ duration: 30 })], CATEGORIES)
    expect(summary.byCategory).toHaveLength(1)
    expect(summary.byCategory[0]).toMatchObject({ name: 'Other', categoryId: null, minutes: 30 })
  })

  it('counts a durationless activity without adding a category slice', () => {
    const summary = summariseDay(DAY, [], [activity({ categoryId: 'work' })], CATEGORIES)
    expect(summary.activityCount).toBe(1)
    expect(summary.trackedMinutes).toBe(0)
    expect(summary.byCategory).toEqual([])
  })

  it('counts todos for the requested day only', () => {
    const summary = summariseDay(
      DAY,
      [
        todo({ status: 'COMPLETED' }),
        todo({ status: 'OPEN' }),
        todo({ status: 'OPEN', plannedDate: '2026-09-02' }),
      ],
      [],
      CATEGORIES,
    )
    expect(summary.completedCount).toBe(1)
    expect(summary.openCount).toBe(1)
  })
})

describe('dayFill', () => {
  it('measures against the whole 24 hours, not against tracked time', () => {
    const summary = summariseDay(DAY, [], [activity({ categoryId: 'work', duration: 360 })], CATEGORIES)
    const fill = dayFill(summary)
    expect(fill.segments[0].share).toBeCloseTo(360 / MINUTES_IN_DAY)
    expect(fill.untrackedMinutes).toBe(MINUTES_IN_DAY - 360)
  })

  it('reports a whole empty day as entirely untracked', () => {
    const fill = dayFill(summariseDay(DAY, [], [], CATEGORIES))
    expect(fill.segments).toEqual([])
    expect(fill.untrackedShare).toBe(1)
  })

  it('shares plus untracked always total one', () => {
    const summary = summariseDay(
      DAY,
      [],
      [
        activity({ categoryId: 'work', duration: 300 }),
        activity({ categoryId: 'health', duration: 90 }),
      ],
      CATEGORIES,
    )
    const fill = dayFill(summary)
    const total = fill.segments.reduce((sum, s) => sum + s.share, 0) + fill.untrackedShare
    expect(total).toBeCloseTo(1)
  })

  it('never reports negative untracked time when a day is over-logged', () => {
    const summary = summariseDay(
      DAY,
      [],
      [activity({ categoryId: 'work', duration: 2000 })],
      CATEGORIES,
    )
    expect(dayFill(summary).untrackedMinutes).toBe(0)
  })
})

describe('categoryShares', () => {
  it('returns fractions that sum to one', () => {
    const summary = summariseDay(
      DAY,
      [],
      [
        activity({ categoryId: 'work', duration: 180 }),
        activity({ categoryId: 'health', duration: 60 }),
      ],
      CATEGORIES,
    )
    const shares = categoryShares(summary)
    expect(shares.map((s) => s.share)).toEqual([0.75, 0.25])
    expect(shares.reduce((sum, s) => sum + s.share, 0)).toBeCloseTo(1)
  })

  it('returns nothing when no time was tracked', () => {
    expect(categoryShares(summariseDay(DAY, [], [], CATEGORIES))).toEqual([])
  })
})
