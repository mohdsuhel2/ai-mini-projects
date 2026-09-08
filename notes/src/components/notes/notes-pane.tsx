'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FolderPlus, Search, SquarePen, X } from 'lucide-react'
import { FolderTree } from './folder-tree'
import { NoteEditor } from './note-editor'
import { MoveDialog } from './move-dialog'
import { Button } from '@/components/common/button'
import { IconButton } from '@/components/common/icon-button'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { Dialog } from '@/components/common/dialog'
import { ConfirmDialog } from '@/components/common/confirm-dialog'
import {
  FolderCycleError,
  createFolder,
  createNote,
  deleteFolder,
  deleteNote,
  duplicateFolder,
  duplicateNote,
  moveFolder,
  moveNote,
  renameFolder,
  updateNote,
  restoreFolderSubtree,
  restoreNote,
  searchNotes,
} from '@/features/notes/api'
import { buildFolderTree, rootNotes } from '@/features/notes/tree'
import { useHotkeys, type Hotkey } from '@/hooks/use-hotkeys'
import { useIsWide } from '@/hooks/use-media-query'
import { useNote, useNotesTree } from '@/hooks/use-data'
import { useUi } from '@/store/ui-context'
import { cn } from '@/lib/utils/cn'
import type { DragPayload } from './drag'
import type { FolderNode, Id, Note } from '@/types'

type MoveTarget =
  | { kind: 'note'; note: Note }
  | { kind: 'folder'; node: FolderNode }
  | null

/** Whatever the keyboard shortcuts act on: the last note or folder clicked. */
type Selection = { kind: 'note'; note: Note } | { kind: 'folder'; node: FolderNode } | null

