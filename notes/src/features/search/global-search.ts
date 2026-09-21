import { listItemsForSearch, listNotePreview } from '@/features/notes/list-note'
import type { Activity, Note, Todo } from '@/types'

export type SearchResultKind = 'note' | 'todo' | 'activity'

export interface SearchResult {
  kind: SearchResultKind
  id: string
  title: string
  subtitle: string
  /** Lower is better. */
  score: number
  noteKind?: 'document' | 'list'
}

function needleScore(haystack: string, needle: string): number | null {
  const text = haystack.toLowerCase()
  const q = needle.toLowerCase()
  if (!q) return null
  const index = text.indexOf(q)
  if (index < 0) return null
  // Title matches at the start rank above body matches deep in the text.
  return index + text.length * 0.001
}

export function globalSearch(
  query: string,
  notes: Note[],
  todos: Todo[],
  activities: Activity[],
  limit = 24,
): SearchResult[] {
  const needle = query.trim()
  if (!needle) return []

  const results: SearchResult[] = []

  for (const note of notes) {
    const body = note.kind === 'list' ? listItemsForSearch(note) : note.body
    const titleScore = needleScore(note.title, needle)
    const bodyScore = needleScore(body, needle)
    const score = titleScore ?? bodyScore
    if (score == null) continue
    results.push({
      kind: 'note',
      id: note.id,
      title: note.title,
      subtitle: note.kind === 'list' ? listNotePreview(note, 72) || 'List note' : 'Document',
      score: titleScore != null ? score : score + 50,
      noteKind: note.kind === 'list' ? 'list' : 'document',
    })
  }

  for (const todo of todos) {
    const haystack = [todo.title, todo.notes ?? ''].join('\n')
    const score = needleScore(haystack, needle)
    if (score == null) continue
    const titleScore = needleScore(todo.title, needle)
    results.push({
      kind: 'todo',
      id: todo.id,
      title: todo.title,
      subtitle: todo.plannedDate ? `Task · ${todo.plannedDate}` : 'Task · Someday',
      score: titleScore ?? score + 20,
    })
  }

  for (const activity of activities) {
    const haystack = [activity.title, activity.notes ?? ''].join('\n')
    const score = needleScore(haystack, needle)
    if (score == null) continue
    const titleScore = needleScore(activity.title, needle)
    results.push({
      kind: 'activity',
      id: activity.id,
      title: activity.title,
      subtitle: `Logged · ${activity.date}`,
      score: titleScore ?? score + 30,
    })
  }

  return results.sort((a, b) => a.score - b.score || a.title.localeCompare(b.title)).slice(0, limit)
}
