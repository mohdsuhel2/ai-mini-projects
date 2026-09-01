'use client'

import { useLiveQuery } from 'dexie-react-hooks'
import { useMemo } from 'react'
import { db } from '@/lib/db/database'
import { liveOnly } from '@/lib/db/records'
import { listCategories } from '@/features/categories/api'
import { listOpenTodos } from '@/features/todos/api'
import { sortActivities } from '@/features/activities/api'
import { getSettings, DEFAULT_SETTINGS } from '@/features/settings/api'
import { getTimer } from '@/features/timer/api'
import { summariseDay } from '@/features/analytics/summary'
import { listFolders, listNotes } from '@/features/notes/api'
import { buildFolderTree, rootNotes } from '@/features/notes/tree'
import type {
  Activity,
  Category,
  DailySummary,
  DayKey,
  Folder,
  FolderNode,
  Note,
  Settings,
  TimerState,
  Todo,
} from '@/types'

/**
 * `useLiveQuery` makes IndexedDB the reactive source of truth directly, which
 * is why there is no global store in this app: a write anywhere re-renders
 * every reader, with no second copy of the data to keep in step.
 */

export function useCategories(): Category[] | undefined {
  return useLiveQuery(() => listCategories(), [])
}

export function useCategoryMap(): Map<string, Category> {
  const categories = useCategories()
  return useMemo(() => new Map((categories ?? []).map((c) => [c.id, c])), [categories])
}

export function useOpenTodos(): Todo[] | undefined {
  return useLiveQuery(() => listOpenTodos(), [])
}

export function useTodosForDay(day: DayKey): Todo[] | undefined {
  return useLiveQuery(async () => {
    const rows = await db().todos.where('plannedDate').equals(day).toArray()
    return liveOnly(rows)
  }, [day])
}

export function useActivitiesForDay(day: DayKey): Activity[] | undefined {
  return useLiveQuery(async () => {
    const rows = await db().activities.where('date').equals(day).toArray()
    return sortActivities(liveOnly(rows))
  }, [day])
}

export function useSettings(): Settings {
  return useLiveQuery(() => getSettings(), [], DEFAULT_SETTINGS)
}

export function useTimer(): TimerState | undefined | null {
  return useLiveQuery(async () => (await getTimer()) ?? null, [])
}

export function useFolders(): Folder[] | undefined {
  return useLiveQuery(() => listFolders(), [])
}

export function useNotes(): Note[] | undefined {
  return useLiveQuery(() => listNotes(), [])
}

export function useNote(id: string | null): Note | undefined {
  return useLiveQuery(async () => (id ? await db().notes.get(id) : undefined), [id])
}

export interface NotesTree {
  roots: FolderNode[]
  unfiled: Note[]
  folders: Folder[]
  notes: Note[]
  loading: boolean
}

export function useNotesTree(): NotesTree {
  const folders = useFolders()
  const notes = useNotes()

  return useMemo(() => {
    if (!folders || !notes) {
      return { roots: [], unfiled: [], folders: [], notes: [], loading: true }
    }
    return {
      roots: buildFolderTree(folders, notes),
      unfiled: rootNotes(notes),
      folders,
      notes,
      loading: false,
    }
  }, [folders, notes])
}

export function useDailySummary(day: DayKey): DailySummary | undefined {
  const todos = useTodosForDay(day)
  const activities = useActivitiesForDay(day)
  const categories = useCategories()

  return useMemo(() => {
    if (!todos || !activities || !categories) return undefined
    return summariseDay(day, todos, activities, categories)
  }, [day, todos, activities, categories])
}
