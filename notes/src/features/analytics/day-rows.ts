import type { Activity, MinuteOfDay } from '@/types'
import { blockSpan } from './summary'

/**
 * A stretch of nothing shorter than this is just the seam between two entries —
 * drawing it would add a row that says less than the space it takes.
 */
export const GAP_THRESHOLD_MINUTES = 20

export type DayRow =
  | {
      kind: 'entry'
      activity: Activity
      /** Minutes past midnight the entry begins, or null when it has no clock time. */
      at: MinuteOfDay
      end: MinuteOfDay | null
      /** Length in minutes; 0 for a moment that was recorded without one. */
      minutes: number
    }
  | { kind: 'gap'; minutes: number; from: MinuteOfDay; to: MinuteOfDay }

export interface DayRows {
  rows: DayRow[]
  /** Logged, but with no clock time to place them at. They sit under the day. */
  untimed: Activity[]
}

/**
 * The day as a run of entries with its holes made explicit.
 *
 * Time is the vertical axis, but a literal one would spend most of its height
 * on the hours nothing happened in. So tracked stretches are drawn to scale and
 * empty ones collapse to a single labelled row: the proportions that carry
 * meaning survive, and the dead space does not.
 */
export function dayRows(
  activities: Activity[],
  gapThreshold: number = GAP_THRESHOLD_MINUTES,
): DayRows {
  const timed: Array<{ activity: Activity; at: MinuteOfDay; end: MinuteOfDay | null }> = []
  const untimed: Activity[] = []

  for (const activity of activities) {
    const span = blockSpan(activity)
    const at = span?.start ?? activity.startTime ?? activity.endTime ?? null
    if (at == null) {
      untimed.push(activity)
      continue
    }
    timed.push({ activity, at, end: span?.end ?? null })
  }

  // Earliest first; where two start together the longer one leads, so the run
  // reads as an outer stretch followed by what happened inside it.
  timed.sort((a, b) => a.at - b.at || (b.end ?? b.at) - (a.end ?? a.at))

  const rows: DayRow[] = []
  // The furthest point already accounted for — not the previous entry's end,
  // which would invent a gap after any entry that another one overlapped.
  let cursor: number | null = null

  for (const { activity, at, end } of timed) {
    if (cursor != null && at - cursor >= gapThreshold) {
      rows.push({ kind: 'gap', minutes: at - cursor, from: cursor, to: at })
    }
    rows.push({ kind: 'entry', activity, at, end, minutes: end == null ? 0 : end - at })
    cursor = Math.max(cursor ?? at, end ?? at)
  }

  return { rows, untimed }
}

export type DayPart = 'morning' | 'afternoon' | 'evening'

export const DAY_PART_LABELS: Record<DayPart, string> = {
  morning: 'Morning',
  afternoon: 'Afternoon',
  evening: 'Evening',
}

/** Noon and 5pm — where people actually feel the day change, not even thirds. */
export function dayPartOf(minute: MinuteOfDay): DayPart {
  if (minute < 12 * 60) return 'morning'
  return minute < 17 * 60 ? 'afternoon' : 'evening'
}

export interface DayPartGroup {
  part: DayPart
  label: string
  entries: Array<Extract<DayRow, { kind: 'entry' }>>
  /** Everything logged in this stretch, for the heading. */
  minutes: number
}

/**
 * Splits the day into the three stretches people plan around.
 *
 * An empty stretch is dropped rather than shown blank: "Evening — nothing" is
 * not a fact about the evening at half past two.
 */
export function groupByDayPart(rows: DayRow[]): DayPartGroup[] {
  const order: DayPart[] = ['morning', 'afternoon', 'evening']
  const buckets = new Map<DayPart, DayPartGroup>(
    order.map((part) => [part, { part, label: DAY_PART_LABELS[part], entries: [], minutes: 0 }]),
  )

  for (const row of rows) {
    if (row.kind !== 'entry') continue
    const group = buckets.get(dayPartOf(row.at))!
    group.entries.push(row)
    group.minutes += row.minutes
  }

  return order.map((part) => buckets.get(part)!).filter((group) => group.entries.length > 0)
}

/** The longest entry of the day — the bar under each row is drawn against it. */
export function longestEntry(rows: DayRow[]): number {
  return rows.reduce((max, row) => (row.kind === 'entry' ? Math.max(max, row.minutes) : max), 0)
}