export function NotesPane() {
  const { roots, unfiled, folders, notes, loading } = useNotesTree()
  const { notify, compose, clearCompose } = useUi()
  const isWide = useIsWide()

  const [selectedId, setSelectedId] = useState<Id | null>(null)
  // Where new notes and folders land. Null is the root.
  const [activeFolderId, setActiveFolderId] = useState<Id | null>(null)
  const [collapsed, setCollapsed] = useState<Set<Id>>(new Set())
  const [query, setQuery] = useState('')
  const [moving, setMoving] = useState<MoveTarget>(null)
  const [selection, setSelection] = useState<Selection>(null)
  const [confirmingDelete, setConfirmingDelete] = useState<Selection>(null)
  const [newFolderParent, setNewFolderParent] = useState<{ parentId: Id | null } | null>(null)

  const selected = useNote(selectedId)

  // Filtering rebuilds the tree from matching notes only, and forces every
  // folder open — a match three levels down has to be visible to be useful.
  const filtering = query.trim().length > 0
  const view = useMemo(() => {
    if (!filtering) return { roots, unfiled }
    const matches = searchNotes(notes, query)
    const keep = new Set(matches.map((n) => n.folderId).filter(Boolean) as Id[])
    const relevantFolders = folders.filter((f) => keep.has(f.id) || hasDescendantIn(folders, f.id, keep))
    return { roots: buildFolderTree(relevantFolders, matches), unfiled: rootNotes(matches) }
  }, [filtering, query, roots, unfiled, notes, folders])

  const noMatches = filtering && view.roots.length === 0 && view.unfiled.length === 0

  function toggleFolder(id: Id) {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const activeFolderName = activeFolderId
    ? (folders.find((f) => f.id === activeFolderId)?.name ?? 'Unfiled')
    : 'Unfiled'

  // The global add button routes here and leaves the intent behind; this pane
  // is the only place that knows where a new note goes and how to open it.
  //
  // Latched, because clearing the intent is a state update that has not landed
  // by the time the effect can run again — under StrictMode's double invoke
  // that produced two notes from one tap.
  const handledNote = useRef(false)
  useEffect(() => {
    if (compose !== 'note') {
      handledNote.current = false
      return
    }
    if (handledNote.current) return
    handledNote.current = true
    clearCompose()
    void handleNewNote(activeFolderId)
  }, [compose, clearCompose, activeFolderId])

  async function handleNewNote(folderId: Id | null) {
    const id = await createNote({ folderId })
    setSelectedId(id)
    if (folderId) {
      setCollapsed((current) => {
        const next = new Set(current)
        next.delete(folderId)
        return next
      })
    }
  }

  async function handleDeleteNote(note: Note) {
    if (selectedId === note.id) setSelectedId(null)
    await deleteNote(note.id)
    notify(`Deleted “${note.title}”`, { label: 'Undo', onClick: () => void restoreNote(note.id) })
  }

  async function handleDeleteFolder(node: FolderNode) {
    const removed = await deleteFolder(node.folder.id)
    if (selectedId && removed.notes.includes(selectedId)) setSelectedId(null)
    const count = removed.notes.length
    notify(
      count > 0
        ? `Deleted “${node.folder.name}” and ${count} note${count === 1 ? '' : 's'}`
        : `Deleted “${node.folder.name}”`,
      { label: 'Undo', onClick: () => void restoreFolderSubtree(removed) },
    )
  }

  async function handleMove(targetParentId: Id | null) {
    if (!moving) return
    if (moving.kind === 'note') {
      await moveNote(moving.note.id, targetParentId)
      notify(`Moved “${moving.note.title}”`)
      return
    }
    try {
      await moveFolder(moving.node.folder.id, targetParentId)
      notify(`Moved “${moving.node.folder.name}”`)
    } catch (error) {
      notify(
        error instanceof FolderCycleError
          ? 'A folder cannot be moved inside itself.'
          : 'That folder could not be moved.',
      )
    }
  }

  async function handleDrop(payload: DragPayload, targetFolderId: Id | null) {
    if (payload.kind === 'note') {
      await moveNote(payload.id, targetFolderId)
      notify('Moved')
      return
    }
    // Dropping a folder onto itself is a no-op, not an error worth reporting.
    if (payload.id === targetFolderId) return
    try {
      await moveFolder(payload.id, targetFolderId)
      notify('Moved')
    } catch (error) {
      notify(
        error instanceof FolderCycleError
          ? 'A folder cannot be moved inside itself.'
          : 'That folder could not be moved.',
      )
    }
    if (targetFolderId) {
      setCollapsed((current) => {
        const next = new Set(current)
        next.delete(targetFolderId)
        return next
      })
    }
  }

  async function handleDuplicateNote(note: Note) {
    const id = await duplicateNote(note.id)
    if (id) {
      setSelectedId(id)
      notify(`Duplicated “${note.title}”`)
    }
  }

  async function handleDuplicateFolder(node: FolderNode) {
    const id = await duplicateFolder(node.folder.id)
    if (id) notify(`Duplicated “${node.folder.name}”`)
  }

  const deleteSelection = useCallback(
    (target: Selection) => {
      if (!target) return
      if (target.kind === 'note') void handleDeleteNote(target.note)
      else void handleDeleteFolder(target.node)
      setSelection(null)
    },
    // The handlers close over state that changes every render; re-creating this
    // each time is cheaper than threading refs through them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [notes, folders, selectedId],
  )

  const hotkeys = useMemo<Hotkey[]>(
    () => [
      // Shift skips the prompt. Undo still covers it, which is what makes the
      // unprompted path safe to offer at all.
      { key: 'backspace', shift: true, handler: () => deleteSelection(selection) },
      { key: 'backspace', handler: () => selection && setConfirmingDelete(selection) },
      { key: 'd', handler: () => selection && setConfirmingDelete(selection) },
    ],
    [selection, deleteSelection],
  )
  useHotkeys(hotkeys, selection !== null)

  const confirmBody = (() => {
    if (!confirmingDelete) return ''
    if (confirmingDelete.kind === 'note') {
      return `“${confirmingDelete.note.title}” will be deleted. You can undo this straight after.`
    }
    const inside = countNotesUnder(confirmingDelete.node)
    return inside > 0
      ? `“${confirmingDelete.node.folder.name}” and the ${inside} note${inside === 1 ? '' : 's'} inside it will be deleted. You can undo this straight after.`
      : `“${confirmingDelete.node.folder.name}” will be deleted. You can undo this straight after.`
  })()

  const showEditor = selected != null
  // On a narrow screen the editor is a pushed screen, not a second column.
  const showTree = isWide || !showEditor

  return (
    <div className={cn('grid gap-x-8', isWide && 'grid-cols-[minmax(0,20rem)_minmax(0,1fr)]')}>
      {showTree && (
        <section aria-label="Folders" className="min-w-0">
          <div className="mb-3 flex items-center gap-1.5">
            <div className="relative min-w-0 flex-1">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-fg-faint"
                strokeWidth={2}
                aria-hidden="true"
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Filter notes"
                aria-label="Filter notes"
                className="h-9 w-full rounded-full bg-bg-sunk pl-8 pr-7 text-[13px] text-fg outline-none transition-colors placeholder:text-fg-faint focus:bg-surface focus:ring-1 focus:ring-line-strong"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery('')}
                  aria-label="Clear filter"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-fg-faint hover:text-fg"
                >
                  <X className="size-3" strokeWidth={2.2} />
                </button>
              )}
            </div>

            <IconButton
              label={`New folder in ${activeFolderName}`}
              size="sm"
              onClick={() => setNewFolderParent({ parentId: activeFolderId })}
            >
              <FolderPlus className="size-4" strokeWidth={2} />
            </IconButton>
            <IconButton
              label={`New note in ${activeFolderName}`}
              size="sm"
              onClick={() => void handleNewNote(activeFolderId)}
            >
              <SquarePen className="size-4" strokeWidth={2} />
            </IconButton>
          </div>

          <p className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
            New items go into{' '}
            <span className="text-fg-muted">{activeFolderName}</span>
          </p>

          {loading ? (
            <PaneSkeleton rows={5} />
          ) : noMatches ? (
            <p className="px-2 py-6 text-center text-[13px] text-fg-subtle">
              Nothing matches “{query.trim()}”.
            </p>
          ) : roots.length === 0 && unfiled.length === 0 ? (
            <EmptyState
              glyph="plan"
              title="No notes yet."
              hint="Notes are the things worth keeping that have no deadline. Make a folder for each area of work."
            >
              <div className="flex gap-2">
                <Button size="sm" variant="primary" onClick={() => void handleNewNote(activeFolderId)}>
                  New note
                </Button>
                <Button size="sm" onClick={() => setNewFolderParent({ parentId: activeFolderId })}>
                  New folder
                </Button>
              </div>
            </EmptyState>
          ) : (
            <div className="rounded-2xl border border-card-line bg-surface p-1">
              <FolderTree
                  roots={view.roots}
                unfiled={view.unfiled}
                selectedNoteId={selectedId}
                activeFolderId={activeFolderId}
                onSelectFolder={(id) => {
                  setActiveFolderId(id === activeFolderId ? null : id)
                  const node = id ? findNode(view.roots, id) : null
                  setSelection(node ? { kind: 'folder', node } : null)
                }}
                collapsed={collapsed}
                forceExpanded={filtering}
                onToggleFolder={toggleFolder}
                onSelectNote={(id) => {
                  setSelectedId(id)
                  const note = notes.find((n) => n.id === id)
                  setSelection(note ? { kind: 'note', note } : null)
                }}
                onNewNote={(folderId) => void handleNewNote(folderId)}
                onNewFolder={(parentId) => setNewFolderParent({ parentId })}
                onMoveFolder={(node) => setMoving({ kind: 'folder', node })}
                onDuplicateFolder={(node) => void handleDuplicateFolder(node)}
                onDeleteFolder={(node) => void handleDeleteFolder(node)}
                onMoveNote={(note) => setMoving({ kind: 'note', note })}
                onDuplicateNote={(note) => void handleDuplicateNote(note)}
                onDeleteNote={(note) => void handleDeleteNote(note)}
                  onRename={(kind, id, name) => {
                  // Renaming in the tree is the same write the editor's title
                  // field makes, so an open note updates as you type here.
                  void (kind === 'folder' ? renameFolder(id, name) : updateNote(id, { title: name }))
                }}
                onDrop={(payload, target) => void handleDrop(payload, target)}
              />
            </div>
          )}
        </section>
      )}

      {showEditor ? (
        <section
          aria-label="Note"
          className={cn('flex min-w-0 flex-col', isWide && 'pl-1')}
        >
          <NoteEditor
            key={selected.id}
            note={selected}
            folders={folders}
            onBack={() => setSelectedId(null)}
          />
        </section>
      ) : (
        isWide && (
          <section aria-label="Note" className="min-w-0 pl-1">
            <EmptyState
              glyph="day"
              title="Nothing open."
              hint="Pick a note from the tree, or start a new one."
            />
          </section>
        )
      )}

      <MoveDialog
        open={moving !== null}
        onClose={() => setMoving(null)}
        folders={folders}
        movingFolderId={moving?.kind === 'folder' ? moving.node.folder.id : null}
        currentParentId={
          moving?.kind === 'note'
            ? moving.note.folderId
            : (moving?.node.folder.parentId ?? null)
        }
        title={
          moving?.kind === 'folder'
            ? `Move “${moving.node.folder.name}”`
            : moving
              ? `Move “${moving.note.title}”`
              : 'Move'
        }
        onMove={(target) => void handleMove(target)}
      />

      <NameDialog
        open={newFolderParent !== null}
        title="New folder"
        label="Folder name"
        initial=""
        confirmLabel="Create"
        onClose={() => setNewFolderParent(null)}
        onSubmit={async (name) => {
          const parentId = newFolderParent?.parentId ?? null
          await createFolder(name, parentId)
          if (parentId) {
            setCollapsed((current) => {
              const next = new Set(current)
              next.delete(parentId)
              return next
            })
          }
        }}
      />

      <ConfirmDialog
        open={confirmingDelete !== null}
        title={confirmingDelete?.kind === 'folder' ? 'Delete folder' : 'Delete note'}
        body={confirmBody}
        onConfirm={() => deleteSelection(confirmingDelete)}
        onClose={() => setConfirmingDelete(null)}
      />

    </div>
  )
}

