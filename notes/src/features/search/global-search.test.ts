import { describe, expect, it } from 'vitest'
import { globalSearch } from './global-search'
import type { Activity, Note, Todo } from '@/types'

const notes: Note[] = [
  {
    id: 'n1',
    title: 'Deploy runbook',
    kind: 'document',
    body: 'Steps for release',
    folderId: null,
    createdAt: 0,
    updatedAt: 0,
  },
  {
    id: 'n2',
    title: 'Groceries',
    kind: 'list',
    body: '',
    items: [{ id: 'i1', text: 'milk', archivedAt: null }],
    folderId: null,
    createdAt: 0,
    updatedAt: 0,
  },
]

const todos: Todo[] = [
  {
    id: 't1',
    title: 'Ship deploy',
    plannedDate: '2026-09-18',
    status: 'OPEN',
    order: 10,
    createdAt: 0,
    updatedAt: 0,
  },
]

const activities: Activity[] = [
  {
    id: 'a1',
    title: 'Morning deploy check',
    date: '2026-09-18',
    source: 'MANUAL',
    createdAt: 0,
    updatedAt: 0,
  },
]

describe('globalSearch', () => {
  it('finds matches across notes, todos, and activities', () => {
    const results = globalSearch('deploy', notes, todos, activities)
    expect(results.map((r) => r.kind)).toEqual(['note', 'todo', 'activity'])
  })

  it('finds list item text inside list notes', () => {
    const results = globalSearch('milk', notes, todos, activities)
    expect(results).toHaveLength(1)
    expect(results[0].id).toBe('n2')
  })
})
