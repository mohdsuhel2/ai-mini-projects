import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { db } from '@/lib/db/database'
import { freshDatabase } from '@/lib/db/test-utils'
import { todayKey } from '@/lib/date/day-key'
import { listActivitiesForDay } from '@/features/activities/api'
import {
  completeTodo,
  createTodo,
  deleteTodo,
  listOpenTodos,
  reopenTodo,
  rescheduleTodo,
  rollOverOverdue,
} from './api'

const TODAY = todayKey()

beforeEach(async () => {
  await freshDatabase()
})

afterEach(() => {
  vi.restoreAllMocks()
})

/**
 * Runs the body at a fixed moment of `TODAY`.
 *
 * Anything that backdates a start by a duration behaves differently within that
 * duration of midnight, so a test asserting the ordinary case has to say which
 * hour it means — otherwise it passes all day and fails just after twelve.
 *
 * Only `Date.now` is pinned. Faking timers wholesale also stops the ones Dexie
 * schedules its transactions on, which fails them before the body can run.
 */
async function at(time: string, body: () => Promise<void>): Promise<void> {
  const fixed = new Date(`${TODAY}T${time}`).getTime()
  const clock = vi.spyOn(Date, 'now').mockReturnValue(fixed)
  try {
    await body()
  } finally {
    clock.mockRestore()
  }
}

describe('createTodo', () => {
  it('stores a todo that is open by default', async () => {
    const id = await createTodo({ title: 'Buy groceries', plannedDate: TODAY })
    const todo = await db().todos.get(id)
    expect(todo).toMatchObject({ title: 'Buy groceries', status: 'OPEN', plannedDate: TODAY })
  })

  it('trims the title and rejects an empty one', async () => {
    const id = await createTodo({ title: '  Spaced  ', plannedDate: TODAY })
    expect((await db().todos.get(id))?.title).toBe('Spaced')
    await expect(createTodo({ title: '   ', plannedDate: TODAY })).rejects.toThrow()
  })

  it('appends new todos after existing ones on the same day', async () => {
    const first = await createTodo({ title: 'First', plannedDate: TODAY })
    const second = await createTodo({ title: 'Second', plannedDate: TODAY })
    const a = await db().todos.get(first)
    const b = await db().todos.get(second)
    expect(b!.order).toBeGreaterThan(a!.order)
  })

  it('sorts timed todos ahead of untimed ones', async () => {
    await createTodo({ title: 'Whenever', plannedDate: TODAY })
    await createTodo({ title: 'At nine', plannedDate: TODAY, plannedTime: 9 * 60 })
    const open = await listOpenTodos()
    expect(open.map((t) => t.title)).toEqual(['At nine', 'Whenever'])
  })
})

describe('completeTodo', () => {
  it('marks the todo complete and records a linked activity', async () => {
    const id = await createTodo({ title: 'Write docs', plannedDate: TODAY })
    await completeTodo(id, 45)

    const todo = await db().todos.get(id)
    expect(todo).toMatchObject({ status: 'COMPLETED', actualDuration: 45 })
    expect(todo!.completedAt).toBeGreaterThan(0)

    const activities = await listActivitiesForDay(TODAY)
    expect(activities).toHaveLength(1)
    expect(activities[0]).toMatchObject({
      title: 'Write docs',
      source: 'TODO_COMPLETION',
      todoId: id,
      duration: 45,
    })
  })

  it('backdates the activity start by its duration', async () => {
    await at('14:00:00', async () => {
      const id = await createTodo({ title: 'Deep work', plannedDate: TODAY })
      await completeTodo(id, 30)
      const [activity] = await listActivitiesForDay(TODAY)
      expect(activity).toMatchObject({ startTime: 810, endTime: 840, duration: 30 })
    })
  })

  it('clamps the start to midnight rather than running into the day before', async () => {
    await at('00:10:00', async () => {
      const id = await createTodo({ title: 'Late finish', plannedDate: TODAY })
      await completeTodo(id, 30)
      const [activity] = await listActivitiesForDay(TODAY)
      // The times only ever describe the part that happened on this day.
      // `duration` is the record of how long it actually took.
      expect(activity).toMatchObject({ startTime: 0, endTime: 10, duration: 30 })
    })
  })

  it('falls back to the estimate when no duration is given', async () => {
    const id = await createTodo({ title: 'Standup', plannedDate: TODAY, estimatedDuration: 15 })
    await completeTodo(id)
    const [activity] = await listActivitiesForDay(TODAY)
    expect(activity.duration).toBe(15)
  })

  it('records no duration when nothing is known', async () => {
    const id = await createTodo({ title: 'Call mum', plannedDate: TODAY })
    await completeTodo(id)
    const [activity] = await listActivitiesForDay(TODAY)
    expect(activity.duration).toBeNull()
    expect(activity.startTime).toBeNull()
  })

  it('is idempotent', async () => {
    const id = await createTodo({ title: 'Once only', plannedDate: TODAY })
    await completeTodo(id, 20)
    await completeTodo(id, 20)
    expect(await listActivitiesForDay(TODAY)).toHaveLength(1)
  })
})

