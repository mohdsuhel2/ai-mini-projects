import { beforeEach, describe, expect, it } from 'vitest'
import { db } from '@/lib/db/database'
import { freshDatabase } from '@/lib/db/test-utils'
import { cutoffFor, describeCleanup, runCleanup, selectForCleanup } from './cleanup'
import type { Activity, Folder, Note, Todo } from '@/types'

const TODAY = '2026-09-10'

const activity = (id: string, date: string, deleted = false): Activity => ({
  id, title: id, categoryId: null, date, startTime: null, endTime: null, duration: 30,
  source: 'MANUAL', todoId: null, notes: null, createdAt: 0, updatedAt: 0,
  deletedAt: deleted ? 1 : null,
})

const todo = (id: string, date: string | null, status: 'OPEN' | 'COMPLETED', deleted = false): Todo => ({
  id, title: id, categoryId: null, plannedDate: date, plannedTime: null,
  estimatedDuration: null, status, notes: null, createdAt: 0, completedAt: null,
  actualDuration: null, order: 10, updatedAt: 0, deletedAt: deleted ? 1 : null,
})

const note = (id: string, deleted = false): Note => ({
  id, title: id, body: '', folderId: null, createdAt: 0, updatedAt: 0,
  deletedAt: deleted ? 1 : null,
})

const folder = (id: string, deleted = false): Folder => ({
  id, name: id, parentId: null, order: 10, createdAt: 0, updatedAt: 0,
  deletedAt: deleted ? 1 : null,
})

const empty = { activities: [], todos: [], notes: [], folders: [] }

describe('cutoffFor', () => {
  it('keeps a full week including today', () => {
    expect(cutoffFor('week', TODAY)).toBe('2026-09-04')
  })

  it('widens with the window', () => {
    expect(cutoffFor('month', TODAY)).toBe('2026-08-12')
    expect(cutoffFor('quarter', TODAY)).toBe('2026-06-13')
  })
})

describe('selectForCleanup', () => {
  const cutoff = cutoffFor('week', TODAY) // 2026-09-04

  it('sweeps activities from before the cutoff and keeps the rest', () => {
    const picked = selectForCleanup(
      { ...empty, activities: [activity('old', '2026-09-03'), activity('edge', cutoff), activity('new', TODAY)] },
      cutoff,
    )
    expect(picked.activities).toEqual(['old'])
  })

  it('never sweeps an open task, however old', () => {
    const picked = selectForCleanup(
      { ...empty, todos: [todo('ancient', '2020-01-01', 'OPEN'), todo('done', '2020-01-01', 'COMPLETED')] },
      cutoff,
    )
    expect(picked.todos).toEqual(['done'])
  })

  it('leaves an undated task alone — it belongs to no day to be old in', () => {
    const picked = selectForCleanup({ ...empty, todos: [todo('someday', null, 'COMPLETED')] }, cutoff)
    expect(picked.todos).toEqual([])
  })

  it('never sweeps notes or folders by age', () => {
    const picked = selectForCleanup({ ...empty, notes: [note('keep')], folders: [folder('keep')] }, cutoff)
    expect(picked.notes).toEqual([])
    expect(picked.folders).toEqual([])
  })

  it('purges anything already in the bin, whatever its date', () => {
    const picked = selectForCleanup(
      {
        activities: [activity('a', TODAY, true)],
        todos: [todo('t', TODAY, 'OPEN', true)],
        notes: [note('n', true)],
        folders: [folder('f', true)],
      },
      cutoff,
    )
    expect(picked).toEqual({ activities: ['a'], todos: ['t'], notes: ['n'], folders: ['f'] })
  })
})

describe('describeCleanup', () => {
  it('separates what aged out from what was already binned', () => {
    const preview = describeCleanup(
      {
        activities: [activity('old', '2026-01-01'), activity('binned', TODAY, true)],
        todos: [], notes: [note('binnedNote', true)], folders: [],
      },
      cutoffFor('week', TODAY),
    )
    expect(preview).toMatchObject({ agedOut: 1, binned: 2, total: 3 })
  })

  it('reports nothing to do on an empty database', () => {
    expect(describeCleanup(empty, cutoffFor('year', TODAY)).total).toBe(0)
  })
})

describe('runCleanup', () => {
  beforeEach(async () => {
    await freshDatabase()
  })

  it('removes exactly what the preview promised and nothing else', async () => {
    await db().activities.bulkAdd([activity('old', '2026-01-01'), activity('recent', TODAY)])
    await db().todos.bulkAdd([
      todo('doneOld', '2026-01-01', 'COMPLETED'),
      todo('openOld', '2026-01-01', 'OPEN'),
    ])
    await db().notes.add(note('keeper'))

    const result = await runCleanup('week', TODAY)
    expect(result.total).toBe(2)

    expect((await db().activities.toArray()).map((a) => a.id)).toEqual(['recent'])
    expect((await db().todos.toArray()).map((t) => t.id).sort()).toEqual(['openOld'])
    expect(await db().notes.count()).toBe(1)
  })

  it('is a hard delete, so the rows are gone rather than hidden', async () => {
    await db().activities.add(activity('old', '2026-01-01'))
    await runCleanup('week', TODAY)
    expect(await db().activities.get('old')).toBeUndefined()
  })

  it('does nothing when there is nothing old enough', async () => {
    await db().activities.add(activity('recent', TODAY))
    const result = await runCleanup('year', TODAY)
    expect(result.total).toBe(0)
    expect(await db().activities.count()).toBe(1)
  })
})
