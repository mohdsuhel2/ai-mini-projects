import { db } from '@/lib/db/database'
import { liveOnly, newId, now } from '@/lib/db/records'
import { instantToDayKey, instantToMinutes, todayKey } from '@/lib/date/day-key'
import type { DayKey, Id, MinuteOfDay, Todo } from '@/types'

export interface NewTodoInput {
  title: string
  /** Null keeps the task without a date, in Someday. */
  plannedDate: DayKey | null
  categoryId?: Id | null
  plannedTime?: MinuteOfDay | null
  estimatedDuration?: number | null
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
  })
}

/**
 * Un-checking removes the derived activity, even if the user edited it.
 * Predictable beats clever: the alternative is orphaned records nobody can
 * explain the provenance of.
 */
export async function reopenTodo(id: Id): Promise<void> {
  const stamp = now()
  await db().transaction('rw', db().todos, db().activities, async () => {
    await db().todos.update(id, {
      status: 'OPEN',
      completedAt: null,
      actualDuration: null,
      updatedAt: stamp,
    })
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
