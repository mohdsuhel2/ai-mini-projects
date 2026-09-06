import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { DailySummary } from './daily-summary'
import { daySchedule, summariseDay } from '@/features/analytics/summary'
import type { Activity, Category } from '@/types'

const DAY = '2026-09-02'

const category = (id: string, name: string, tone: Category['tone']): Category => ({
  id,
  name,
  icon: 'briefcase',
  tone,
  scope: 'both',
  isDefault: true,
  order: 10,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

const activity = (partial: Partial<Activity>): Activity => ({
  id: partial.id ?? Math.random().toString(),
  title: partial.title ?? 'Something',
  categoryId: partial.categoryId ?? null,
  date: DAY,
  startTime: partial.startTime ?? null,
  endTime: partial.endTime ?? null,
  duration: partial.duration ?? null,
  source: 'MANUAL',
  todoId: null,
  notes: null,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

const CATEGORIES = [
  category('fitness', 'Fitness', 'orange'),
  category('personal', 'Personal', 'violet'),
]

/** Only the bar's own children, so the legend's tone swatches stay out of it. */
function bars(container: HTMLElement): Array<{ tone: string; left: string; width: string }> {
  const bar = container.querySelector('[role="img"]')
  if (!bar) throw new Error('no bar rendered')
  return [...bar.querySelectorAll<HTMLElement>('[data-tone]')].map((el) => ({
    tone: el.dataset.tone ?? '',
    left: el.style.left,
    width: el.style.width,
  }))
}

function renderDay(activities: Activity[]) {
  return render(
    <DailySummary
      summary={summariseDay(DAY, [], activities, CATEGORIES)}
      schedule={daySchedule(activities, CATEGORIES)}
      isToday={false}
    />,
  )
}

describe('DailySummary bar', () => {
  it('draws an afternoon entry in the afternoon, not against the left edge', () => {
    // The exact day from the bug report: a mid-morning entry and a 3 PM one.
    const { container } = renderDay([
      activity({ id: 'a', title: 'dwada', categoryId: 'personal', startTime: 622, duration: 45 }),
      activity({ id: 'b', title: 'dwa', categoryId: 'fitness', startTime: 907, duration: 45 }),
    ])

    expect(bars(container)).toEqual([
      { tone: 'violet', left: '43.19444444444444%', width: '3.125%' },
      { tone: 'orange', left: '62.98611111111111%', width: '3.125%' },
    ])
  })

  it('leaves midnight empty when nothing was logged there', () => {
    const { container } = renderDay([
      activity({ id: 'b', title: 'dwa', categoryId: 'fitness', startTime: 907, duration: 45 }),
    ])
    expect(bars(container).every((bar) => bar.left !== '0%')).toBe(true)
  })
})