/** Depth-first lookup of a node by folder id. */
function findNode(nodes: FolderNode[], id: Id): FolderNode | null {
  for (const node of nodes) {
    if (node.folder.id === id) return node
    const found = findNode(node.children, id)
    if (found) return found
  }
  return null
}

/** Notes in a folder and everything beneath it, for the confirmation copy. */
function countNotesUnder(node: FolderNode): number {
  return node.notes.length + node.children.reduce((sum, c) => sum + countNotesUnder(c), 0)
}

/** True when any folder under `id` is in `keep`, so ancestors survive a filter. */
function hasDescendantIn(
  folders: { id: Id; parentId: Id | null }[],
  id: Id,
  keep: Set<Id>,
): boolean {
  const children = folders.filter((f) => f.parentId === id)
  return children.some((child) => keep.has(child.id) || hasDescendantIn(folders, child.id, keep))
}

function NameDialog({
  open,
  title,
  label,
  initial,
  confirmLabel,
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  label: string
  initial: string
  confirmLabel: string
  onClose: () => void
  onSubmit: (name: string) => Promise<void>
}) {
  return (
    <Dialog open={open} onClose={onClose} title={title}>
      {open && (
        <NameForm
          label={label}
          initial={initial}
          confirmLabel={confirmLabel}
          onClose={onClose}
          onSubmit={onSubmit}
        />
      )}
    </Dialog>
  )
}

function NameForm({
  label,
  initial,
  confirmLabel,
  onClose,
  onSubmit,
}: {
  label: string
  initial: string
  confirmLabel: string
  onClose: () => void
  onSubmit: (name: string) => Promise<void>
}) {
  const [name, setName] = useState(initial)

  async function submit() {
    if (!name.trim()) return
    await onSubmit(name)
    onClose()
  }

  return (
    <>
      <div className="px-5 py-4">
        <label htmlFor="folder-name" className="text-[12px] font-medium text-fg-muted">
          {label}
        </label>
        <input
          id="folder-name"
          autoFocus
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              void submit()
            }
          }}
          placeholder="Service 1"
          className="mt-1.5 w-full rounded-xl bg-bg-sunk px-3 py-2.5 text-[14px] text-fg outline-none transition-colors focus:bg-surface focus:ring-1 focus:ring-line-strong"
        />
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" disabled={!name.trim()} onClick={submit}>
          {confirmLabel}
        </Button>
      </footer>
    </>
  )
}
