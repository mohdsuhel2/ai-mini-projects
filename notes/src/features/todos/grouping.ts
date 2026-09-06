import { addWeeks, endOfWeek } from 'date-fns'
import { fromDayKey, shiftDay, toDayKey, todayKey } from '@/lib/date/day-key'
import type { DayKey, Todo } from '@/types'

export type GroupId =
  | 'pending'
  | 'today'
  | 'tomorrow'
  | 'thisWeek'
  | 'nextWeek'
  | 'upcoming'
  | 'someday'

export interface TodoGroup {
  id: GroupId
  label: string
  todos: Todo[]
}

const LABELS: Record<GroupId, string> = {
  pending: 'Pending',
  today: 'Today',
  tomorrow: 'Tomorrow',
  thisWeek: 'Later this week',
  nextWeek: 'Next week',
  upcoming: 'Upcoming',
  someday: 'Someday',
}

/** Monday, matching the default in settings. */
export type WeekStart = 0 | 1

/**
 * Buckets a date by how soon it is. Today and tomorrow are named outright;
 * everything else is measured against calendar week boundaries, because "later
 * this week" is a different kind of soon from "in nine days".
 */
export function groupIdFor(
  day: DayKey | null,
  today: DayKey = todayKey(),
  weekStartsOn: WeekStart = 1,
): GroupId {
  // A task worth keeping without a date belongs to no day at all.
  if (day == null) return 'someday'
  if (day < today) return 'pending'
  if (day === today) return 'today'
  if (day === shiftDay(today, 1)) return 'tomorrow'

  const reference = fromDayKey(today)
  const endThisWeek = toDayKey(endOfWeek(reference, { weekStartsOn }))
  if (day <= endThisWeek) return 'thisWeek'

  const endNextWeek = toDayKey(endOfWeek(addWeeks(reference, 1), { weekStartsOn }))
  if (day <= endNextWeek) return 'nextWeek'

  return 'upcoming'
}

const ORDER: GroupId[] = [
  'pending',
  'today',
  'tomorrow',
  'thisWeek',
  'nextWeek',
  'upcoming',
  'someday',
]

/**
 * Groups open todos for the Plan column. Empty buckets are dropped, except
 * Today: an empty Today is meaningful — it is the prompt to plan something —
 * while an empty "Next week" is just a blank heading.
 */
export function groupTodos(
  todos: Todo[],
  today: DayKey = todayKey(),
  weekStartsOn: WeekStart = 1,
): TodoGroup[] {
  const buckets: Record<GroupId, Todo[]> = {
    pending: [],
    today: [],
    tomorrow: [],
    thisWeek: [],
    nextWeek: [],
    upcoming: [],
    someday: [],
  }

  for (const todo of todos) {
    buckets[groupIdFor(todo.plannedDate, today, weekStartsOn)].push(todo)
  }

  const byDate = (a: Todo, b: Todo) => (a.plannedDate ?? '').localeCompare(b.plannedDate ?? '')
  const byDateThenTime = (a: Todo, b: Todo) =>
    byDate(a, b) || (a.plannedTime ?? 1440) - (b.plannedTime ?? 1440) || a.order - b.order

  // Groups spanning several days read by date; single-day groups keep the
  // manual order the list was handed.
  buckets.pending.sort((a, b) => byDate(a, b) || a.order - b.order)
  buckets.thisWeek.sort(byDateThenTime)
  buckets.nextWeek.sort(byDateThenTime)
  buckets.upcoming.sort(byDateThenTime)
  buckets.someday.sort((a, b) => a.order - b.order)

  return ORDER.filter((id) => buckets[id].length > 0 || id === 'today').map((id) => ({
    id,
    label: LABELS[id],
    todos: buckets[id],
  }))
}

/** True when the group's heading already implies each row's date. */
export function groupImpliesDate(id: GroupId): boolean {
  return id === 'today' || id === 'tomorrow' || id === 'someday'
}
