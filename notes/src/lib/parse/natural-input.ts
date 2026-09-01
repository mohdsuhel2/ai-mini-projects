import type { DayKey, MinuteOfDay } from '@/types'
import { shiftDay, todayKey, toDayKey, weekendKey } from '@/lib/date/day-key'
import { formatClock, formatDayLabel, formatDuration } from '@/lib/date/format'

/**
 * A small, deterministic parser for the quick-add field. No AI, no network.
 *
 * It only claims a fragment when the fragment is unambiguous, because a wrong
 * guess is worse than no guess: whatever it consumes disappears from the title.
 * Everything it did claim is reported back as a token so the UI can show the
 * user what happened before they commit.
 */

export type TokenKind = 'date' | 'time' | 'duration'

export interface ParsedToken {
  kind: TokenKind
  /** The literal text taken out of the input. */
  text: string
  /** Human-readable rendering of the resolved value. */
  label: string
}

export interface ParseResult {
  title: string
  dayKey: DayKey | null
  minuteOfDay: MinuteOfDay | null
  durationMinutes: number | null
  tokens: ParsedToken[]
  /** True when the phrasing reads as something already done. */
  suggestsActivity: boolean
}

/**
 * A bare `Nm` above this is almost certainly not minutes — "Sprint 400m" is
 * metres, "Read 20 pages" is pages. Five hours is a generous ceiling for a
 * duration someone bothers to write without an hours unit.
 */
const MAX_BARE_MINUTES = 300

const WEEKDAYS: Array<{ index: number; pattern: string }> = [
  { index: 0, pattern: 'sunday' },
  { index: 1, pattern: 'monday|mon' },
  { index: 2, pattern: 'tuesday|tues|tue' },
  { index: 3, pattern: 'wednesday|weds|wed' },
  { index: 4, pattern: 'thursday|thurs|thur|thu' },
  { index: 5, pattern: 'friday|fri' },
  { index: 6, pattern: 'saturday' },
]
// `sat` and `sun` are deliberately absent as abbreviations: both are ordinary
// English words ("sat down", "sun deck") and a false match silently eats them.

const PAST_TENSE = [
  'watched', 'went', 'did', 'had', 'ate', 'drank', 'slept', 'ran', 'jogged',
  'walked', 'played', 'worked', 'wrote', 'studied', 'called', 'attended',
  'finished', 'completed', 'cleaned', 'cooked', 'browsed', 'scrolled', 'spent',
  'met', 'drove', 'travelled', 'traveled', 'listened', 'practised', 'practiced',
  'shopped', 'rested', 'relaxed', 'exercised', 'stretched', 'commuted', 'napped',
]
const PAST_TENSE_RE = new RegExp(`\\b(?:${PAST_TENSE.join('|')})\\b`, 'i')

/** Tracks which characters of the input have been claimed by a matcher. */
class Consumer {
  private readonly claimed: boolean[]

  constructor(private readonly input: string) {
    this.claimed = new Array(input.length).fill(false)
  }

  /** The input with claimed characters blanked, so matchers never overlap. */
  view(): string {
    let out = ''
    for (let i = 0; i < this.input.length; i += 1) {
      out += this.claimed[i] ? ' ' : this.input[i]
    }
    return out
  }

  take(start: number, end: number): void {
    for (let i = start; i < end; i += 1) this.claimed[i] = true
  }

  remainder(): string {
    let out = ''
    for (let i = 0; i < this.input.length; i += 1) {
      if (!this.claimed[i]) out += this.input[i]
    }
    return out
  }
}

function cleanTitle(raw: string): string {
  let title = raw.replace(/\s+/g, ' ').trim()
  // Strip connectives left dangling by a removed fragment ("… for", "… at").
  let previous: string
  do {
    previous = title
    title = title
      .replace(/^[\s,;:\-–—]+/, '')
      .replace(/[\s,;:\-–—]+$/, '')
      .replace(/\s+\b(?:for|at|on|by|from|around|about)\s*$/i, '')
      .replace(/^\s*\b(?:for|at|on|by|from)\b\s+/i, '')
  } while (title !== previous)
  return title.trim()
}

