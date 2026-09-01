import { describe, expect, it } from 'vitest'
import { groupIdFor, groupTodos } from './grouping'
import type { Todo } from '@/types'

const TODAY = '2026-09-01'

const todo = (plannedDate: string | null, extra: Partial<Todo> = {}): Todo => ({
  id: extra.id ?? `${plannedDate ?? 'none'}-${Math.random()}`,
  title: extra.title ?? plannedDate ?? 'Someday task',
  categoryId: null,
  plannedDate,
  plannedTime: extra.plannedTime ?? null,
  estimatedDuration: null,
  status: 'OPEN',
  notes: null,
  createdAt: 0,
  completedAt: null,
  actualDuration: null,
  order: extra.order ?? 10,
  updatedAt: 0,
  deletedAt: null,
})

// TODAY is Tuesday 1 September 2026. With weeks starting Monday:
//   this week  Mon 31 Aug – Sun 6 Sep
//   next week  Mon 7 Sep  – Sun 13 Sep
describe('groupIdFor', () => {
  it.each([
    ['2026-08-31', 'overdue'],
    ['2026-09-01', 'today'],
    ['2026-09-02', 'tomorrow'],
    ['2026-09-03', 'thisWeek'],
    ['2026-09-06', 'thisWeek'],
    ['2026-09-07', 'nextWeek'],
    ['2026-09-13', 'nextWeek'],
    ['2026-09-14', 'upcoming'],
    ['2027-01-01', 'upcoming'],
  ])('places %s in %s', (day, expected) => {
    expect(groupIdFor(day, TODAY)).toBe(expected)
  })

  it('respects a Sunday week start', () => {
    // With weeks starting Sunday, this week ends Sat 5 Sep.
    expect(groupIdFor('2026-09-06', TODAY, 0)).toBe('nextWeek')
    expect(groupIdFor('2026-09-05', TODAY, 0)).toBe('thisWeek')
  })

  it('never puts today or tomorrow in a week bucket', () => {
    expect(groupIdFor('2026-09-01', TODAY)).toBe('today')
    expect(groupIdFor('2026-09-02', TODAY)).toBe('tomorrow')
  })

  it('places a dateless todo in someday', () => {
    expect(groupIdFor(null, TODAY)).toBe('someday')
  })
})

describe('groupTodos', () => {
  it('always shows Today, even when empty', () => {
    expect(groupTodos([], TODAY).map((g) => g.id)).toEqual(['today'])
  })

  it('drops other empty groups', () => {
    const groups = groupTodos([todo(TODAY)], TODAY)
    expect(groups.map((g) => g.id)).toEqual(['today'])
  })

  it('orders groups from most to least urgent', () => {
    const groups = groupTodos(
      [
        todo('2026-10-20'),
        todo('2026-09-10'),
        todo('2026-09-04'),
        todo('2026-08-20'),
        todo('2026-09-02'),
        todo(TODAY),
        todo(null),
      ],
      TODAY,
    )
    expect(groups.map((g) => g.id)).toEqual([
      'overdue',
      'today',
      'tomorrow',
      'thisWeek',
      'nextWeek',
      'upcoming',
      'someday',
    ])
  })

  it('drops week buckets that have nothing in them', () => {
    const groups = groupTodos([todo(TODAY), todo('2026-10-20')], TODAY)
    expect(groups.map((g) => g.id)).toEqual(['today', 'upcoming'])
  })

  it('sorts multi-day groups by date rather than manual order', () => {
    const groups = groupTodos(
      [todo('2026-09-06', { order: 10 }), todo('2026-09-04', { order: 99 })],
      TODAY,
    )
    const thisWeek = groups.find((g) => g.id === 'thisWeek')!
    expect(thisWeek.todos.map((t) => t.plannedDate)).toEqual(['2026-09-04', '2026-09-06'])
  })

  it('sorts overdue oldest first', () => {
    const groups = groupTodos([todo('2026-08-30'), todo('2026-08-01')], TODAY)
    const overdue = groups.find((g) => g.id === 'overdue')!
    expect(overdue.todos.map((t) => t.plannedDate)).toEqual(['2026-08-01', '2026-08-30'])
  })

  it('collects dateless todos into Someday, after everything dated', () => {
    const groups = groupTodos(
      [todo(null, { title: 'One day' }), todo(TODAY, { title: 'Now' }), todo('2026-09-20')],
      TODAY,
    )
    expect(groups.map((g) => g.id)).toEqual(['today', 'upcoming', 'someday'])
    expect(groups.at(-1)!.todos.map((t) => t.title)).toEqual(['One day'])
  })

  it('sorts dated groups without tripping over a null date', () => {
    // Both dated todos land in Upcoming; the null one must not break the sort.
    const groups = groupTodos([todo(null), todo('2026-10-20'), todo('2026-09-20')], TODAY)
    const upcoming = groups.find((g) => g.id === 'upcoming')!
    expect(upcoming.todos.map((t) => t.plannedDate)).toEqual(['2026-09-20', '2026-10-20'])
  })

  it('keeps manual order within today', () => {
    const groups = groupTodos(
      [todo(TODAY, { title: 'second', order: 20 }), todo(TODAY, { title: 'first', order: 10 })],
      TODAY,
    )
    // Today preserves the order it was handed, which listOpenTodos has sorted.
    expect(groups[0].todos.map((t) => t.title)).toEqual(['second', 'first'])
  })
})
