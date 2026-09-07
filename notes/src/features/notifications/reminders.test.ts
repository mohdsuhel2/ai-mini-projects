import { describe, expect, it } from 'vitest'
import { dueReminders, REMINDER_WINDOW_MINUTES } from './reminders'
import type { Todo } from '@/types'

const TODAY = '2026-09-07'
const NOON = 12 * 60

const todo = (partial: Partial<Todo>): Todo => ({
  id: partial.id ?? Math.random().toString(),
  title: partial.title ?? 'Ring the bank',
  categoryId: null,
  plannedDate: partial.plannedDate === undefined ? TODAY : partial.plannedDate,
  plannedTime: partial.plannedTime === undefined ? NOON : partial.plannedTime,
  estimatedDuration: null,
  status: partial.status ?? 'OPEN',
  notes: null,
  createdAt: 0,
  completedAt: null,
  actualDuration: null,
  order: 10,
  updatedAt: 0,
  deletedAt: null,
})

const none = new Set<string>()
const due = (todos: Todo[], at: number, notified = none) =>
  dueReminders(todos, at, TODAY, notified).map((t) => t.id)

describe('dueReminders', () => {
  it('fires when the minute arrives, and not a minute before', () => {
    const t = todo({ id: 'a' })
    expect(due([t], NOON - 1)).toEqual([])
    expect(due([t], NOON)).toEqual(['a'])
  })

  it('still fires just inside the window, and never outside it', () => {
    const t = todo({ id: 'a' })
    expect(due([t], NOON + REMINDER_WINDOW_MINUTES)).toEqual(['a'])
    expect(due([t], NOON + REMINDER_WINDOW_MINUTES + 1)).toEqual([])
  })

  it('does not replay the morning when the app is opened in the evening', () => {
    const morning = [todo({ id: 'a', plannedTime: 9 * 60 }), todo({ id: 'b', plannedTime: 10 * 60 })]
    expect(due(morning, 19 * 60)).toEqual([])
  })

  it('fires once, then leaves the task alone', () => {
    const t = todo({ id: 'a' })
    expect(due([t], NOON, new Set(['a']))).toEqual([])
  })

  it('ignores a task with no time on it', () => {
    expect(due([todo({ id: 'a', plannedTime: null })], NOON)).toEqual([])
  })

  it('ignores a task that is already done', () => {
    expect(due([todo({ id: 'a', status: 'COMPLETED' })], NOON)).toEqual([])
  })

  it('ignores a task belonging to another day, past or future', () => {
    expect(due([todo({ id: 'a', plannedDate: '2026-09-06' })], NOON)).toEqual([])
    expect(due([todo({ id: 'b', plannedDate: '2026-09-08' })], NOON)).toEqual([])
    expect(due([todo({ id: 'c', plannedDate: null })], NOON)).toEqual([])
  })

  it('returns every task sharing the same minute', () => {
    const both = [todo({ id: 'a' }), todo({ id: 'b' })]
    expect(due(both, NOON).sort()).toEqual(['a', 'b'])
  })
})
