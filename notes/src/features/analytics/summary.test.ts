import { describe, expect, it } from 'vitest'
import { blockAt, categoryShares, daySchedule, summariseDay, MINUTES_IN_DAY } from './summary'
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

describe('daySchedule', () => {
  it('places a block at the hour it happened, not against the left edge', () => {
    const [block] = daySchedule(
      [activity({ categoryId: 'work', startTime: 13 * 60, duration: 60 })],
      CATEGORIES,
    ).blocks
    expect(block).toMatchObject({ start: 780, end: 840, minutes: 60, name: 'Work' })
  })

  it('derives the missing end, and the missing start, from the duration', () => {
    const fromStart = daySchedule([activity({ startTime: 9 * 60, duration: 30 })], CATEGORIES)
    expect(fromStart.blocks[0]).toMatchObject({ start: 540, end: 570 })

    const fromEnd = daySchedule([activity({ endTime: 9 * 60, duration: 30 })], CATEGORIES)
    expect(fromEnd.blocks[0]).toMatchObject({ start: 510, end: 540 })
  })

  it('orders blocks by start, longest first, so a nested entry paints last', () => {
    const schedule = daySchedule(
      [
        activity({ id: 'short', startTime: 600, duration: 30 }),
        activity({ id: 'long', startTime: 600, duration: 180 }),
        activity({ id: 'early', startTime: 60, duration: 30 }),
      ],
      CATEGORIES,
    )
    expect(schedule.blocks.map((b) => b.id)).toEqual(['early', 'long', 'short'])
  })

  it('counts overlapping time once when measuring the day', () => {
    const schedule = daySchedule(
      [
        activity({ startTime: 600, duration: 120 }),
        activity({ startTime: 660, duration: 120 }),
      ],
      CATEGORIES,
    )
    expect(schedule.coveredMinutes).toBe(180)
    expect(schedule.untrackedMinutes).toBe(MINUTES_IN_DAY - 180)
  })

  it('truncates an entry that runs past midnight rather than wrapping it', () => {
    const schedule = daySchedule([activity({ startTime: 23 * 60, duration: 180 })], CATEGORIES)
    expect(schedule.blocks[0]).toMatchObject({ start: 1380, end: MINUTES_IN_DAY, minutes: 60 })
  })

  it('holds timeless tracked minutes aside instead of guessing an hour', () => {
    const schedule = daySchedule([activity({ duration: 45 })], CATEGORIES)
    expect(schedule.blocks).toEqual([])
    expect(schedule.unplacedMinutes).toBe(45)
    expect(schedule.untrackedMinutes).toBe(MINUTES_IN_DAY - 45)
  })

  it('draws no block for a moment with no length', () => {
    expect(daySchedule([activity({ startTime: 600 })], CATEGORIES).blocks).toEqual([])
  })

  it('reports a whole empty day as entirely untracked', () => {
    const schedule = daySchedule([], CATEGORIES)
    expect(schedule.blocks).toEqual([])
    expect(schedule.untrackedMinutes).toBe(MINUTES_IN_DAY)
  })

  it('never reports negative untracked time when a day is over-logged', () => {
    const schedule = daySchedule(
      [activity({ startTime: 0, duration: 1400 }), activity({ duration: 500 })],
      CATEGORIES,
    )
    expect(schedule.untrackedMinutes).toBe(0)
  })

  it('labels an activity with no category as Other', () => {
    const schedule = daySchedule([activity({ startTime: 600, duration: 60 })], CATEGORIES)
    expect(schedule.blocks[0]).toMatchObject({ name: 'Other', tone: 'slate', categoryId: null })
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

describe('blockAt', () => {
  const blocks = (...spans: Array<[number, number, string?]>) =>
    daySchedule(
      spans.map(([start, end, id]) =>
        activity({ id: id ?? `${start}`, categoryId: 'work', startTime: start, duration: end - start }),
      ),
      CATEGORIES,
    ).blocks

  it('names the entry under the minute', () => {
    expect(blockAt(blocks([600, 660, 'a']), 620)).toMatchObject({ id: 'a' })
  })

  it('answers with the entry painted on top where two overlap', () => {
    // daySchedule paints the longer one first, so the nested short one is what
    // the eye actually sees at that minute.
    expect(blockAt(blocks([600, 780, 'long'], [620, 640, 'short']), 630)).toMatchObject({
      id: 'short',
    })
  })

  it('treats an entry as covering its start but not its end', () => {
    const b = blocks([600, 660, 'a'])
    expect(blockAt(b, 600)).not.toBeNull()
    expect(blockAt(b, 660)).toBeNull()
  })

  it('finds nothing in the gaps, and nothing on an empty day', () => {
    expect(blockAt(blocks([540, 600], [780, 840]), 700)).toBeNull()
    expect(blockAt([], 700)).toBeNull()
  })

  it('clamps a minute past the end of the day back into it', () => {
    expect(blockAt(blocks([1400, 1440, 'late']), MINUTES_IN_DAY)).toMatchObject({ id: 'late' })
  })
})
