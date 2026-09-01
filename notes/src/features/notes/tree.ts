import type { Folder, FolderNode, Id, Note } from '@/types'

/**
 * Folder-tree maths, kept pure and separate from storage.
 *
 * Everything here defends against malformed structure rather than assuming it
 * away: a folder whose parent was deleted, or a cycle that arrived through an
 * imported backup, must still render. Losing a subtree means losing the notes
 * inside it, with no way for the user to reach them again.
 */

function byOrderThenName(a: Folder, b: Folder): number {
  return a.order - b.order || a.name.localeCompare(b.name)
}

export function buildFolderTree(folders: Folder[], notes: Note[]): FolderNode[] {
  const byId = new Map(folders.map((f) => [f.id, f]))

  const notesByFolder = new Map<string, Note[]>()
  for (const note of notes) {
    const key = note.folderId ?? ''
    const bucket = notesByFolder.get(key)
    if (bucket) bucket.push(note)
    else notesByFolder.set(key, [note])
  }
  for (const bucket of notesByFolder.values()) {
    bucket.sort((a, b) => a.title.localeCompare(b.title) || a.createdAt - b.createdAt)
  }

  const childrenOf = new Map<string, Folder[]>()
  const roots: Folder[] = []

  for (const folder of folders) {
    // A folder pointing at a parent that no longer exists is treated as a root
    // rather than dropped, so its contents stay reachable.
    const parentExists = folder.parentId != null && byId.has(folder.parentId)
    if (!parentExists) {
      roots.push(folder)
      continue
    }
    const key = folder.parentId as string
    const bucket = childrenOf.get(key)
    if (bucket) bucket.push(folder)
    else childrenOf.set(key, [folder])
  }

  const visited = new Set<string>()

  function build(folder: Folder, depth: number): FolderNode {
    visited.add(folder.id)
    const children = (childrenOf.get(folder.id) ?? [])
      .filter((child) => !visited.has(child.id))
      .sort(byOrderThenName)
      .map((child) => build(child, depth + 1))

    return {
      folder,
      depth,
      children,
      notes: notesByFolder.get(folder.id) ?? [],
    }
  }

  const tree = roots.sort(byOrderThenName).map((folder) => build(folder, 0))

  // Anything still unvisited sits in a cycle and has no reachable root. Surface
  // those at the top level rather than letting them disappear.
  for (const folder of folders) {
    if (!visited.has(folder.id)) tree.push(build(folder, 0))
  }

  return tree
}

/** Notes that belong to no folder, shown as "Unfiled". */
export function rootNotes(notes: Note[]): Note[] {
  return notes
    .filter((note) => note.folderId == null)
    .sort((a, b) => a.title.localeCompare(b.title) || a.createdAt - b.createdAt)
}

/** Every folder beneath `folderId`, at any depth. */
export function descendantIds(folders: Folder[], folderId: Id): Set<Id> {
  const childrenOf = new Map<string, Folder[]>()
  for (const folder of folders) {
    if (folder.parentId == null) continue
    const bucket = childrenOf.get(folder.parentId)
    if (bucket) bucket.push(folder)
    else childrenOf.set(folder.parentId, [folder])
  }

  const found = new Set<Id>()
  const queue = [folderId]
  while (queue.length > 0) {
    const current = queue.pop() as Id
    for (const child of childrenOf.get(current) ?? []) {
      // The guard also terminates a cycle that arrived through an import.
      if (found.has(child.id)) continue
      found.add(child.id)
      queue.push(child.id)
    }
  }
  return found
}

/**
 * A folder cannot become its own descendant. Without this check the subtree
 * detaches from every root and the notes inside it are unreachable forever.
 */
export function canMoveFolder(
  folders: Folder[],
  folderId: Id,
  targetParentId: Id | null,
): boolean {
  if (targetParentId == null) return true
  if (targetParentId === folderId) return false
  return !descendantIds(folders, folderId).has(targetParentId)
}

/** Breadcrumb from the root down to the given folder. */
export function folderPath(folders: Folder[], folderId: Id | null): Folder[] {
  if (folderId == null) return []
  const byId = new Map(folders.map((f) => [f.id, f]))

  const path: Folder[] = []
  const seen = new Set<Id>()
  let current = byId.get(folderId)
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    path.unshift(current)
    current = current.parentId ? byId.get(current.parentId) : undefined
  }
  return path
}

/** Depth-first order for rendering, skipping the contents of collapsed folders. */
export function flattenTree(nodes: FolderNode[], collapsed = new Set<Id>()): FolderNode[] {
  const out: FolderNode[] = []
  const walk = (list: FolderNode[]) => {
    for (const node of list) {
      out.push(node)
      if (!collapsed.has(node.folder.id)) walk(node.children)
    }
  }
  walk(nodes)
  return out
}

/** Total notes in a folder and everything under it, for the count badge. */
export function noteCount(node: FolderNode): number {
  return node.notes.length + node.children.reduce((sum, child) => sum + noteCount(child), 0)
}
