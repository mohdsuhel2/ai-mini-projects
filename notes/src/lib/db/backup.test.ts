import { beforeEach, describe, expect, it } from 'vitest'
import { db } from './database'
import { freshDatabase } from './test-utils'
import { BackupError, clearAllData, exportBackup, importBackup, parseBackup } from './backup'
import { createTodo, completeTodo, listOpenTodos } from '@/features/todos/api'
import { createActivity, listActivitiesForDay } from '@/features/activities/api'
import { todayKey } from '@/lib/date/day-key'

const TODAY = todayKey()

beforeEach(async () => {
  await freshDatabase()
})

describe('export', () => {
  it('includes every table and an honest count', async () => {
    await createTodo({ title: 'Task', plannedDate: TODAY })
    await createActivity({ title: 'Lunch', date: TODAY, duration: 45 })

    const backup = await exportBackup()
    expect(backup.format).toBe('simply-notes-backup')
    expect(backup.counts.todos).toBe(1)
    expect(backup.counts.activities).toBe(1)
    expect(backup.data.categories.length).toBeGreaterThan(0)
  })
})

describe('parseBackup', () => {
  it('rejects malformed JSON', () => {
    expect(() => parseBackup('{oops')).toThrow(BackupError)
  })

  it('rejects a file from another application', () => {
    expect(() => parseBackup(JSON.stringify({ format: 'something-else' }))).toThrow(BackupError)
  })

  it('rejects a backup from a newer version', () => {
    expect(() =>
      parseBackup(JSON.stringify({ format: 'simply-notes-backup', version: 99, data: {} })),
    ).toThrow(/newer version/)
  })

  it('rejects a record missing its id', () => {
    const file = {
      format: 'simply-notes-backup',
      version: 1,
      data: { todos: [{ title: 'No id' }] },
    }
    expect(() => parseBackup(JSON.stringify(file))).toThrow(/missing an id/)
  })

  it('tolerates absent tables', () => {
    const parsed = parseBackup(
      JSON.stringify({ format: 'simply-notes-backup', version: 1, data: { todos: [] } }),
    )
    expect(parsed.data.activities).toEqual([])
  })
})

describe('round trip', () => {
  it('restores todos, activities and their link after a wipe', async () => {
    const id = await createTodo({ title: 'Write the spec', plannedDate: TODAY })
    await completeTodo(id, 50)
    await createActivity({ title: 'Coffee', date: TODAY, duration: 15 })

    const serialised = JSON.stringify(await exportBackup())
    await clearAllData()
    expect(await listActivitiesForDay(TODAY)).toEqual([])

    await importBackup(parseBackup(serialised), 'replace')

    const activities = await listActivitiesForDay(TODAY)
    expect(activities.map((a) => a.title).sort()).toEqual(['Coffee', 'Write the spec'])
    expect(activities.find((a) => a.source === 'TODO_COMPLETION')?.todoId).toBe(id)
    expect((await db().todos.get(id))?.status).toBe('COMPLETED')
  })

  it('replace mode discards records absent from the backup', async () => {
    const serialised = JSON.stringify(await exportBackup())
    await createTodo({ title: 'Added after the export', plannedDate: TODAY })

    await importBackup(parseBackup(serialised), 'replace')
    expect(await listOpenTodos()).toEqual([])
  })

  it('merge mode keeps local records and the newer copy of a conflict', async () => {
    const id = await createTodo({ title: 'Original', plannedDate: TODAY })
    const backup = parseBackup(JSON.stringify(await exportBackup()))

    await db().todos.update(id, { title: 'Edited locally', updatedAt: Date.now() + 10_000 })
    await createTodo({ title: 'Local only', plannedDate: TODAY })

    await importBackup(backup, 'merge')

    const titles = (await listOpenTodos()).map((t) => t.title).sort()
    expect(titles).toEqual(['Edited locally', 'Local only'])
  })
})

describe('version compatibility', () => {
  it('imports a version 1 backup, which predates notes and folders', async () => {
    const v1 = {
      format: 'simply-notes-backup',
      version: 1,
      exportedAt: '2026-08-01T00:00:00.000Z',
      counts: { todos: 1 },
      data: {
        todos: [
          {
            id: 'legacy-1',
            title: 'From the old version',
            categoryId: null,
            plannedDate: TODAY,
            plannedTime: null,
            estimatedDuration: null,
            status: 'OPEN',
            notes: null,
            createdAt: 1,
            completedAt: null,
            actualDuration: null,
            order: 10,
            updatedAt: 1,
            deletedAt: null,
          },
        ],
        activities: [],
        categories: [],
        settings: [],
      },
    }

    const parsed = parseBackup(JSON.stringify(v1))
    expect(parsed.data.notes).toEqual([])
    expect(parsed.data.folders).toEqual([])

    await importBackup(parsed, 'merge')
    expect((await listOpenTodos()).map((t) => t.title)).toContain('From the old version')
  })

  it('carries notes and folders through a round trip', async () => {
    const { createFolder, createNote, listFolders, listNotes } = await import(
      '@/features/notes/api'
    )
    const work = await createFolder('Work')
    const service = await createFolder('Service 1', work)
    await createNote({ title: 'Runbook', body: '## Steps', folderId: service })

    const serialised = JSON.stringify(await exportBackup())
    await clearAllData()
    expect(await listNotes()).toEqual([])

    await importBackup(parseBackup(serialised), 'replace')
    expect((await listFolders()).map((f) => f.name).sort()).toEqual(['Service 1', 'Work'])
    const [note] = await listNotes()
    expect(note).toMatchObject({ title: 'Runbook', body: '## Steps', folderId: service })
  })
})

describe('clearAllData', () => {
  it('leaves a usable app with default categories reseeded', async () => {
    await createTodo({ title: 'Task', plannedDate: TODAY })
    await clearAllData()

    expect(await listOpenTodos()).toEqual([])
    expect(await db().categories.count()).toBeGreaterThan(0)
    expect((await db().settings.get('settings'))?.onboarded).toBe(true)
  })
})
