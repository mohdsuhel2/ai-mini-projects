import type { Id, Instant, ListNoteItem, Note } from '@/types'

export const UNTITLED_LIST = 'Untitled list'

export function isListNote(note: Note): boolean {
  return note.kind === 'list'
}

export function isItemPinned(item: ListNoteItem): boolean {
  return item.pinnedAt != null
}

export function isItemFlagged(item: ListNoteItem): boolean {
  return item.flaggedAt != null
}

export function activeListItems(note: Note): ListNoteItem[] {
  return sortedActiveListItems(note)
}

export function sortedActiveListItems(note: Note): ListNoteItem[] {
  const active = (note.items ?? []).filter((item) => item.archivedAt == null)
  const pinned = active
    .filter((item) => item.pinnedAt != null)
    .sort((a, b) => (b.pinnedAt ?? 0) - (a.pinnedAt ?? 0))
  const unpinned = active
    .filter((item) => item.pinnedAt == null)
    .sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0))
  return [...pinned, ...unpinned]
}

export function archivedListItems(note: Note): ListNoteItem[] {
  return (note.items ?? [])
    .filter((item) => item.archivedAt != null)
    .sort((a, b) => (b.archivedAt ?? 0) - (a.archivedAt ?? 0))
}

export function listNotePreview(note: Note, maxLen = 60): string {
  const active = activeListItems(note)
  if (active.length === 0) {
    const archived = archivedListItems(note)
    return archived.length > 0 ? `${archived.length} archived` : ''
  }
  const first = active[0].text.replace(/\s+/g, ' ').trim()
  const clipped = first.length > maxLen ? `${first.slice(0, maxLen - 1)}…` : first
  const flagged = active.filter(isItemFlagged).length
  const suffix =
    flagged > 0 ? ` · ${flagged} flagged` : active.length > 1 ? ` · ${active.length} items` : ''
  if (active.length === 1) return clipped + (flagged > 0 ? ' · flagged' : '')
  return `${clipped}${suffix}`
}

export function listItemsForSearch(note: Note): string {
  return (note.items ?? []).map((item) => item.text).join('\n')
}

export function sortArchivedLast(items: ListNoteItem[]): ListNoteItem[] {
  const active = items.filter((item) => item.archivedAt == null)
  const archived = items
    .filter((item) => item.archivedAt != null)
    .sort((a, b) => (a.archivedAt ?? 0) - (b.archivedAt ?? 0))
  return [...active, ...archived]
}

export function patchListItem(
  items: ListNoteItem[],
  itemId: Id,
  patch: Partial<Pick<ListNoteItem, 'text' | 'archivedAt' | 'pinnedAt' | 'flaggedAt'>>,
): ListNoteItem[] {
  return items.map((item) => (item.id === itemId ? { ...item, ...patch } : item))
}

export function withArchivedAt(items: ListNoteItem[], itemId: Id, archivedAt: Instant | null): ListNoteItem[] {
  const patch =
    archivedAt != null
      ? { archivedAt, pinnedAt: null }
      : { archivedAt }
  return sortArchivedLast(patchListItem(items, itemId, patch))
}

export function toggleItemTimestamp(
  items: ListNoteItem[],
  itemId: Id,
  field: 'pinnedAt' | 'flaggedAt',
  stamp: Instant,
): ListNoteItem[] {
  return items.map((item) => {
    if (item.id !== itemId) return item
    const active = item[field] != null
    return { ...item, [field]: active ? null : stamp }
  })
}

export function removeListItem(items: ListNoteItem[], itemId: Id): ListNoteItem[] {
  return items.filter((item) => item.id !== itemId)
}

export function insertListItem(items: ListNoteItem[], item: ListNoteItem): ListNoteItem[] {
  return sortArchivedLast([...items.filter((entry) => entry.id !== item.id), item])
}
