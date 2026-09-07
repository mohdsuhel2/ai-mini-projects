import { db } from '@/lib/db/database'
import { liveOnly, newId, now } from '@/lib/db/records'
import { instantToDayKey, instantToMinutes, todayKey } from '@/lib/date/day-key'
import { occurrenceAfter, occurrenceOnOrAfter } from './recurrence'
import type { DayKey, Id, MinuteOfDay, Recurrence, Todo } from '@/types'

export interface NewTodoInput {
  title: string
  /** Null keeps the task without a date, in Someday. */
  plannedDate: DayKey | null
  categoryId?: Id | null
  plannedTime?: MinuteOfDay | null
  estimatedDuration?: number | null
  recurrence?: Recurrence | null
  /** Set only when one occurrence spawns the next; a new series makes its own. */
  seriesId?: Id | null
  notes?: string | null
}

function sortTodos(todos: Todo[]): Todo[] {
  return todos.sort((a, b) => {
    // A timed task outranks an untimed one on the same day.
    if (a.plannedTime != null && b.plannedTime != null && a.plannedTime !== b.plannedTime) {
      return a.plannedTime - b.plannedTime
    }
    if (a.plannedTime != null && b.plannedTime == null) return -1
    if (a.plannedTime == null && b.plannedTime != null) return 1
    return a.order - b.order
  })
}

export async function listOpenTodos(): Promise<Todo[]> {
  const rows = await db().todos.where('status').equals('OPEN').toArray()
  return sortTodos(liveOnly(rows))
}

export async function listTodosForDay(day: DayKey): Promise<Todo[]> {
  const rows = await db().todos.where('plannedDate').equals(day).toArray()
  return sortTodos(liveOnly(rows))
}

export async function createTodo(input: NewTodoInput): Promise<Id> {
  const title = input.title.trim()
  if (!title) throw new Error('A todo needs a title')

  const stamp = now()
  const siblings = input.plannedDate
    ? await db().todos.where('plannedDate').equals(input.plannedDate).toArray()
    : (await db().todos.toArray()).filter((t) => t.plannedDate == null)
  const maxOrder = siblings.reduce((max, t) => Math.max(max, t.order), 0)

  const id = newId()
  await db().todos.add({
    id,
    title,
    categoryId: input.categoryId ?? null,
    plannedDate: input.plannedDate,
    plannedTime: input.plannedTime ?? null,
    estimatedDuration: input.estimatedDuration ?? null,
    status: 'OPEN',
    recurrence: input.recurrence ?? null,
    // A repeating task needs an identity beyond this one occurrence, so the
    // next one can be recognised as the same thing rather than a lookalike.
    seriesId: input.recurrence ? (input.seriesId ?? newId()) : null,
    notes: input.notes ?? null,
    createdAt: stamp,
    completedAt: null,
    actualDuration: null,
    order: maxOrder + 10,
    updatedAt: stamp,
    deletedAt: null,
  })
  return id
}

export async function updateTodo(id: Id, patch: Partial<Omit<Todo, 'id'>>): Promise<void> {
  await db().todos.update(id, { ...patch, updatedAt: now() })
}

export async function deleteTodo(id: Id): Promise<void> {
  const stamp = now()
  await db().transaction('rw', db().todos, db().activities, async () => {
    await db().todos.update(id, { deletedAt: stamp, updatedAt: stamp })
    const linked = await db().activities.where('todoId').equals(id).toArray()
    await Promise.all(
      linked
        .filter((a) => a.source === 'TODO_COMPLETION')
        .map((a) => db().activities.update(a.id, { deletedAt: stamp, updatedAt: stamp })),
    )
  })
}

/**
 * Completing a todo also records what happened. Both writes share one
 * transaction: a completed task that never reached the timeline would leave the
 * day's record lying about itself.
 */
export async function completeTodo(id: Id, actualDuration?: number | null): Promise<void> {
  const stamp = now()
  await db().transaction('rw', db().todos, db().activities, async () => {
    const todo = await db().todos.get(id)
    if (!todo || todo.status === 'COMPLETED') return

    const duration = actualDuration ?? todo.estimatedDuration ?? null
    const endTime = instantToMinutes(stamp)
    const startTime = duration != null ? Math.max(0, endTime - duration) : null

    await db().todos.update(id, {
      status: 'COMPLETED',
      completedAt: stamp,
      actualDuration: duration,
      updatedAt: stamp,
    })

    await db().activities.add({
      id: newId(),
      title: todo.title,
      categoryId: todo.categoryId ?? null,
      date: instantToDayKey(stamp),
      startTime,
      endTime,
      duration,
      source: 'TODO_COMPLETION',
      todoId: todo.id,
      notes: null,
      createdAt: stamp,
      updatedAt: stamp,
      deletedAt: null,
    })

    if (todo.recurrence) await spawnNextOccurrence(todo, stamp)
  })
}

