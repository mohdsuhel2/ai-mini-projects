import { db } from '@/lib/db/database'
import { shiftDay, todayKey } from '@/lib/date/day-key'
import type { Activity, DayKey, Folder, Id, Note, Todo } from '@/types'

export type RetentionWindow = 'week' | 'month' | 'quarter' | 'year'

export const RETENTION_DAYS: Record<RetentionWindow, number> = {
  week: 7,
  month: 30,
  quarter: 90,
  year: 365,
}

export const RETENTION_LABELS: Record<RetentionWindow, string> = {
  week: 'Last week',
  month: 'Last month',
  quarter: 'Last 3 months',
  year: 'Last year',
}

/** The oldest day that survives. Anything strictly before it is swept. */
export function cutoffFor(window: RetentionWindow, today: DayKey = todayKey()): DayKey {
  return shiftDay(today, -(RETENTION_DAYS[window] - 1))
}

export interface CleanupSelection {
  activities: Id[]
  todos: Id[]
  /** Only ever rows already in the bin — notes are never swept by age. */
  notes: Id[]
  folders: Id[]
}

export interface CleanupPreview extends CleanupSelection {
  cutoff: DayKey
  /** Old records being removed, as opposed to bin rows being purged. */
  agedOut: number
  binned: number
  total: number
}

const isBinned = (row: { deletedAt?: number | null }) => row.deletedAt != null

/**
 * What a sweep would take.
 *
 * Three rules, each chosen so nothing you might still act on disappears:
 *
 * - **Activities** are the log. Anything before the cutoff goes.
 * - **Only COMPLETED todos** go. An open task from four months ago is still a
 *   task you have not done; age is not consent to delete it.
 * - **Notes and folders are never swept by age.** A note has no date it belongs
 *   to — it is a thing you wrote to keep, and "old" says nothing about whether
 *   you still want it.
 *
 * Rows already in the bin are purged for real regardless of age, because that
 * is the one place where the user has already said they are finished with them.
 */
export function selectForCleanup(
  data: { activities: Activity[]; todos: Todo[]; notes: Note[]; folders: Folder[] },
  cutoff: DayKey,
): CleanupSelection {
  return {
    activities: data.activities
      .filter((row) => isBinned(row) || row.date < cutoff)
      .map((row) => row.id),
    todos: data.todos
      .filter(
        (row) =>
          isBinned(row) ||
          (row.status === 'COMPLETED' && row.plannedDate != null && row.plannedDate < cutoff),
      )
      .map((row) => row.id),
    notes: data.notes.filter(isBinned).map((row) => row.id),
    folders: data.folders.filter(isBinned).map((row) => row.id),
  }
}

/** Counts the same selection two ways, so the confirmation can say which is which. */
export function describeCleanup(
  data: { activities: Activity[]; todos: Todo[]; notes: Note[]; folders: Folder[] },
  cutoff: DayKey,
): CleanupPreview {
  const selection = selectForCleanup(data, cutoff)
  const binned =
    data.activities.filter(isBinned).length +
    data.todos.filter(isBinned).length +
    data.notes.filter(isBinned).length +
    data.folders.filter(isBinned).length
  const total =
    selection.activities.length +
    selection.todos.length +
    selection.notes.length +
    selection.folders.length

  return { ...selection, cutoff, binned, agedOut: total - binned, total }
}

async function readAll() {
  const [activities, todos, notes, folders] = await Promise.all([
    db().activities.toArray(),
    db().todos.toArray(),
    db().notes.toArray(),
    db().folders.toArray(),
  ])
  return { activities, todos, notes, folders }
}

/** What a sweep would remove right now, without removing anything. */
export async function previewCleanup(
  window: RetentionWindow,
  today: DayKey = todayKey(),
): Promise<CleanupPreview> {
  return describeCleanup(await readAll(), cutoffFor(window, today))
}

/**
 * Runs the sweep. Hard deletes, in one transaction.
 *
 * Soft-deleting here would be theatre: the whole point is to be rid of the
 * rows, and a "cleanup" that leaves everything on disk has not cleaned
 * anything. There is no undo, which is why the caller must confirm against the
 * counts from `previewCleanup` first.
 */
export async function runCleanup(
  window: RetentionWindow,
  today: DayKey = todayKey(),
): Promise<CleanupPreview> {
  const preview = await previewCleanup(window, today)

  await db().transaction(
    'rw',
    [db().activities, db().todos, db().notes, db().folders],
    async () => {
      await db().activities.bulkDelete(preview.activities)
      await db().todos.bulkDelete(preview.todos)
      await db().notes.bulkDelete(preview.notes)
      await db().folders.bulkDelete(preview.folders)
    },
  )

  return preview
}
