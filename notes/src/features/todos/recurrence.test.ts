import { describe, expect, it } from 'vitest'
import {
  describeRecurrence,
  occurrenceAfter,
  occurrenceOnOrAfter,
  shortRecurrence,
  weekdayOf,
  weekdayOnOrAfter,
  weekdaysOf,
} from './recurrence'
import type { Weekday } from '@/types'

// 2026-09-07 is a Monday.
const MON = '2026-09-07'
const TUE = '2026-09-08'
const WED = '2026-09-09'
const SUN = '2026-09-13'

const weekly = (...weekdays: Weekday[]) => ({ kind: 'weekly' as const, weekdays })

describe('weekdaysOf', () => {
  it('puts the days in week order however they were tapped', () => {
    expect(weekdaysOf(weekly(5, 1, 3))).toEqual([1, 3, 5])
  })

  it('drops a day chosen twice', () => {
    expect(weekdaysOf(weekly(1, 1, 3))).toEqual([1, 3])
  })
})

describe('weekdayOnOrAfter', () => {
  it('returns the day itself when it already matches', () => {
    expect(weekdayOnOrAfter(MON, 1)).toBe(MON)
  })

  it('crosses the week boundary rather than going backwards', () => {
    // Sunday is index 0, which is numerically behind Monday's 1.
    expect(weekdayOnOrAfter(MON, 0)).toBe(SUN)
  })

  it('agrees with the weekday it reads back', () => {
    for (let w = 0; w < 7; w++) {
      expect(weekdayOf(weekdayOnOrAfter(MON, w as Weekday))).toBe(w)
    }
  })
})

describe('occurrenceOnOrAfter', () => {
  it('leaves a daily task where it is', () => {
    expect(occurrenceOnOrAfter({ kind: 'daily' }, MON)).toBe(MON)
  })

  it('picks the soonest of the chosen days, not the first in the week', () => {
    // Asked on Tuesday, a Mon/Wed/Fri task is due Wednesday — not next Monday.
    expect(occurrenceOnOrAfter(weekly(1, 3, 5), TUE)).toBe(WED)
  })

  it('catches a stale task up to its next real day', () => {
    expect(occurrenceOnOrAfter(weekly(5), MON)).toBe('2026-09-11')
  })

  it('does not stall on a weekly repeat with no days left selected', () => {
    expect(occurrenceOnOrAfter(weekly(), MON)).toBe(MON)
  })
})

describe('occurrenceAfter', () => {
  it('moves a daily task on by one day', () => {
    expect(occurrenceAfter({ kind: 'daily' }, MON)).toBe(TUE)
  })

  it('moves to the next chosen day, never staying on the same one', () => {
    expect(occurrenceAfter(weekly(1, 3, 5), MON)).toBe(WED)
    expect(occurrenceAfter(weekly(1), MON)).toBe('2026-09-14')
  })

  it('anchors to the day that was due, not to a different weekday', () => {
    // A Monday series ticked while sitting on Sunday still lands on Monday.
    expect(occurrenceAfter(weekly(1), SUN)).toBe('2026-09-14')
  })
})

describe('labels', () => {
  it('names a single day and a plain set', () => {
    expect(describeRecurrence(weekly(1))).toBe('Every Monday')
    expect(shortRecurrence(weekly(1))).toBe('Mondays')
    expect(describeRecurrence(weekly(1, 3, 5))).toBe('Mon, Wed & Fri')
    expect(shortRecurrence(weekly(1, 3, 5))).toBe('Mon, Wed, Fri')
  })

  it('recognises the sets people actually mean', () => {
    expect(describeRecurrence(weekly(1, 2, 3, 4, 5))).toBe('Every weekday')
    expect(shortRecurrence(weekly(0, 6))).toBe('Weekends')
  })

  it('calls all seven days what it is', () => {
    expect(describeRecurrence(weekly(0, 1, 2, 3, 4, 5, 6))).toBe('Every day')
    expect(shortRecurrence({ kind: 'daily' })).toBe('Daily')
  })
})
