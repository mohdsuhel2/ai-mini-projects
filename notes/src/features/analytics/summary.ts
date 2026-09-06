import type {
  Activity,
  Category,
  CategoryTotal,
  DailySummary,
  DayKey,
  Id,
  MinuteOfDay,
  Tone,
  Todo,
} from '@/types'

const UNCATEGORISED: Pick<CategoryTotal, 'name' | 'tone' | 'icon'> = {
  name: 'Other',
  tone: 'slate',
  icon: 'circle-dashed',
}

/**
 * Rolls a day's activities up by category. Activities without a duration are
 * counted as events but contribute no minutes — the bar chart must never
 * pretend to know a length it was not told.
 */
export function summariseDay(
  day: DayKey,
  todos: Todo[],
  activities: Activity[],
  categories: Category[],
): DailySummary {
  const byId = new Map(categories.map((c) => [c.id, c]))
  const totals = new Map<string, CategoryTotal>()

  let trackedMinutes = 0

  for (const activity of activities) {
    const minutes = activity.duration ?? 0
    trackedMinutes += minutes
    if (minutes <= 0) continue

    const key = activity.categoryId ?? '__none__'
    const category = activity.categoryId ? byId.get(activity.categoryId) : undefined
    const existing = totals.get(key)
    if (existing) {
      existing.minutes += minutes
    } else {
      totals.set(key, {
        categoryId: activity.categoryId ?? null,
        name: category?.name ?? UNCATEGORISED.name,
        tone: category?.tone ?? UNCATEGORISED.tone,
        icon: category?.icon ?? UNCATEGORISED.icon,
        minutes,
      })
    }
  }

  const dayTodos = todos.filter((t) => t.plannedDate === day)

  return {
    day,
    completedCount: dayTodos.filter((t) => t.status === 'COMPLETED').length,
    openCount: dayTodos.filter((t) => t.status === 'OPEN').length,
    trackedMinutes,
    activityCount: activities.length,
    // Largest first; ties fall back to name so the order never flickers.
    byCategory: [...totals.values()].sort(
      (a, b) => b.minutes - a.minutes || a.name.localeCompare(b.name),
    ),
  }
}

/** Share of tracked time per category, as a 0–1 fraction. */
export function categoryShares(summary: DailySummary): Array<CategoryTotal & { share: number }> {
  const total = summary.byCategory.reduce((sum, c) => sum + c.minutes, 0)
  if (total === 0) return []
  return summary.byCategory.map((c) => ({ ...c, share: c.minutes / total }))
}

export const MINUTES_IN_DAY = 24 * 60

/** One activity as it sits on the clock, ready to be drawn at its own hour. */
export interface DayBlock {
  id: Id
  title: string
  categoryId: Id | null
  name: string
  tone: Tone
  icon: string
  /** Minutes past midnight, clamped into the day. `end` is always > `start`. */
  start: MinuteOfDay
  end: MinuteOfDay
  minutes: number
}

export interface DaySchedule {
  /** Chronological; where two entries overlap the longer one comes first. */
  blocks: DayBlock[]
  /** Minutes of the day covered by at least one block — overlaps count once. */
  coveredMinutes: number
  /** Tracked minutes on entries with no clock time, so nothing can be drawn. */
  unplacedMinutes: number
  untrackedMinutes: number
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

/**
 * The stretch of clock an activity occupies, or null when it cannot be placed.
 *
 * Two of `startTime`, `endTime` and `duration` are enough — the third follows.
 * A moment with no length (a start and nothing else) is not a stretch of time
 * and gets no block; inventing a width for it would be inventing data.
 */
export function blockSpan(activity: Activity): { start: MinuteOfDay; end: MinuteOfDay } | null {
  const duration = activity.duration ?? null
  let start = activity.startTime ?? null
  let end = activity.endTime ?? null

  if (start == null && end != null && duration != null) start = end - duration
  if (end == null && start != null && duration != null) end = start + duration
  if (start == null || end == null) return null

  const from = clamp(start, 0, MINUTES_IN_DAY)
  // An entry running past midnight is truncated rather than wrapped: the bar
  // is one day, and the tail belongs to a day this bar is not showing.
  const to = clamp(end, from, MINUTES_IN_DAY)
  return to > from ? { start: from, end: to } : null
}

/** Total length of a set of spans with overlaps counted once. */
function unionMinutes(spans: Array<{ start: number; end: number }>): number {
  const sorted = [...spans].sort((a, b) => a.start - b.start)
  let total = 0
  let cursor = -1
  for (const span of sorted) {
    const from = Math.max(span.start, cursor)
    if (span.end > from) {
      total += span.end - from
      cursor = span.end
    }
  }
  return total
}

/**
 * The day laid out on the clock: every activity drawn at the hour it actually
 * happened, not packed against the left edge. Watching YouTube from 1 to 2 PM
 * should colour 1 to 2 PM — a bar that only shows *how much* answers a
 * different question than the one the day is asking.
 */
export function daySchedule(activities: Activity[], categories: Category[]): DaySchedule {
  const byId = new Map(categories.map((c) => [c.id, c]))
  const blocks: DayBlock[] = []
  let unplacedMinutes = 0

  for (const activity of activities) {
    const span = blockSpan(activity)
    if (!span) {
      unplacedMinutes += Math.max(0, activity.duration ?? 0)
      continue
    }
    const category = activity.categoryId ? byId.get(activity.categoryId) : undefined
    blocks.push({
      id: activity.id,
      title: activity.title,
      categoryId: activity.categoryId ?? null,
      name: category?.name ?? UNCATEGORISED.name,
      tone: category?.tone ?? UNCATEGORISED.tone,
      icon: category?.icon ?? UNCATEGORISED.icon,
      start: span.start,
      end: span.end,
      minutes: span.end - span.start,
    })
  }

  // Longest first within the same start, so a short entry nested inside a long
  // one is painted last and stays visible.
  blocks.sort((a, b) => a.start - b.start || b.end - a.end)

  const coveredMinutes = unionMinutes(blocks)

  return {
    blocks,
    coveredMinutes,
    unplacedMinutes,
    untrackedMinutes: Math.max(0, MINUTES_IN_DAY - coveredMinutes - unplacedMinutes),
  }
}

/**
 * The entry at one minute of the day, or null where nothing was logged.
 *
 * Where entries overlap, the answer is the one actually visible: blocks paint
 * in order, so the last cover wins.
 */
export function blockAt(blocks: DayBlock[], minute: MinuteOfDay): DayBlock | null {
  const at = clamp(minute, 0, MINUTES_IN_DAY - 1)
  let hit: DayBlock | null = null
  for (const block of blocks) {
    if (at >= block.start && at < block.end) hit = block
  }
  return hit
}
