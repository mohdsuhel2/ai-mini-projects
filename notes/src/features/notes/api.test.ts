import { beforeEach, describe, expect, it } from 'vitest'
import { db } from '@/lib/db/database'
import { freshDatabase } from '@/lib/db/test-utils'
import {
  FolderCycleError,
  createFolder,
  duplicateFolder,
  duplicateNote,
  createNote,
  deleteFolder,
  deleteNote,
  listFolders,
  listNotes,
  moveFolder,
  moveNote,
  restoreFolderSubtree,
  searchNotes,
  updateNote,
  UNTITLED_NOTE,
} from './api'
import { buildFolderTree } from './tree'

beforeEach(async () => {
  await freshDatabase()
})

describe('folders', () => {
  it('creates a root folder', async () => {
    const id = await createFolder('Work')
    expect(await db().folders.get(id)).toMatchObject({ name: 'Work', parentId: null })
  })

  it('nests arbitrarily deep', async () => {
    const work = await createFolder('Work')
    const service = await createFolder('Service 1', work)
    const deploy = await createFolder('Deploy', service)
    const runbooks = await createFolder('Runbooks', deploy)

    expect((await db().folders.get(runbooks))?.parentId).toBe(deploy)
    const tree = buildFolderTree(await listFolders(), [])
    expect(tree[0].children[0].children[0].children[0].folder.name).toBe('Runbooks')
  })

  it('rejects an empty name', async () => {
    await expect(createFolder('   ')).rejects.toThrow()
  })

  it('moves a folder to another parent', async () => {
    const work = await createFolder('Work')
    const personal = await createFolder('Personal')
    const service = await createFolder('Service 1', work)

    await moveFolder(service, personal)
    expect((await db().folders.get(service))?.parentId).toBe(personal)
  })

  it('refuses to move a folder into its own descendant', async () => {
    const work = await createFolder('Work')
    const service = await createFolder('Service 1', work)
    const deploy = await createFolder('Deploy', service)

    await expect(moveFolder(work, deploy)).rejects.toThrow(FolderCycleError)
    expect((await db().folders.get(work))?.parentId).toBeNull()
  })
})

describe('cascade delete', () => {
  it('removes the folder, its subtree and every note inside', async () => {
    const work = await createFolder('Work')
    const service = await createFolder('Service 1', work)
    const personal = await createFolder('Personal')
    await createNote({ title: 'Runbook', folderId: service })
    await createNote({ title: 'Budget', folderId: personal })

    await deleteFolder(work)

    expect((await listFolders()).map((f) => f.name)).toEqual(['Personal'])
    expect((await listNotes()).map((n) => n.title)).toEqual(['Budget'])
  })

  it('restores the whole subtree on undo', async () => {
    const work = await createFolder('Work')
    const service = await createFolder('Service 1', work)
    await createNote({ title: 'Runbook', folderId: service })

    const removed = await deleteFolder(work)
    expect(await listNotes()).toEqual([])

    await restoreFolderSubtree(removed)
    expect((await listFolders()).map((f) => f.name).sort()).toEqual(['Service 1', 'Work'])
    expect((await listNotes()).map((n) => n.title)).toEqual(['Runbook'])
  })

  it('leaves notes outside the subtree alone', async () => {
    const work = await createFolder('Work')
    await createNote({ title: 'Loose note' })
    await deleteFolder(work)
    expect((await listNotes()).map((n) => n.title)).toEqual(['Loose note'])
  })
})

describe('notes', () => {
  it('creates an untitled note at the root', async () => {
    const id = await createNote()
    expect(await db().notes.get(id)).toMatchObject({ title: UNTITLED_NOTE, body: '', folderId: null })
  })

  it('falls back to a title rather than storing an empty one', async () => {
    const id = await createNote({ title: 'Real' })
    await updateNote(id, { title: '   ' })
    expect((await db().notes.get(id))?.title).toBe(UNTITLED_NOTE)
  })

  it('stores the body as written', async () => {
    const id = await createNote()
    await updateNote(id, { body: '## Steps\n\n1. Do the thing' })
    expect((await db().notes.get(id))?.body).toBe('## Steps\n\n1. Do the thing')
  })

  it('moves a note between folders and back to the root', async () => {
    const work = await createFolder('Work')
    const id = await createNote({ title: 'Runbook' })

    await moveNote(id, work)
    expect((await db().notes.get(id))?.folderId).toBe(work)

    await moveNote(id, null)
    expect((await db().notes.get(id))?.folderId).toBeNull()
  })

  it('soft-deletes so undo can bring it back', async () => {
    const id = await createNote({ title: 'Gone' })
    await deleteNote(id)
    expect(await listNotes()).toEqual([])
    expect((await db().notes.get(id))?.deletedAt).toBeGreaterThan(0)
  })
})

describe('duplicate', () => {
  it('copies a note into the same folder', async () => {
    const work = await createFolder('Work')
    const id = await createNote({ title: 'Runbook', body: '## Steps', folderId: work })

    const copyId = await duplicateNote(id)
    const copy = await db().notes.get(copyId!)
    expect(copy).toMatchObject({ title: 'Runbook (copy)', body: '## Steps', folderId: work })
  })

  it('gives the copy its own identity', async () => {
    const id = await createNote({ title: 'Original', body: 'first' })
    const copyId = await duplicateNote(id)

    await updateNote(copyId!, { body: 'changed' })
    expect((await db().notes.get(id))?.body).toBe('first')
  })

  it('deep-copies a folder, its subtree and its notes', async () => {
    const work = await createFolder('Work')
    const service = await createFolder('Service 1', work)
    await createFolder('Runbooks', service)
    await createNote({ title: 'Handover', folderId: service })

    const copyId = await duplicateFolder(work)
    const folders = await listFolders()

    expect(folders).toHaveLength(6)
    expect(folders.find((f) => f.id === copyId)?.name).toBe('Work (copy)')

    const copiedService = folders.find((f) => f.parentId === copyId)
    expect(copiedService?.name).toBe('Service 1')
    expect(folders.some((f) => f.parentId === copiedService?.id && f.name === 'Runbooks')).toBe(true)

    const notes = await listNotes()
    expect(notes).toHaveLength(2)
    expect(notes.filter((n) => n.folderId === copiedService?.id)).toHaveLength(1)
  })

  it('places the copy beside the original, not inside it', async () => {
    const work = await createFolder('Work')
    const copyId = await duplicateFolder(work)
    expect((await db().folders.get(copyId!))?.parentId).toBeNull()
  })

  it('leaves the original untouched when the copy is edited', async () => {
    const work = await createFolder('Work')
    await createNote({ title: 'Shared', body: 'original', folderId: work })

    const copyId = await duplicateFolder(work)
    const copiedNote = (await listNotes()).find((n) => n.folderId === copyId)
    await updateNote(copiedNote!.id, { body: 'changed' })

    const originalNote = (await listNotes()).find((n) => n.folderId === work)
    expect(originalNote?.body).toBe('original')
  })
})

describe('searchNotes', () => {
  const notes = [
    { title: 'Deploy runbook', body: 'kubectl rollout' },
    { title: 'Budget', body: 'Rent and groceries' },
  ] as Parameters<typeof searchNotes>[0]

  it('matches on title, case-insensitively', () => {
    expect(searchNotes(notes, 'DEPLOY').map((n) => n.title)).toEqual(['Deploy runbook'])
  })

  it('matches on body', () => {
    expect(searchNotes(notes, 'groceries').map((n) => n.title)).toEqual(['Budget'])
  })

  it('returns everything for an empty query', () => {
    expect(searchNotes(notes, '  ')).toHaveLength(2)
  })
})
