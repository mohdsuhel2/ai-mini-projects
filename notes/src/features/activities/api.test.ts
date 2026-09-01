import { describe, expect, it } from 'vitest'
import { lastEndOfDay, suggestedStartTime } from './api'
import type { Activity } from '@/types'

const activity = (partial: Partial<Activity>): Activity => ({
  id: partial.id ?? Math.random().toString(),
  title: 'x',
  categoryId: null,
  date: '2026-09-01',
  startTime: partial.startTime ?? null,
  endTime: partial.endTime ?? null,
  duration: partial.duration ?? null,
  source: 'MANUAL',
  todoId: null,
  notes: null,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

const NOON = 12 * 60

describe('lastEndOfDay', () => {
  it('is null for a day with nothing on it', () => {
    expect(lastEndOfDay([])).toBeNull()
  })

  it('takes the latest end time', () => {
    expect(
      lastEndOfDay([
        activity({ startTime: 8 * 60, endTime: 8 * 60 + 30 }),
        activity({ startTime: 16 * 60, endTime: 16 * 60 + 40 }),
        activity({ startTime: 12 * 60, endTime: 12 * 60 + 45 }),
      ]),
    ).toBe(16 * 60 + 40)
  })

  it('derives an end from a start plus a duration', () => {
    expect(lastEndOfDay([activity({ startTime: 9 * 60, duration: 90 })])).toBe(10 * 60 + 30)
  })

  it('ignores entries with no time at all', () => {
    expect(lastEndOfDay([activity({ duration: 30 })])).toBeNull()
  })
})

describe('suggestedStartTime', () => {
  it('picks up where the previous activity finished', () => {
    const activities = [activity({ startTime: 9 * 60, endTime: 10 * 60 + 30 })]
    expect(suggestedStartTime(activities, 45, NOON)).toBe(10 * 60 + 30)
  })

  it('ends now when the day is still empty', () => {
    expect(suggestedStartTime([], 30, NOON)).toBe(NOON - 30)
  })

  it('never suggests a time before midnight', () => {
    expect(suggestedStartTime([], 120, 30)).toBe(0)
  })

  it('never lets an entry run past the end of the day', () => {
    const activities = [activity({ startTime: 23 * 60, endTime: 23 * 60 + 55 })]
    const start = suggestedStartTime(activities, 60, NOON)
    expect(start + 60).toBeLessThanOrEqual(24 * 60 - 1)
  })

  it('chains repeatedly, so logging several in a row needs no arithmetic', () => {
    let activities: Activity[] = []
    const first = suggestedStartTime(activities, 30, NOON)
    activities = [activity({ startTime: first, endTime: first + 30 })]
    expect(suggestedStartTime(activities, 45, NOON)).toBe(first + 30)
  })
})
