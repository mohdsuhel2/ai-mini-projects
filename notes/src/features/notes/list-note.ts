import type { Id, Instant, ListNoteItem, Note } from '@/types'

export const UNTITLED_LIST = 'Untitled list'

export function isListNote(note: Note): boolean {
  return note.kind === 'list'
}

export function isItemPinned(item: ListNoteItem): boolean {
  return item.pinnedAt != null
}

function itemSortKey(item: ListNoteItem): number {
  return item.order ?? item.createdAt ?? 0
}

export function activeListItems(note: Note): ListNoteItem[] {
  return sortedActiveListItems(note)
}

export function sortedActiveListItems(note: Note): ListNoteItem[] {
  return (note.items ?? [])
    .filter((item) => item.archivedAt == null)
    .sort((a, b) => itemSortKey(a) - itemSortKey(b))
}

export function archivedListItems(note: Note): ListNoteItem[] {
  return (note.items ?? [])
    .filter((item) => item.archivedAt != null)
    .sort((a, b) => (b.archivedAt ?? 0) - (a.archivedAt ?? 0))
}

export function nextListItemOrder(items: ListNoteItem[]): number {
  const active = items.filter((item) => item.archivedAt == null)
  const max = active.reduce((highest, item) => Math.max(highest, itemSortKey(item)), 0)
  return max + 10
}

/** Active ids in the order they would appear after a drop. */
export function buildPreviewIds(
  ids: Id[],
  draggedId: Id,
  insertBeforeId: Id | null,
  lockedIds: ReadonlySet<Id> = new Set(),
): Id[] {
  if (lockedIds.has(draggedId)) return ids

  const movable = ids.filter((id) => !lockedIds.has(id))
  const movableWithoutDragged = movable.filter((id) => id !== draggedId)
  let insertAt =
    insertBeforeId === null
      ? movableWithoutDragged.length
      : movableWithoutDragged.findIndex((id) => id === insertBeforeId)
  if (insertAt < 0) insertAt = movableWithoutDragged.length

  const reorderedMovable = [...movableWithoutDragged]
  reorderedMovable.splice(insertAt, 0, draggedId)

  const next = [...ids]
  let movableIndex = 0
  for (let index = 0; index < ids.length; index++) {
    if (!lockedIds.has(ids[index])) {
      next[index] = reorderedMovable[movableIndex++]
    }
  }
  return next
}

/** Vertical shift (px) for each row while a drag preview is active. */
export function layoutShifts(
  ids: Id[],
  previewIds: Id[],
  heights: Map<Id, number>,
  draggedId: Id,
): Map<Id, number> {
  const tops = (order: Id[]) => {
    const map = new Map<Id, number>()
    let y = 0
    for (const id of order) {
      map.set(id, y)
      y += heights.get(id) ?? 0
    }
    return map
  }

  const originalTops = tops(ids)
  const previewTops = tops(previewIds)
  const shifts = new Map<Id, number>()

  for (const id of ids) {
    if (id === draggedId) continue
    shifts.set(id, (previewTops.get(id) ?? 0) - (originalTops.get(id) ?? 0))
  }

  return shifts
}

export function reorderActiveListItems(
  items: ListNoteItem[],
  draggedId: Id,
  insertBeforeId: Id | null,
): ListNoteItem[] {
  const active = items
    .filter((item) => item.archivedAt == null)
    .sort((a, b) => itemSortKey(a) - itemSortKey(b))

  const fromIndex = active.findIndex((item) => item.id === draggedId)
  if (fromIndex < 0) return items
  if (isItemPinned(active[fromIndex])) return items

  const lockedIds = new Set(active.filter(isItemPinned).map((item) => item.id))
  const previewIds = buildPreviewIds(
    active.map((item) => item.id),
    draggedId,
    insertBeforeId,
    lockedIds,
  )

  const orderById = new Map(previewIds.map((id, index) => [id, (index + 1) * 10]))
  return items.map((item) => (orderById.has(item.id) ? { ...item, order: orderById.get(item.id) } : item))
}

export function listNotePreview(note: Note, maxLen = 60): string {
  const active = activeListItems(note)
  if (active.length === 0) {
    const archived = archivedListItems(note)
    return archived.length > 0 ? `${archived.length} archived` : ''
  }
  const first = active[0].text.replace(/\s+/g, ' ').trim()
  const clipped = first.length > maxLen ? `${first.slice(0, maxLen - 1)}…` : first
  if (active.length === 1) return clipped
  return `${clipped} · ${active.length} items`
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
  patch: Partial<Pick<ListNoteItem, 'text' | 'archivedAt' | 'pinnedAt' | 'order'>>,
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
  field: 'pinnedAt',
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
