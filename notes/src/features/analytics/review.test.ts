import { describe, expect, it } from 'vitest'
import { buildWeeklyReview, carryForwardCandidates } from './review'
import type { Activity, Todo } from '@/types'

describe('weekly review', () => {
  it('summarises plan, streak inputs, and backlog', () => {
    const todos: Todo[] = [
      { id: '1', title: 'Done', plannedDate: '2026-09-17', status: 'COMPLETED', order: 10, createdAt: 0, updatedAt: 0 },
      { id: '2', title: 'Open', plannedDate: '2026-09-17', status: 'OPEN', order: 20, createdAt: 0, updatedAt: 0 },
      { id: '3', title: 'Later', plannedDate: null, status: 'OPEN', order: 30, createdAt: 0, updatedAt: 0 },
    ]
    const activities: Activity[] = [
      { id: 'a', title: 'Work', date: '2026-09-17', duration: 45, source: 'MANUAL', createdAt: 0, updatedAt: 0 },
    ]
    const review = buildWeeklyReview(todos, activities, ['2026-09-17'], '2026-09-18')
    expect(review.plan.planned).toBe(2)
    expect(review.plan.completed).toBe(1)
    expect(review.somedayCount).toBe(1)
    expect(review.trackedMinutes).toBe(45)
  })

  it('lists overdue carry-forward candidates', () => {
    const todos: Todo[] = [
      { id: '1', title: 'Late', plannedDate: '2026-09-15', status: 'OPEN', order: 10, createdAt: 0, updatedAt: 0 },
    ]
    expect(carryForwardCandidates(todos, '2026-09-18').map((t) => t.id)).toEqual(['1'])
  })
})
