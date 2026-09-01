import Dexie, { type EntityTable } from 'dexie'
import type { Activity, Category, Folder, Note, Settings, TimerState, Todo } from '@/types'
import { buildDefaultCategories } from './default-categories'

export const SETTINGS_KEY = 'settings'
export const TIMER_KEY = 'timer'

export class SimplyNotesDatabase extends Dexie {
  todos!: EntityTable<Todo, 'id'>
  activities!: EntityTable<Activity, 'id'>
  categories!: EntityTable<Category, 'id'>
  settings!: EntityTable<Settings, 'id'>
  timer!: EntityTable<TimerState, 'id'>
  notes!: EntityTable<Note, 'id'>
  folders!: EntityTable<Folder, 'id'>

  constructor() {
    super('simply-notes')

    this.version(1).stores({
      todos: 'id, plannedDate, status, categoryId, order, updatedAt, [status+plannedDate]',
      activities: 'id, date, categoryId, source, todoId, updatedAt',
      categories: 'id, name, scope, order',
      settings: 'id',
      timer: 'id',
    })

    // v2 adds notes and folders, and lets a todo have no date at all.
    // Existing todos are untouched: a missing plannedDate simply drops out of
    // the by-day index, which is what an undated task should do.
    this.version(2).stores({
      todos: 'id, plannedDate, status, categoryId, order, updatedAt, [status+plannedDate]',
      activities: 'id, date, categoryId, source, todoId, updatedAt',
      categories: 'id, name, scope, order',
      settings: 'id',
      timer: 'id',
      notes: 'id, folderId, updatedAt',
      folders: 'id, parentId, order',
    })

    this.on('populate', () => {
      const now = Date.now()
      void this.categories.bulkAdd(buildDefaultCategories(now))
      void this.settings.add({
        id: SETTINGS_KEY,
        theme: 'system',
        firstDayOfWeek: 1,
        onboarded: false,
        updatedAt: now,
      })
    })
  }
}

let instance: SimplyNotesDatabase | null = null

/**
 * Constructed lazily. Client components are also rendered on the server, and
 * touching IndexedDB there would throw; nothing calls this until the app has
 * mounted in the browser.
 */
export function db(): SimplyNotesDatabase {
  if (!instance) instance = new SimplyNotesDatabase()
  return instance
}

/** Test seam: drops the cached handle so a fresh database can be opened. */
export function resetDatabaseForTests(): void {
  instance = null
}