describe('reopenTodo', () => {
  it('reopens the todo and removes the derived activity', async () => {
    const id = await createTodo({ title: 'Undo me', plannedDate: TODAY })
    await completeTodo(id, 20)
    await reopenTodo(id)

    expect(await db().todos.get(id)).toMatchObject({ status: 'OPEN', completedAt: null })
    expect(await listActivitiesForDay(TODAY)).toEqual([])
  })

  it('leaves manually logged activities alone', async () => {
    const { createActivity } = await import('@/features/activities/api')
    await createActivity({ title: 'Lunch', date: TODAY, duration: 45 })
    const id = await createTodo({ title: 'Task', plannedDate: TODAY })
    await completeTodo(id, 10)
    await reopenTodo(id)

    const remaining = await listActivitiesForDay(TODAY)
    expect(remaining.map((a) => a.title)).toEqual(['Lunch'])
  })
})

describe('deleteTodo', () => {
  it('soft-deletes so a future sync can propagate the removal', async () => {
    const id = await createTodo({ title: 'Gone', plannedDate: TODAY })
    await deleteTodo(id)
    expect(await listOpenTodos()).toEqual([])
    expect((await db().todos.get(id))?.deletedAt).toBeGreaterThan(0)
  })
})

describe('undated todos', () => {
  it('creates a todo with no date at all', async () => {
    const id = await createTodo({ title: 'Someday maybe', plannedDate: null })
    expect((await db().todos.get(id))?.plannedDate).toBeNull()
  })

  it('keeps an undated todo out of every by-day query', async () => {
    await createTodo({ title: 'Someday maybe', plannedDate: null })
    const { listTodosForDay } = await import('./api')
    expect(await listTodosForDay(TODAY)).toEqual([])
  })

  it('still lists an undated todo among open todos', async () => {
    await createTodo({ title: 'Someday maybe', plannedDate: null })
    expect((await listOpenTodos()).map((t) => t.title)).toEqual(['Someday maybe'])
  })

  it('does not treat an undated todo as overdue', async () => {
    await createTodo({ title: 'Someday maybe', plannedDate: null })
    expect(await rollOverOverdue(TODAY)).toBe(0)
    expect((await listOpenTodos())[0].plannedDate).toBeNull()
  })

  it('can give an undated todo a date later', async () => {
    const id = await createTodo({ title: 'Someday maybe', plannedDate: null })
    await rescheduleTodo(id, TODAY)
    expect((await db().todos.get(id))?.plannedDate).toBe(TODAY)
  })

  it('can strip the date off a dated todo', async () => {
    const id = await createTodo({ title: 'Dated', plannedDate: TODAY })
    await rescheduleTodo(id, null)
    expect((await db().todos.get(id))?.plannedDate).toBeNull()
  })
})

describe('rescheduling', () => {
  it('moves a todo to another day', async () => {
    const id = await createTodo({ title: 'Later', plannedDate: TODAY })
    await rescheduleTodo(id, '2027-01-01')
    expect((await db().todos.get(id))?.plannedDate).toBe('2027-01-01')
  })

  it('rolls overdue todos onto today and leaves future ones alone', async () => {
    await createTodo({ title: 'Overdue', plannedDate: '2020-01-01' })
    await createTodo({ title: 'Future', plannedDate: '2099-01-01' })
    const moved = await rollOverOverdue(TODAY)
    expect(moved).toBe(1)

    const open = await listOpenTodos()
    expect(open.find((t) => t.title === 'Overdue')?.plannedDate).toBe(TODAY)
    expect(open.find((t) => t.title === 'Future')?.plannedDate).toBe('2099-01-01')
  })
})
