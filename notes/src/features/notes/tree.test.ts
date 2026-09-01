import { describe, expect, it } from 'vitest'
import {
  buildFolderTree,
  canMoveFolder,
  descendantIds,
  flattenTree,
  folderPath,
} from './tree'
import type { Folder, Note } from '@/types'

const folder = (id: string, name: string, parentId: string | null = null, order = 10): Folder => ({
  id,
  name,
  parentId,
  order,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

const note = (id: string, title: string, folderId: string | null = null): Note => ({
  id,
  title,
  body: '',
  folderId,
  createdAt: 0,
  updatedAt: 0,
  deletedAt: null,
})

// work
//   service-1
//     deploy
//   service-2
// personal
const FOLDERS = [
  folder('work', 'Work'),
  folder('service-1', 'Service 1', 'work', 10),
  folder('service-2', 'Service 2', 'work', 20),
  folder('deploy', 'Deploy', 'service-1'),
  folder('personal', 'Personal', null, 20),
]

describe('buildFolderTree', () => {
  it('nests folders under their parent', () => {
    const roots = buildFolderTree(FOLDERS, [])
    expect(roots.map((r) => r.folder.name)).toEqual(['Work', 'Personal'])
    expect(roots[0].children.map((c) => c.folder.name)).toEqual(['Service 1', 'Service 2'])
    expect(roots[0].children[0].children.map((c) => c.folder.name)).toEqual(['Deploy'])
  })

  it('records depth from the root', () => {
    const roots = buildFolderTree(FOLDERS, [])
    expect(roots[0].depth).toBe(0)
    expect(roots[0].children[0].depth).toBe(1)
    expect(roots[0].children[0].children[0].depth).toBe(2)
  })

  it('places notes in their folder', () => {
    const roots = buildFolderTree(FOLDERS, [note('n1', 'Runbook', 'service-1')])
    expect(roots[0].children[0].notes.map((n) => n.title)).toEqual(['Runbook'])
  })

  it('sorts siblings by order then name', () => {
    const roots = buildFolderTree(
      [folder('b', 'Beta', null, 10), folder('a', 'Alpha', null, 10), folder('c', 'Gamma', null, 5)],
      [],
    )
    expect(roots.map((r) => r.folder.name)).toEqual(['Gamma', 'Alpha', 'Beta'])
  })

  it('sorts notes alphabetically within a folder', () => {
    const roots = buildFolderTree(
      [folder('work', 'Work')],
      [note('b', 'Zebra', 'work'), note('a', 'Apple', 'work')],
    )
    expect(roots[0].notes.map((n) => n.title)).toEqual(['Apple', 'Zebra'])
  })

  it('rescues a folder whose parent no longer exists', () => {
    // An orphan must still be reachable, or its notes are lost forever.
    const roots = buildFolderTree([folder('lost', 'Lost', 'deleted-parent')], [])
    expect(roots.map((r) => r.folder.name)).toEqual(['Lost'])
  })

  it('does not hang on a cycle in stored data', () => {
    const cyclic = [folder('a', 'A', 'b'), folder('b', 'B', 'a')]
    const roots = buildFolderTree(cyclic, [])
    expect(roots.length).toBeGreaterThan(0)
  })

  it('handles an empty tree', () => {
    expect(buildFolderTree([], [])).toEqual([])
  })
})

describe('descendantIds', () => {
  it('collects every folder beneath one, at any depth', () => {
    expect([...descendantIds(FOLDERS, 'work')].sort()).toEqual(['deploy', 'service-1', 'service-2'])
  })

  it('returns nothing for a leaf', () => {
    expect([...descendantIds(FOLDERS, 'deploy')]).toEqual([])
  })
})

describe('canMoveFolder', () => {
  it('allows a move to the root', () => {
    expect(canMoveFolder(FOLDERS, 'service-1', null)).toBe(true)
  })

  it('allows a move to an unrelated folder', () => {
    expect(canMoveFolder(FOLDERS, 'service-1', 'personal')).toBe(true)
  })

  it('refuses moving a folder into itself', () => {
    expect(canMoveFolder(FOLDERS, 'work', 'work')).toBe(false)
  })

  it('refuses moving a folder into its own child', () => {
    expect(canMoveFolder(FOLDERS, 'work', 'service-1')).toBe(false)
  })

  it('refuses moving a folder into a deeper descendant', () => {
    // The case that silently detaches a subtree if it is not caught.
    expect(canMoveFolder(FOLDERS, 'work', 'deploy')).toBe(false)
  })
})

describe('folderPath', () => {
  it('reads from the root down to the folder', () => {
    expect(folderPath(FOLDERS, 'deploy').map((f) => f.name)).toEqual(['Work', 'Service 1', 'Deploy'])
  })

  it('is empty at the root', () => {
    expect(folderPath(FOLDERS, null)).toEqual([])
  })

  it('is empty for an unknown folder', () => {
    expect(folderPath(FOLDERS, 'nope')).toEqual([])
  })
})

describe('flattenTree', () => {
  it('flattens depth-first for rendering', () => {
    const flat = flattenTree(buildFolderTree(FOLDERS, []))
    expect(flat.map((n) => n.folder.name)).toEqual([
      'Work',
      'Service 1',
      'Deploy',
      'Service 2',
      'Personal',
    ])
  })

  it('stops descending into a collapsed folder', () => {
    const flat = flattenTree(buildFolderTree(FOLDERS, []), new Set(['work']))
    expect(flat.map((n) => n.folder.name)).toEqual(['Work', 'Personal'])
  })
})
