import { db } from '@/lib/db/database'
import { liveOnly, newId, now } from '@/lib/db/records'
import {
  insertListItem,
  listItemsForSearch,
  patchListItem,
  removeListItem,
  toggleItemTimestamp,
  UNTITLED_LIST,
  withArchivedAt,
} from './list-note'
import { canMoveFolder, descendantIds } from './tree'
import type { Folder, Id, ListNoteItem, Note } from '@/types'

export async function listFolders(): Promise<Folder[]> {
  return liveOnly(await db().folders.toArray())
}

export async function listNotes(): Promise<Note[]> {
  return liveOnly(await db().notes.toArray())
}

export async function getNote(id: Id): Promise<Note | undefined> {
  const note = await db().notes.get(id)
  return note && note.deletedAt == null ? note : undefined
}

export async function createFolder(name: string, parentId: Id | null = null): Promise<Id> {
  const trimmed = name.trim()
  if (!trimmed) throw new Error('A folder needs a name')

  const stamp = now()
  const siblings = (await listFolders()).filter((f) => f.parentId === (parentId ?? null))
  const maxOrder = siblings.reduce((max, f) => Math.max(max, f.order), 0)

  const id = newId()
  await db().folders.add({
    id,
    name: trimmed,
    parentId: parentId ?? null,
    order: maxOrder + 10,
    createdAt: stamp,
    updatedAt: stamp,
    deletedAt: null,
  })
  return id
}

export async function renameFolder(id: Id, name: string): Promise<void> {
  const trimmed = name.trim()
  if (!trimmed) return
  await db().folders.update(id, { name: trimmed, updatedAt: now() })
}

export class FolderCycleError extends Error {}

/**
 * Refuses a move that would put a folder inside its own subtree. That move
 * detaches the subtree from every root, which means the notes inside it can
 * never be displayed again — a silent, unrecoverable data loss.
 */
export async function moveFolder(id: Id, targetParentId: Id | null): Promise<void> {
  const folders = await listFolders()
  if (!canMoveFolder(folders, id, targetParentId)) {
    throw new FolderCycleError('A folder cannot be moved inside itself.')
  }
  const siblings = folders.filter((f) => f.parentId === (targetParentId ?? null))
  const maxOrder = siblings.reduce((max, f) => Math.max(max, f.order), 0)
  await db().folders.update(id, {
    parentId: targetParentId ?? null,
    order: maxOrder + 10,
    updatedAt: now(),
  })
}

/**
 * Soft-deletes the folder together with everything beneath it, in one
 * transaction, so a single Undo restores the whole subtree intact.
 */
export async function deleteFolder(id: Id): Promise<{ folders: Id[]; notes: Id[] }> {
  const stamp = now()
  const folders = await listFolders()
  const doomed = [id, ...descendantIds(folders, id)]
  const doomedSet = new Set(doomed)

  const notes = (await listNotes()).filter((n) => n.folderId != null && doomedSet.has(n.folderId))

  await db().transaction('rw', [db().folders, db().notes], async () => {
    await Promise.all(doomed.map((f) => db().folders.update(f, { deletedAt: stamp, updatedAt: stamp })))
    await Promise.all(notes.map((n) => db().notes.update(n.id, { deletedAt: stamp, updatedAt: stamp })))
  })

  return { folders: doomed, notes: notes.map((n) => n.id) }
}

/** Restores a subtree that a delete removed, for Undo. */
export async function restoreFolderSubtree(ids: { folders: Id[]; notes: Id[] }): Promise<void> {
  const stamp = now()
  await db().transaction('rw', [db().folders, db().notes], async () => {
    await Promise.all(ids.folders.map((f) => db().folders.update(f, { deletedAt: null, updatedAt: stamp })))
    await Promise.all(ids.notes.map((n) => db().notes.update(n, { deletedAt: null, updatedAt: stamp })))
  })
}

export const UNTITLED_NOTE = 'Untitled note'

export async function createNote(input: { title?: string; body?: string; folderId?: Id | null } = {}): Promise<Id> {
  const stamp = now()
  const id = newId()
  await db().notes.add({
    id,
    kind: 'document',
    title: input.title?.trim() || UNTITLED_NOTE,
    body: input.body ?? '',
    folderId: input.folderId ?? null,
    createdAt: stamp,
    updatedAt: stamp,
    deletedAt: null,
  })
  return id
}

export async function createListNote(input: { title?: string; folderId?: Id | null } = {}): Promise<Id> {
  const stamp = now()
  const id = newId()
  await db().notes.add({
    id,
    kind: 'list',
    title: input.title?.trim() || UNTITLED_LIST,
    body: '',
    items: [],
    folderId: input.folderId ?? null,
    createdAt: stamp,
    updatedAt: stamp,
    deletedAt: null,
  })
  return id
}

export async function updateNote(
  id: Id,
  patch: Partial<Pick<Note, 'title' | 'body' | 'folderId' | 'items'>>,
): Promise<void> {
  const note = await getNote(id)
  const next: Partial<Note> = { ...patch, updatedAt: now() }
  if (patch.title !== undefined) {
    next.title = patch.title.trim() || (note?.kind === 'list' ? UNTITLED_LIST : UNTITLED_NOTE)
  }
  await db().notes.update(id, next)
}

async function mutateListItems(noteId: Id, mutate: (items: ListNoteItem[]) => ListNoteItem[]): Promise<void> {
  const note = await getNote(noteId)
  if (!note || note.kind !== 'list') throw new Error('Not a list note')
  await updateNote(noteId, { items: mutate(note.items ?? []) })
}

