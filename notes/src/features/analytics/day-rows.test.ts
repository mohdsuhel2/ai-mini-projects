import { describe, expect, it } from 'vitest'
import { dayRows, GAP_THRESHOLD_MINUTES } from './day-rows'
import type { Activity } from '@/types'

const activity = (partial: Partial<Activity>): Activity => ({
  id: partial.id ?? Math.random().toString(),
  title: partial.title ?? 'Something',
  categoryId: null,
  date: '2026-09-02',
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

const kinds = (rows: ReturnType<typeof dayRows>['rows']) => rows.map((r) => r.kind)

describe('dayRows', () => {
  it('orders entries by when they started', () => {
    const { rows } = dayRows([
      activity({ id: 'late', startTime: 900, duration: 30 }),
      activity({ id: 'early', startTime: 600, duration: 30 }),
    ])
    expect(rows.filter((r) => r.kind === 'entry').map((r) => r.activity.id)).toEqual([
      'early',
      'late',
    ])
  })

  it('collapses a long empty stretch into one labelled gap', () => {
    const { rows } = dayRows([
      activity({ startTime: 540, duration: 60 }), // 9:00–10:00
      activity({ startTime: 780, duration: 30 }), // 13:00–13:30
    ])
    expect(kinds(rows)).toEqual(['entry', 'gap', 'entry'])
    const gap = rows[1]
    expect(gap).toMatchObject({ kind: 'gap', minutes: 180, from: 600, to: 780 })
  })

  it('leaves a seam between back-to-back entries alone', () => {
    const { rows } = dayRows([
      activity({ startTime: 540, duration: 60 }),
      activity({ startTime: 605, duration: 30 }), // 5 minutes later
    ])
    expect(kinds(rows)).toEqual(['entry', 'entry'])
  })

  it('draws a gap exactly at the threshold, not one minute under it', () => {
    const under = dayRows([
      activity({ startTime: 540, duration: 60 }),
      activity({ startTime: 600 + GAP_THRESHOLD_MINUTES - 1, duration: 10 }),
    ])
    const at = dayRows([
      activity({ startTime: 540, duration: 60 }),
      activity({ startTime: 600 + GAP_THRESHOLD_MINUTES, duration: 10 }),
    ])
    expect(kinds(under.rows)).toEqual(['entry', 'entry'])
    expect(kinds(at.rows)).toEqual(['entry', 'gap', 'entry'])
  })

  it('never invents a gap after an entry another one overlapped', () => {
    // A long block, a short one nested inside it, then a resume within the long
    // block's reach: the short entry's end must not become the day's cursor.
    const { rows } = dayRows([
      activity({ id: 'long', startTime: 540, duration: 240 }), // 9:00–13:00
      activity({ id: 'short', startTime: 560, duration: 20 }), // 9:20–9:40
      activity({ id: 'after', startTime: 790, duration: 30 }), // 13:10
    ])
    expect(kinds(rows)).toEqual(['entry', 'entry', 'entry'])
  })

  it('sets no gap before the first entry of the day', () => {
    const { rows } = dayRows([activity({ startTime: 800, duration: 30 })])
    expect(kinds(rows)).toEqual(['entry'])
  })

  it('gives a moment with no length a place but no height', () => {
    const { rows } = dayRows([activity({ startTime: 600 })])
    expect(rows[0]).toMatchObject({ kind: 'entry', at: 600, end: null, minutes: 0 })
  })

  it('holds entries with no clock time under the day rather than guessing one', () => {
    const { rows, untimed } = dayRows([
      activity({ id: 'placed', startTime: 600, duration: 30 }),
      activity({ id: 'loose', duration: 45 }),
    ])
    expect(rows).toHaveLength(1)
    expect(untimed.map((a) => a.id)).toEqual(['loose'])
  })

  it('returns nothing for a day with nothing on it', () => {
    expect(dayRows([])).toEqual({ rows: [], untimed: [] })
  })
})
