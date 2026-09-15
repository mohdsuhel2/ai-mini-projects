import { describe, expect, it } from 'vitest'
import {
  activeListItems,
  archivedListItems,
  isItemFlagged,
  isItemPinned,
  listNotePreview,
  sortedActiveListItems,
  toggleItemTimestamp,
  withArchivedAt,
} from './list-note'
import type { ListNoteItem, Note } from '@/types'

function listNote(items: ListNoteItem[]): Note {
  return {
    id: 'n1',
    title: 'Discuss',
    kind: 'list',
    body: '',
    items,
    folderId: null,
    createdAt: 0,
    updatedAt: 0,
  }
}

describe('list-note helpers', () => {
  it('splits active and archived items', () => {
    const note = listNote([
      { id: 'a', text: 'Open', archivedAt: null },
      { id: 'b', text: 'Done', archivedAt: 100 },
    ])
    expect(activeListItems(note).map((i) => i.id)).toEqual(['a'])
    expect(archivedListItems(note).map((i) => i.id)).toEqual(['b'])
  })

  it('builds a preview from the first active item', () => {
    const note = listNote([
      { id: 'a', text: 'Ship the deploy', archivedAt: null },
      { id: 'b', text: 'Other', archivedAt: null },
    ])
    expect(listNotePreview(note)).toBe('Ship the deploy · 2 items')
  })

  it('moves archived items to the end and clears pin', () => {
    const items: ListNoteItem[] = [
      { id: 'a', text: 'one', archivedAt: null, pinnedAt: 10 },
      { id: 'b', text: 'two', archivedAt: null },
    ]
    const next = withArchivedAt(items, 'a', 99)
    expect(next.map((i) => i.id)).toEqual(['b', 'a'])
    expect(next[1].archivedAt).toBe(99)
    expect(next[1].pinnedAt).toBeNull()
  })

  it('sorts pinned items before unpinned', () => {
    const note = listNote([
      { id: 'a', text: 'later', createdAt: 3, pinnedAt: null, archivedAt: null },
      { id: 'b', text: 'pinned', createdAt: 1, pinnedAt: 50, archivedAt: null },
      { id: 'c', text: 'middle', createdAt: 2, pinnedAt: null, archivedAt: null },
    ])
    expect(sortedActiveListItems(note).map((i) => i.id)).toEqual(['b', 'c', 'a'])
  })

  it('toggles pin and flag timestamps', () => {
    const items: ListNoteItem[] = [{ id: 'a', text: 'one', archivedAt: null }]
    const pinned = toggleItemTimestamp(items, 'a', 'pinnedAt', 100)
    expect(isItemPinned(pinned[0])).toBe(true)
    const unpinned = toggleItemTimestamp(pinned, 'a', 'pinnedAt', 101)
    expect(isItemPinned(unpinned[0])).toBe(false)

    const flagged = toggleItemTimestamp(items, 'a', 'flaggedAt', 200)
    expect(isItemFlagged(flagged[0])).toBe(true)
  })
})
