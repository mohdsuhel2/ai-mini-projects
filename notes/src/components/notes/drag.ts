import type { Id } from '@/types'

/**
 * A private MIME type, so the tree only ever accepts drags that came from the
 * tree. A file or a text selection dragged in from elsewhere is ignored rather
 * than parsed into a nonsense move.
 */
export const DRAG_TYPE = 'application/x-simply-notes'

export interface DragPayload {
  kind: 'note' | 'folder'
  id: Id
}

export function setDragPayload(event: React.DragEvent, payload: DragPayload): void {
  event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(payload))
  event.dataTransfer.effectAllowed = 'move'
}

export function readDragPayload(event: React.DragEvent): DragPayload | null {
  const raw = event.dataTransfer.getData(DRAG_TYPE)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as DragPayload
    if (parsed.kind !== 'note' && parsed.kind !== 'folder') return null
    if (typeof parsed.id !== 'string') return null
    return parsed
  } catch {
    return null
  }
}

/**
 * During dragover the payload is unreadable for security reasons — only the
 * type list is exposed — so drop eligibility is decided from the type alone
 * and the real validation happens on drop.
 */
export function isTreeDrag(event: React.DragEvent): boolean {
  return event.dataTransfer.types.includes(DRAG_TYPE)
}

/** List-item reorder drags — separate from folder/note tree moves. */
export const LIST_ITEM_DRAG_TYPE = 'application/x-simply-notes-list-item'

export function setListItemDragPayload(event: React.DragEvent, itemId: Id): void {
  event.dataTransfer.setData(LIST_ITEM_DRAG_TYPE, itemId)
  event.dataTransfer.effectAllowed = 'move'
}

export function readListItemDragPayload(event: React.DragEvent): Id | null {
  const raw = event.dataTransfer.getData(LIST_ITEM_DRAG_TYPE)
  return typeof raw === 'string' && raw.length > 0 ? raw : null
}

export function isListItemDrag(event: React.DragEvent): boolean {
  return event.dataTransfer.types.includes(LIST_ITEM_DRAG_TYPE)
}