export async function addListItem(noteId: Id, text: string): Promise<Id> {
  const trimmed = text.trim()
  if (!trimmed) throw new Error('An item needs some text')
  const itemId = newId()
  const stamp = now()
  await mutateListItems(noteId, (items) => [
    ...items,
    { id: itemId, text: trimmed, createdAt: stamp, archivedAt: null, pinnedAt: null, flaggedAt: null },
  ])
  return itemId
}

export async function archiveListItem(noteId: Id, itemId: Id): Promise<void> {
  await mutateListItems(noteId, (items) => withArchivedAt(items, itemId, now()))
}

export async function restoreListItem(noteId: Id, itemId: Id): Promise<void> {
  await mutateListItems(noteId, (items) => withArchivedAt(items, itemId, null))
}

export async function toggleListItemPin(noteId: Id, itemId: Id): Promise<void> {
  const stamp = now()
  await mutateListItems(noteId, (items) => toggleItemTimestamp(items, itemId, 'pinnedAt', stamp))
}

export async function toggleListItemFlag(noteId: Id, itemId: Id): Promise<void> {
  const stamp = now()
  await mutateListItems(noteId, (items) => toggleItemTimestamp(items, itemId, 'flaggedAt', stamp))
}

export async function updateListItemText(noteId: Id, itemId: Id, text: string): Promise<void> {
  const trimmed = text.trim()
  if (!trimmed) throw new Error('An item needs some text')
  await mutateListItems(noteId, (items) => patchListItem(items, itemId, { text: trimmed }))
}

export async function deleteListItem(noteId: Id, itemId: Id): Promise<ListNoteItem | null> {
  const note = await getNote(noteId)
  const removed = note?.items?.find((item) => item.id === itemId) ?? null
  if (!removed) return null
  await mutateListItems(noteId, (items) => removeListItem(items, itemId))
  return removed
}

export async function restoreListItemSnapshot(noteId: Id, item: ListNoteItem): Promise<void> {
  await mutateListItems(noteId, (items) => insertListItem(items, item))
}

export async function moveNote(id: Id, folderId: Id | null): Promise<void> {
  await db().notes.update(id, { folderId: folderId ?? null, updatedAt: now() })
}

export async function deleteNote(id: Id): Promise<void> {
  const stamp = now()
  await db().notes.update(id, { deletedAt: stamp, updatedAt: stamp })
}

export async function restoreNote(id: Id): Promise<void> {
  await db().notes.update(id, { deletedAt: null, updatedAt: now() })
}

function copyName(name: string): string {
  return `${name} (copy)`
}

export async function duplicateNote(id: Id): Promise<Id | null> {
  const source = await getNote(id)
  if (!source) return null
  if (source.kind === 'list') {
    const stamp = now()
    const id = newId()
    await db().notes.add({
      ...source,
      id,
      title: copyName(source.title),
      items: (source.items ?? []).map((item) => ({ ...item, id: newId() })),
      createdAt: stamp,
      updatedAt: stamp,
      deletedAt: null,
    })
    return id
  }
  return createNote({
    title: copyName(source.title),
    body: source.body,
    folderId: source.folderId,
  })
}

/**
 * Deep-copies a folder, its whole subtree and every note inside, in one
 * transaction. Ids are freshly minted so the copy shares nothing with the
 * original — editing one must never change the other.
 */
export async function duplicateFolder(id: Id): Promise<Id | null> {
  const folders = await listFolders()
  const source = folders.find((f) => f.id === id)
  if (!source) return null

  const notes = await listNotes()
  const subtree = [id, ...descendantIds(folders, id)]
  const subtreeSet = new Set(subtree)

  const stamp = now()
  // Old id to new id, so children can be re-parented onto the copies.
  const remapped = new Map<Id, Id>(subtree.map((oldId) => [oldId, newId()]))

  const newFolders: Folder[] = folders
    .filter((f) => subtreeSet.has(f.id))
    .map((f) => ({
      ...f,
      id: remapped.get(f.id) as Id,
      name: f.id === id ? copyName(f.name) : f.name,
      parentId:
        f.id === id
          ? source.parentId
          : ((f.parentId && remapped.get(f.parentId)) ?? null),
      order: f.id === id ? f.order + 1 : f.order,
      createdAt: stamp,
      updatedAt: stamp,
      deletedAt: null,
    }))

  const newNotes: Note[] = notes
    .filter((n) => n.folderId != null && subtreeSet.has(n.folderId))
    .map((n) => ({
      ...n,
      id: newId(),
      folderId: remapped.get(n.folderId as Id) as Id,
      createdAt: stamp,
      updatedAt: stamp,
      deletedAt: null,
    }))

  await db().transaction('rw', [db().folders, db().notes], async () => {
    await db().folders.bulkAdd(newFolders)
    if (newNotes.length > 0) await db().notes.bulkAdd(newNotes)
  })

  return remapped.get(id) as Id
}

/** Case-insensitive substring match over titles and bodies. */
export function searchNotes(notes: Note[], query: string): Note[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return notes
  return notes.filter((note) => {
    const haystack = [
      note.title,
      note.kind === 'list' ? listItemsForSearch(note) : note.body,
    ]
      .join('\n')
      .toLowerCase()
    return haystack.includes(needle)
  })
}