/**
 * Writes the occurrence that follows a completed one.
 *
 * Anchored to the day the task was due rather than the day it was ticked, so
 * finishing early does not drag the whole series earlier. Does nothing if the
 * series already has an open occurrence — completing a task the roll-forward
 * has not caught up with yet must not leave two of the same thing waiting.
 */
async function spawnNextOccurrence(todo: Todo, stamp: number): Promise<void> {
  if (!todo.recurrence) return

  const open = liveOnly(await db().todos.where('status').equals('OPEN').toArray())
  if (open.some((t) => t.seriesId != null && t.seriesId === todo.seriesId)) return

  const anchor = todo.plannedDate ?? instantToDayKey(stamp)
  await db().todos.add({
    ...todo,
    id: newId(),
    plannedDate: occurrenceAfter(todo.recurrence, anchor),
    status: 'OPEN',
    completedAt: null,
    actualDuration: null,
    createdAt: stamp,
    updatedAt: stamp,
    deletedAt: null,
  })
}

/**
 * Catches repeating tasks up to the present.
 *
 * A missed occurrence moves to its next real date rather than accumulating or
 * sitting in Pending: a repeating task is a habit, and a habit that greets you
 * with a fortnight of arrears is one you stop opening the app to avoid.
 *
 * The new date is written rather than faked at render time, so what is stored
 * matches what is shown and completing it records the day it says.
 */
export async function rollForwardRecurring(today: DayKey = todayKey()): Promise<number> {
  const open = liveOnly(await db().todos.where('status').equals('OPEN').toArray())
  const stale = open.filter(
    (todo) => todo.recurrence != null && todo.plannedDate != null && todo.plannedDate < today,
  )
  if (stale.length === 0) return 0

  const stamp = now()
  await db().todos.bulkUpdate(
    stale.map((todo) => ({
      key: todo.id,
      changes: {
        plannedDate: occurrenceOnOrAfter(todo.recurrence!, today),
        updatedAt: stamp,
      },
    })),
  )
  return stale.length
}

/**
 * Un-checking removes the derived activity, even if the user edited it.
 * Predictable beats clever: the alternative is orphaned records nobody can
 * explain the provenance of.
 */
export async function reopenTodo(id: Id): Promise<void> {
  const stamp = now()
  await db().transaction('rw', db().todos, db().activities, async () => {
    const todo = await db().todos.get(id)
    await db().todos.update(id, {
      status: 'OPEN',
      completedAt: null,
      actualDuration: null,
      updatedAt: stamp,
    })

    // Completing spawned the next occurrence; un-completing takes it back,
    // or the series would be left with two open copies of itself.
    if (todo?.seriesId) {
      const open = liveOnly(await db().todos.where('status').equals('OPEN').toArray())
      // Identified by falling later in the series, not by a later `createdAt`:
      // a task created and completed inside the same millisecond shares one.
      const successors = open.filter(
        (t) =>
          t.id !== id &&
          t.seriesId === todo.seriesId &&
          t.plannedDate != null &&
          todo.plannedDate != null &&
          t.plannedDate > todo.plannedDate,
      )
      await Promise.all(successors.map((t) => db().todos.delete(t.id)))
    }
    const linked = await db().activities.where('todoId').equals(id).toArray()
    await Promise.all(
      linked
        .filter((a) => a.source === 'TODO_COMPLETION')
        .map((a) => db().activities.update(a.id, { deletedAt: stamp, updatedAt: stamp })),
    )
  })
}

export async function rescheduleTodo(id: Id, plannedDate: DayKey | null): Promise<void> {
  const stamp = now()
  const siblings = plannedDate
    ? await db().todos.where('plannedDate').equals(plannedDate).toArray()
    : (await db().todos.toArray()).filter((t) => t.plannedDate == null)
  const maxOrder = siblings.reduce((max, t) => Math.max(max, t.order), 0)
  await db().todos.update(id, { plannedDate, order: maxOrder + 10, updatedAt: stamp })
}

/** Moves everything still open and overdue onto today. */
export async function rollOverOverdue(target: DayKey = todayKey()): Promise<number> {
  const stamp = now()
  const open = await listOpenTodos()
  // An undated task is not late; it simply has no date.
  const overdue = open.filter((t) => t.plannedDate != null && t.plannedDate < target)
  if (overdue.length === 0) return 0
  await db().transaction('rw', db().todos, async () => {
    for (const todo of overdue) {
      await db().todos.update(todo.id, { plannedDate: target, updatedAt: stamp })
    }
  })
  return overdue.length
}
