import { db, SETTINGS_KEY } from '@/lib/db/database'
import { buildDefaultCategories } from '@/lib/db/default-categories'
import { now } from '@/lib/db/records'
import type { Activity, BackupFile, Category, Folder, Note, Settings, Todo } from '@/types'

export const BACKUP_FORMAT = 'simply-notes-backup'
export const BACKUP_VERSION = 2

export async function exportBackup(): Promise<BackupFile> {
  const [todos, activities, categories, settings, notes, folders] = await Promise.all([
    db().todos.toArray(),
    db().activities.toArray(),
    db().categories.toArray(),
    db().settings.toArray(),
    db().notes.toArray(),
    db().folders.toArray(),
  ])

  return {
    format: BACKUP_FORMAT,
    version: BACKUP_VERSION,
    exportedAt: new Date().toISOString(),
    counts: {
      todos: todos.length,
      activities: activities.length,
      categories: categories.length,
      notes: notes.length,
      folders: folders.length,
    },
    data: { todos, activities, categories, settings, notes, folders },
  }
}

export class BackupError extends Error {}

function expectArray<T>(value: unknown, field: string): T[] {
  if (value == null) return []
  if (!Array.isArray(value)) throw new BackupError(`"${field}" should be a list`)
  return value as T[]
}

/**
 * Validates the envelope before touching the database. A partial import is
 * worse than a refused one: the user cannot tell which half survived.
 */
export function parseBackup(raw: string): BackupFile {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    throw new BackupError('That file is not valid JSON.')
  }

  if (typeof parsed !== 'object' || parsed === null) {
    throw new BackupError('That file does not look like a Simply Notes backup.')
  }

  const candidate = parsed as Partial<BackupFile>
  if (candidate.format !== BACKUP_FORMAT) {
    throw new BackupError('That file was not exported from Simply Notes.')
  }
  if (typeof candidate.version !== 'number' || candidate.version > BACKUP_VERSION) {
    throw new BackupError('That backup was made by a newer version of Simply Notes.')
  }
  if (typeof candidate.data !== 'object' || candidate.data === null) {
    throw new BackupError('That backup has no data in it.')
  }

  const data = candidate.data as Partial<BackupFile['data']>
  const todos = expectArray<Todo>(data.todos, 'todos')
  const activities = expectArray<Activity>(data.activities, 'activities')
  const categories = expectArray<Category>(data.categories, 'categories')
  const settings = expectArray<Settings>(data.settings, 'settings')
  // Absent in version 1 backups, which must still import cleanly.
  const notes = expectArray<Note>(data.notes, 'notes')
  const folders = expectArray<Folder>(data.folders, 'folders')

  for (const todo of todos) {
    if (typeof todo.id !== 'string' || typeof todo.title !== 'string') {
      throw new BackupError('A task in that backup is missing an id or title.')
    }
  }
  for (const activity of activities) {
    if (typeof activity.id !== 'string' || typeof activity.title !== 'string') {
      throw new BackupError('An activity in that backup is missing an id or title.')
    }
  }

  return {
    format: BACKUP_FORMAT,
    version: candidate.version,
    exportedAt: typeof candidate.exportedAt === 'string' ? candidate.exportedAt : '',
    counts: candidate.counts ?? {},
    data: { todos, activities, categories, settings, notes, folders },
  }
}

export type ImportMode = 'replace' | 'merge'

export interface ImportResult {
  todos: number
  activities: number
  categories: number
  notes: number
}

/**
 * `replace` wipes first; `merge` keeps whichever copy of a record was written
 * last, which is also the rule a future cloud sync will need.
 */
export async function importBackup(backup: BackupFile, mode: ImportMode): Promise<ImportResult> {
  const { todos, activities, categories, settings, notes, folders } = backup.data

  // Dexie's varargs form caps at five tables; the array form has no limit.
  await db().transaction(
    'rw',
    [db().todos, db().activities, db().categories, db().settings, db().notes, db().folders],
    async () => {
      if (mode === 'replace') {
        await Promise.all([
          db().todos.clear(),
          db().activities.clear(),
          db().categories.clear(),
          db().settings.clear(),
          db().notes.clear(),
          db().folders.clear(),
        ])
        await db().categories.bulkPut(categories)
        await db().todos.bulkPut(todos)
        await db().activities.bulkPut(activities)
        await db().settings.bulkPut(settings)
        await db().folders.bulkPut(folders)
        await db().notes.bulkPut(notes)
        return
      }

      await mergeNewest((id) => db().categories.get(id), (rows) => db().categories.bulkPut(rows), categories)
      await mergeNewest((id) => db().todos.get(id), (rows) => db().todos.bulkPut(rows), todos)
      await mergeNewest((id) => db().activities.get(id), (rows) => db().activities.bulkPut(rows), activities)
      await mergeNewest((id) => db().folders.get(id), (rows) => db().folders.bulkPut(rows), folders)
      await mergeNewest((id) => db().notes.get(id), (rows) => db().notes.bulkPut(rows), notes)
    },
  )

  return {
    todos: todos.length,
    activities: activities.length,
    categories: categories.length,
    notes: notes.length,
  }
}

interface Timestamped {
  id: string
  updatedAt?: number
}

/** Last write wins, per record — the same rule a future cloud sync will need. */
async function mergeNewest<T extends Timestamped>(
  get: (id: string) => Promise<T | undefined>,
  put: (rows: T[]) => Promise<unknown>,
  incoming: T[],
): Promise<void> {
  const winners: T[] = []
  for (const record of incoming) {
    const existing = await get(record.id)
    if (!existing || (record.updatedAt ?? 0) >= (existing.updatedAt ?? 0)) {
      winners.push(record)
    }
  }
  if (winners.length > 0) await put(winners)
}

/** Wipes everything and reseeds the defaults, leaving a usable empty app. */
export async function clearAllData(): Promise<void> {
  const stamp = now()
  await db().transaction(
    'rw',
    [
      db().todos,
      db().activities,
      db().categories,
      db().settings,
      db().timer,
      db().notes,
      db().folders,
    ],
    async () => {
      await Promise.all([
        db().todos.clear(),
        db().activities.clear(),
        db().categories.clear(),
        db().settings.clear(),
        db().timer.clear(),
        db().notes.clear(),
        db().folders.clear(),
      ])
      await db().categories.bulkAdd(buildDefaultCategories(stamp))
      await db().settings.add({
        id: SETTINGS_KEY,
        theme: 'system',
        firstDayOfWeek: 1,
        onboarded: true,
        updatedAt: stamp,
      })
    },
  )
}

export function backupFilename(date: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `simply-notes-${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}.json`
}