function matchDuration(consumer: Consumer): { minutes: number; text: string } | null {
  const view = consumer.view()

  const patterns: Array<{ re: RegExp; minutes: (m: RegExpExecArray) => number | null }> = [
    {
      re: /\b(?:for\s+)?half\s+an\s+hour\b/i,
      minutes: () => 30,
    },
    {
      re: /\b(?:for\s+)?(?:an|a)\s+(?:hour|hr)\b/i,
      minutes: () => 60,
    },
    {
      // "2h", "1 hr", "2 hours 15 minutes", "1h30m"
      // Units are ordered longest-first, and `(?![a-z])` rather than `\b`, so
      // the glued form \"1h30m\" matches as readily as \"1 hour 30 minutes\".
      re: /\b(?:for\s+)?(\d{1,2})\s*(?:hours|hour|hrs|hr|h)(?![a-z])(?:\s*(?:and\s+)?(\d{1,3})\s*(?:minutes|minute|mins|min|m)(?![a-z]))?/i,
      minutes: (m) => {
        const hours = Number(m[1])
        const mins = m[2] ? Number(m[2]) : 0
        if (hours < 1 || hours > 24 || mins > 59) return null
        return hours * 60 + mins
      },
    },
    {
      // "45m", "30 min", "90 mins"
      re: /\b(?:for\s+)?(\d{1,3})\s*(?:minutes|minute|mins|min|m)(?![a-z])/i,
      minutes: (m) => {
        const value = Number(m[1])
        if (value < 1 || value > MAX_BARE_MINUTES) return null
        return value
      },
    },
  ]

  for (const { re, minutes } of patterns) {
    const match = re.exec(view)
    if (!match) continue
    const value = minutes(match)
    if (value == null) continue
    consumer.take(match.index, match.index + match[0].length)
    return { minutes: value, text: match[0].trim() }
  }
  return null
}

/**
 * A bare hour has no meridiem to disambiguate it. The band below reads 1–6 as
 * afternoon and 7–11 as morning, which matches how people actually schedule:
 * "at 5" is teatime, "at 9" is the morning standup.
 */
function resolveBareHour(hour: number): number | null {
  if (hour === 0) return 0
  if (hour === 12) return 12
  if (hour >= 1 && hour <= 6) return hour + 12
  if (hour >= 7 && hour <= 11) return hour
  if (hour >= 13 && hour <= 23) return hour
  return null
}

function matchTime(consumer: Consumer): { minute: MinuteOfDay; text: string } | null {
  const view = consumer.view()

  const noon = /\b(?:at\s+)?(noon|midday|midnight)\b/i.exec(view)
  if (noon) {
    consumer.take(noon.index, noon.index + noon[0].length)
    return { minute: noon[1].toLowerCase() === 'midnight' ? 0 : 12 * 60, text: noon[0].trim() }
  }

  // "9:30am", "at 18:00"
  const withMinutes = /\b(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?/i.exec(view)
  if (withMinutes) {
    let hour = Number(withMinutes[1])
    const minute = Number(withMinutes[2])
    const meridiem = withMinutes[3]?.toLowerCase().replace(/\./g, '')
    if (minute <= 59) {
      if (meridiem === 'pm' && hour < 12) hour += 12
      else if (meridiem === 'am' && hour === 12) hour = 0
      if (hour <= 23) {
        consumer.take(withMinutes.index, withMinutes.index + withMinutes[0].length)
        return { minute: hour * 60 + minute, text: withMinutes[0].trim() }
      }
    }
  }

  // "6 PM", "at 3pm"
  const withMeridiem = /\b(?:at\s+)?(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)\b/i.exec(view)
  if (withMeridiem) {
    let hour = Number(withMeridiem[1])
    const meridiem = withMeridiem[2].toLowerCase().replace(/\./g, '')
    if (hour >= 1 && hour <= 12) {
      if (meridiem === 'pm' && hour < 12) hour += 12
      else if (meridiem === 'am' && hour === 12) hour = 0
      consumer.take(withMeridiem.index, withMeridiem.index + withMeridiem[0].length)
      return { minute: hour * 60, text: withMeridiem[0].trim() }
    }
  }

  // A bare hour, only when "at" makes the intent explicit.
  const bare = /\bat\s+(\d{1,2})\b(?!\s*[:.]?\d)/i.exec(view)
  if (bare) {
    const hour = resolveBareHour(Number(bare[1]))
    if (hour != null) {
      consumer.take(bare.index, bare.index + bare[0].length)
      return { minute: hour * 60, text: bare[0].trim() }
    }
  }

  return null
}

function nextWeekday(target: number, now: Date): DayKey {
  const current = now.getDay()
  // Always strictly ahead: "tuesday" said on a Tuesday means the next one.
  const delta = ((target - current + 7) % 7) || 7
  return toDayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() + delta))
}

