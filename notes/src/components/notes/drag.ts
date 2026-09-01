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
