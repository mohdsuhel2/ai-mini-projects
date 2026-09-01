import { describe, expect, it } from 'vitest'
import { parseNaturalInput } from './natural-input'

// A fixed Tuesday so weekday and weekend maths are deterministic.
const NOW = new Date(2026, 8, 1, 14, 30, 0) // Tue 1 Sep 2026, 2:30 PM

const parse = (input: string) => parseNaturalInput(input, NOW)

describe('title extraction', () => {
  it('leaves a plain title untouched', () => {
    const r = parse('Buy groceries')
    expect(r.title).toBe('Buy groceries')
    expect(r.dayKey).toBeNull()
    expect(r.durationMinutes).toBeNull()
    expect(r.minuteOfDay).toBeNull()
  })

  it('preserves the original casing of the remaining title', () => {
    expect(parse('Call Dr Mehta tomorrow').title).toBe('Call Dr Mehta')
  })

  it('collapses whitespace left behind by removed tokens', () => {
    expect(parse('Review   PR   tomorrow  at 9am').title).toBe('Review PR')
  })

  it('trims dangling connectives', () => {
    expect(parse('Worked on the deck for 2h').title).toBe('Worked on the deck')
  })

  it('returns an empty title when the input is only metadata', () => {
    expect(parse('tomorrow at 5pm').title).toBe('')
  })
})

describe('durations', () => {
  it.each([
    ['Watched YouTube for 1 hour', 60, 'Watched YouTube'],
    ['Instagram for 30 minutes', 30, 'Instagram'],
    ['Standup 15m', 15, 'Standup'],
    ['Deep work 2h', 120, 'Deep work'],
    ['Gym 1h30m', 90, 'Gym'],
    ['Lunch 45 min', 45, 'Lunch'],
    ['Reading for 2 hours 15 minutes', 135, 'Reading'],
    ['Nap for half an hour', 30, 'Nap'],
    ['Call for an hour', 60, 'Call'],
    ['Commute 90 mins', 90, 'Commute'],
    ['Workout 1 hr', 60, 'Workout'],
  ])('parses %s', (input, minutes, title) => {
    const r = parse(input)
    expect(r.durationMinutes).toBe(minutes)
    expect(r.title).toBe(title)
  })

  it('does not treat a bare number as a duration', () => {
    const r = parse('Read 20 pages')
    expect(r.durationMinutes).toBeNull()
    expect(r.title).toBe('Read 20 pages')
  })

  it('ignores an implausible duration', () => {
    const r = parse('Sprint 400m')
    expect(r.durationMinutes).toBeNull()
    expect(r.title).toBe('Sprint 400m')
  })
})

describe('dates', () => {
  it('resolves today', () => {
    expect(parse('Pay rent today').dayKey).toBe('2026-09-01')
  })

  it('resolves tomorrow', () => {
    expect(parse('Buy groceries tomorrow').dayKey).toBe('2026-09-02')
  })

  it('resolves tonight to today', () => {
    expect(parse('Laundry tonight').dayKey).toBe('2026-09-01')
  })

  it('resolves this weekend to the coming Saturday', () => {
    expect(parse('Clean the garage this weekend').dayKey).toBe('2026-09-05')
  })

  it('resolves a weekday name to the next such day', () => {
    // Tuesday 1 Sep -> next Friday is 4 Sep.
    expect(parse('Submit report friday').dayKey).toBe('2026-09-04')
  })

  it('resolves a weekday that matches today to next week', () => {
    expect(parse('Team sync tuesday').dayKey).toBe('2026-09-08')
  })

  it('resolves next monday', () => {
    expect(parse('Kickoff next monday').dayKey).toBe('2026-09-07')
  })

  it('does not match a weekday inside a longer word', () => {
    const r = parse('Order sunglasses')
    expect(r.dayKey).toBeNull()
    expect(r.title).toBe('Order sunglasses')
  })
})

describe('times', () => {
  it.each([
    ['Gym at 6 PM', 18 * 60, 'Gym'],
    ['Standup at 9:30am', 9 * 60 + 30, 'Standup'],
    ['Meeting 3pm', 15 * 60, 'Meeting'],
    ['Deploy at 18:00', 18 * 60, 'Deploy'],
    ['Lunch at noon', 12 * 60, 'Lunch'],
    ['Sleep at midnight', 0, 'Sleep'],
  ])('parses %s', (input, minute, title) => {
    const r = parse(input)
    expect(r.minuteOfDay).toBe(minute)
    expect(r.title).toBe(title)
  })

  it('reads a bare afternoon hour after "at" as PM', () => {
    expect(parse('Call John at 5').minuteOfDay).toBe(17 * 60)
  })

  it('reads a bare morning hour after "at" as AM', () => {
    expect(parse('Breakfast at 9').minuteOfDay).toBe(9 * 60)
  })

  it('requires "at" before a bare hour', () => {
    const r = parse('Buy 5 apples')
    expect(r.minuteOfDay).toBeNull()
    expect(r.title).toBe('Buy 5 apples')
  })
})

describe('combinations', () => {
  it('parses a date and a time together', () => {
    const r = parse('Dentist tomorrow at 4:15pm')
    expect(r.dayKey).toBe('2026-09-02')
    expect(r.minuteOfDay).toBe(16 * 60 + 15)
    expect(r.title).toBe('Dentist')
  })

  it('parses a duration and a date together', () => {
    const r = parse('Review designs tomorrow for 45m')
    expect(r.dayKey).toBe('2026-09-02')
    expect(r.durationMinutes).toBe(45)
    expect(r.title).toBe('Review designs')
  })
})

describe('activity inference', () => {
  it('suggests an activity for past-tense phrasing', () => {
    expect(parse('Watched YouTube for 1 hour').suggestsActivity).toBe(true)
    expect(parse('Went for a run').suggestsActivity).toBe(true)
  })

  it('suggests an activity for a bare duration with no future date', () => {
    expect(parse('Instagram 30m').suggestsActivity).toBe(true)
  })

  it('does not suggest an activity when a future date is present', () => {
    expect(parse('Workshop tomorrow for 2h').suggestsActivity).toBe(false)
  })

  it('does not suggest an activity for a plain imperative', () => {
    expect(parse('Buy groceries').suggestsActivity).toBe(false)
    expect(parse('Call the bank at 11am').suggestsActivity).toBe(false)
  })
})

describe('tokens', () => {
  it('reports what it consumed so the UI can show a preview', () => {
    const r = parse('Dentist tomorrow at 4pm for 30m')
    expect(r.tokens.map((t) => t.kind).sort()).toEqual(['date', 'duration', 'time'])
    expect(r.tokens.find((t) => t.kind === 'date')?.label).toBe('Tomorrow')
    expect(r.tokens.find((t) => t.kind === 'time')?.label).toBe('4:00 PM')
    expect(r.tokens.find((t) => t.kind === 'duration')?.label).toBe('30m')
  })

  it('reports no tokens for a plain title', () => {
    expect(parse('Buy groceries').tokens).toEqual([])
  })
})