function matchDate(consumer: Consumer, now: Date): { day: DayKey; text: string } | null {
  const view = consumer.view()
  const today = todayKey(now)

  const simple: Array<{ re: RegExp; day: () => DayKey }> = [
    { re: /\b(?:the\s+)?day\s+after\s+tomorrow\b/i, day: () => shiftDay(today, 2) },
    { re: /\btomorrow\b|\btmrw\b|\btmw\b/i, day: () => shiftDay(today, 1) },
    { re: /\byesterday\b/i, day: () => shiftDay(today, -1) },
    { re: /\bthis\s+weekend\b|\bweekend\b/i, day: () => weekendKey(now) },
    { re: /\btoday\b|\btonight\b|\bthis\s+evening\b|\bthis\s+morning\b|\bthis\s+afternoon\b/i, day: () => today },
  ]

  for (const { re, day } of simple) {
    const match = re.exec(view)
    if (match) {
      consumer.take(match.index, match.index + match[0].length)
      return { day: day(), text: match[0].trim() }
    }
  }

  for (const { index, pattern } of WEEKDAYS) {
    const re = new RegExp(`\\b(?:on\\s+)?(?:next\\s+|this\\s+|coming\\s+)?(?:${pattern})\\b`, 'i')
    const match = re.exec(view)
    if (match) {
      consumer.take(match.index, match.index + match[0].length)
      return { day: nextWeekday(index, now), text: match[0].trim() }
    }
  }

  return null
}

export function parseNaturalInput(input: string, now: Date = new Date()): ParseResult {
  const consumer = new Consumer(input)
  const tokens: ParsedToken[] = []

  // Duration first: it is the most specific pattern, and its units would
  // otherwise be misread as clock digits.
  const duration = matchDuration(consumer)
  const time = matchTime(consumer)
  const date = matchDate(consumer, now)

  if (date) tokens.push({ kind: 'date', text: date.text, label: formatDayLabel(date.day, todayKey(now)) })
  if (time) tokens.push({ kind: 'time', text: time.text, label: formatClock(time.minute) })
  if (duration) {
    tokens.push({ kind: 'duration', text: duration.text, label: formatDuration(duration.minutes) })
  }

  const title = cleanTitle(consumer.remainder())
  const dayKey = date?.day ?? null
  const isFuture = dayKey != null && dayKey > todayKey(now)

  const suggestsActivity =
    PAST_TENSE_RE.test(input) || (duration != null && !isFuture && time == null)

  return {
    title,
    dayKey,
    minuteOfDay: time?.minute ?? null,
    durationMinutes: duration?.minutes ?? null,
    tokens,
    suggestsActivity,
  }
}
