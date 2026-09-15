import { describe, expect, it } from 'vitest'
import {
  activeListItems,
  archivedListItems,
  isItemPinned,
  listNotePreview,
  buildPreviewIds,
  layoutShifts,
  nextListItemOrder,
  pinnedListItems,
  reorderActiveListItems,
  sortedActiveListItems,
  toggleItemPin,
  toggleItemTimestamp,
  unpinnedListItems,
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

  it('sorts active items by manual order within each section', () => {
    const note = listNote([
      { id: 'a', text: 'later', order: 30, archivedAt: null },
      { id: 'b', text: 'first', order: 10, archivedAt: null },
      { id: 'c', text: 'middle', order: 20, archivedAt: null },
    ])
    expect(sortedActiveListItems(note).map((i) => i.id)).toEqual(['b', 'c', 'a'])
  })

  it('keeps pinned items above unpinned items', () => {
    const note = listNote([
      { id: 'a', text: 'open', order: 10, archivedAt: null },
      { id: 'b', text: 'pinned', order: 20, pinnedAt: 5, archivedAt: null },
    ])
    expect(pinnedListItems(note).map((item) => item.id)).toEqual(['b'])
    expect(unpinnedListItems(note).map((item) => item.id)).toEqual(['a'])
    expect(sortedActiveListItems(note).map((item) => item.id)).toEqual(['b', 'a'])
  })

  it('builds preview order and layout shifts while dragging', () => {
    const ids = ['a', 'b', 'c']
    const heights = new Map([
      ['a', 40],
      ['b', 50],
      ['c', 30],
    ])
    expect(buildPreviewIds(ids, 'a', 'c')).toEqual(['b', 'a', 'c'])
    const shifts = layoutShifts(ids, buildPreviewIds(ids, 'a', 'c'), heights, 'a')
    expect(shifts.get('b')).toBe(-40)
    expect(shifts.get('c')).toBe(0)
  })

  it('reorders unpinned items without moving pinned ones', () => {
    const items: ListNoteItem[] = [
      { id: 'p', text: 'pinned', order: 10, pinnedAt: 1, archivedAt: null },
      { id: 'a', text: 'one', order: 20, archivedAt: null },
      { id: 'b', text: 'two', order: 30, archivedAt: null },
    ]
    const next = reorderActiveListItems(items, 'b', 'a')
    expect(sortedActiveListItems(listNote(next)).map((item) => item.id)).toEqual(['p', 'b', 'a'])
  })

  it('reorders active items and writes new order values', () => {
    const items: ListNoteItem[] = [
      { id: 'a', text: 'one', order: 10, archivedAt: null },
      { id: 'b', text: 'two', order: 20, archivedAt: null },
      { id: 'c', text: 'done', order: 30, archivedAt: 99 },
    ]
    const next = reorderActiveListItems(items, 'b', 'a')
    expect(sortedActiveListItems(listNote(next)).map((i) => i.id)).toEqual(['b', 'a'])
    expect(next.find((i) => i.id === 'b')?.order).toBe(10)
    expect(next.find((i) => i.id === 'a')?.order).toBe(20)
    expect(next.find((i) => i.id === 'c')?.order).toBe(30)
  })

  it('assigns the next order slot for new items', () => {
    const items: ListNoteItem[] = [
      { id: 'a', text: 'one', order: 10, archivedAt: null },
      { id: 'b', text: 'two', order: 40, archivedAt: null },
    ]
    expect(nextListItemOrder(items)).toBe(50)
  })

  it('toggles pin timestamps', () => {
    const items: ListNoteItem[] = [{ id: 'a', text: 'one', archivedAt: null }]
    const pinned = toggleItemTimestamp(items, 'a', 'pinnedAt', 100)
    expect(isItemPinned(pinned[0])).toBe(true)
    const unpinned = toggleItemTimestamp(pinned, 'a', 'pinnedAt', 101)
    expect(isItemPinned(unpinned[0])).toBe(false)
  })

  it('moves order when pinning and unpinning', () => {
    const items: ListNoteItem[] = [
      { id: 'a', text: 'one', order: 10, archivedAt: null },
      { id: 'b', text: 'two', order: 20, archivedAt: null },
    ]
    const pinned = toggleItemPin(items, 'b', 100)
    expect(pinned.find((item) => item.id === 'b')?.pinnedAt).toBe(100)
    expect(pinned.find((item) => item.id === 'b')?.order).toBe(10)
    const unpinned = toggleItemPin(pinned, 'b', 101)
    expect(unpinned.find((item) => item.id === 'b')?.pinnedAt).toBeNull()
    expect(unpinned.find((item) => item.id === 'b')?.order).toBe(20)
  })
})
