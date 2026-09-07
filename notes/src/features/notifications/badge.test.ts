import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyBadge, badgeCount } from './badge'
import type { Todo } from '@/types'

const TODAY = '2026-09-07'

const todo = (partial: Partial<Todo>): Todo => ({
  id: Math.random().toString(),
  title: 'Task',
  categoryId: null,
  plannedDate: partial.plannedDate === undefined ? TODAY : partial.plannedDate,
  plannedTime: null,
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

describe('badgeCount', () => {
  it('counts what is late and what is due today', () => {
    const todos = [
      todo({ plannedDate: '2026-09-01' }),
      todo({ plannedDate: '2026-09-06' }),
      todo({ plannedDate: TODAY }),
    ]
    expect(badgeCount(todos, TODAY)).toBe(3)
  })

  it('leaves out what is not due yet, so the badge can reach zero', () => {
    const todos = [
      todo({ plannedDate: '2026-09-08' }),
      todo({ plannedDate: '2026-10-01' }),
      todo({ plannedDate: null }),
    ]
    expect(badgeCount(todos, TODAY)).toBe(0)
  })

  it('leaves out anything already done', () => {
    expect(badgeCount([todo({ status: 'COMPLETED' })], TODAY)).toBe(0)
  })
})

afterEach(() => {
  Reflect.deleteProperty(navigator, 'setAppBadge')
  Reflect.deleteProperty(navigator, 'clearAppBadge')
})

describe('applyBadge', () => {
  it('is a silent no-op where the platform cannot badge', async () => {
    // jsdom's navigator has neither method; the point is that this resolves.
    await expect(applyBadge(3)).resolves.toBeUndefined()
    await expect(applyBadge(0)).resolves.toBeUndefined()
  })

  it('sets a count and clears at zero where it can', async () => {
    const setAppBadge = vi.fn().mockResolvedValue(undefined)
    const clearAppBadge = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { setAppBadge, clearAppBadge })

    await applyBadge(4)
    await applyBadge(0)
    expect(setAppBadge).toHaveBeenCalledWith(4)
    expect(clearAppBadge).toHaveBeenCalled()
  })

  it('swallows a rejection rather than surfacing it', async () => {
    Object.assign(navigator, { setAppBadge: vi.fn().mockRejectedValue(new Error('not installed')) })
    await expect(applyBadge(1)).resolves.toBeUndefined()
  })
})
