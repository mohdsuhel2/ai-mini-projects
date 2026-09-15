'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { buildPreviewIds, layoutShifts } from '@/features/notes/list-note'
import type { Id } from '@/types'

interface DragSession {
  id: Id
  startY: number
  insertBeforeId: Id | null
}

interface UsePointerListReorderOptions {
  lockedIds?: ReadonlySet<Id>
}

/**
 * Pointer-driven list reorder: the dragged row follows the cursor and sibling
 * rows animate into their preview positions in real time.
 */
export function usePointerListReorder(
  itemIds: Id[],
  onCommit: (draggedId: Id, insertBeforeId: Id | null) => void,
  { lockedIds = new Set<Id>() }: UsePointerListReorderOptions = {},
) {
  const sessionRef = useRef<DragSession | null>(null)
  const itemIdsRef = useRef(itemIds)
  const lockedIdsRef = useRef(lockedIds)
  const heightsRef = useRef(new Map<Id, number>())

  useEffect(() => {
    itemIdsRef.current = itemIds
    lockedIdsRef.current = lockedIds
  }, [itemIds, lockedIds])

  const [draggingId, setDraggingId] = useState<Id | null>(null)
  const [dragDeltaY, setDragDeltaY] = useState(0)
  const [insertBeforeId, setInsertBeforeId] = useState<Id | null>(null)
  const [layoutShiftById, setLayoutShiftById] = useState<Map<Id, number>>(new Map())
  const rowRefs = useRef(new Map<Id, HTMLLIElement>())

  const setRowRef = useCallback((id: Id, node: HTMLLIElement | null) => {
    if (node) rowRefs.current.set(id, node)
    else rowRefs.current.delete(id)
  }, [])

  const measureHeights = useCallback(() => {
    const heights = new Map<Id, number>()
    for (const id of itemIdsRef.current) {
      const row = rowRefs.current.get(id)
      if (!row) continue
      heights.set(id, row.getBoundingClientRect().height)
    }
    heightsRef.current = heights
    return heights
  }, [])

  const updatePreviewLayout = useCallback((session: DragSession) => {
    const ids = itemIdsRef.current
    const previewIds = buildPreviewIds(ids, session.id, session.insertBeforeId, lockedIdsRef.current)
    const shifts = layoutShifts(ids, previewIds, heightsRef.current, session.id)
    for (const id of lockedIdsRef.current) shifts.set(id, 0)
    setLayoutShiftById(shifts)
  }, [])

  const resolveInsertBefore = useCallback((clientY: number, draggedId: Id): Id | null => {
    const ids = itemIdsRef.current
    const locked = lockedIdsRef.current

    for (let index = 0; index < ids.length; index++) {
      const id = ids[index]
      if (id === draggedId) continue
      const row = rowRefs.current.get(id)
      if (!row) continue
      const rect = row.getBoundingClientRect()
      if (clientY < rect.top || clientY > rect.bottom) continue

      if (locked.has(id)) {
        const midpoint = rect.top + rect.height / 2
        const searchFrom = clientY < midpoint ? index : index + 1
        const nextUnlocked = ids
          .slice(searchFrom)
          .find((candidate) => candidate !== draggedId && !locked.has(candidate))
        return nextUnlocked ?? null
      }

      if (clientY < rect.top + rect.height / 2) return id
      return ids.slice(index + 1).find((candidate) => candidate !== draggedId && !locked.has(candidate)) ?? null
    }

    for (const id of ids) {
      if (id === draggedId || locked.has(id)) continue
      const row = rowRefs.current.get(id)
      if (!row) continue
      const rect = row.getBoundingClientRect()
      if (clientY < rect.top + rect.height / 2) return id
    }

    return null
  }, [])

  const startDrag = useCallback(
    (id: Id, clientY: number) => {
      if (lockedIdsRef.current.has(id)) return
      measureHeights()
      const next: DragSession = {
        id,
        startY: clientY,
        insertBeforeId: resolveInsertBefore(clientY, id),
      }
      sessionRef.current = next
      setDraggingId(id)
      setDragDeltaY(0)
      setInsertBeforeId(next.insertBeforeId)
      updatePreviewLayout(next)
    },
    [measureHeights, resolveInsertBefore, updatePreviewLayout],
  )

  const finishDrag = useCallback(() => {
    const session = sessionRef.current
    sessionRef.current = null
    setDraggingId(null)
    setDragDeltaY(0)
    setInsertBeforeId(null)
    setLayoutShiftById(new Map())
    if (!session || lockedIdsRef.current.has(session.id)) return
    onCommit(session.id, session.insertBeforeId)
  }, [onCommit])

  useEffect(() => {
    if (!draggingId) return

    const previousUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'

    function onPointerMove(event: PointerEvent) {
      const session = sessionRef.current
      if (!session) return
      setDragDeltaY(event.clientY - session.startY)
      const nextInsert = resolveInsertBefore(event.clientY, session.id)
      if (nextInsert === session.insertBeforeId) return
      session.insertBeforeId = nextInsert
      setInsertBeforeId(nextInsert)
      updatePreviewLayout(session)
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', finishDrag)
    window.addEventListener('pointercancel', finishDrag)
    return () => {
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', finishDrag)
      window.removeEventListener('pointercancel', finishDrag)
    }
  }, [draggingId, finishDrag, resolveInsertBefore, updatePreviewLayout])

  return {
    draggingId,
    insertBeforeId,
    dragDeltaY,
    layoutShiftById,
    setRowRef,
    startDrag,
  }
}
