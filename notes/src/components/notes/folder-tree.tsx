'use client'

import { useEffect, useRef, useState } from 'react'
import {
  ChevronRight,
  Copy,
  FilePlus2,
  FileText,
  Folder as FolderIcon,
  FolderOpen,
  FolderPlus,
  Home,
  MoreHorizontal,
  Pencil,
  Trash2,
} from 'lucide-react'
import { IconButton } from '@/components/common/icon-button'
import {
  ContextMenu,
  ContextMenuItem,
  ContextMenuSeparator,
  type ContextMenuState,
} from '@/components/common/context-menu'
import { noteCount } from '@/features/notes/tree'
import { plainTextPreview } from '@/lib/markdown/render'
import { cn } from '@/lib/utils/cn'
import { isTreeDrag, readDragPayload, setDragPayload, type DragPayload } from './drag'
import type { FolderNode, Id, Note } from '@/types'

/**
 * Folders and notes share one outline rather than sitting in two columns.
 * "Work → Service 1 → Deploy runbook" is how the user described the problem, so
 * it is how the navigation reads — and it leaves the note itself room to breathe.
 *
 * Rows drag onto folders to move them. Drag-and-drop is pointer-only, so every
 * action it offers also lives on the menu, which works from a keyboard and on a
 * phone.
 */

export interface TreeActions {
  onSelectNote: (id: Id) => void
  onSelectFolder: (id: Id | null) => void
  onToggleFolder: (id: Id) => void
  onNewNote: (folderId: Id | null) => void
  onNewFolder: (parentId: Id | null) => void
  onMoveFolder: (node: FolderNode) => void
  onDuplicateFolder: (node: FolderNode) => void
  onDeleteFolder: (node: FolderNode) => void
  onMoveNote: (note: Note) => void
  onDuplicateNote: (note: Note) => void
  onDeleteNote: (note: Note) => void
  onDrop: (payload: DragPayload, targetFolderId: Id | null) => void
  /** Committed from an inline rename — double-clicking a row's name. */
  onRename: (kind: 'note' | 'folder', id: Id, name: string) => void
}

/** The row currently being renamed in place. One at a time, tree-wide. */
type RenameTarget = { kind: 'note' | 'folder'; id: Id } | null

interface FolderTreeProps extends TreeActions {
  roots: FolderNode[]
  unfiled: Note[]
  selectedNoteId: Id | null
  /** Where a new note or folder will be created. Null is the root. */
  activeFolderId: Id | null
  collapsed: Set<Id>
  /** Set while a filter is active: every folder opens so matches are visible. */
  forceExpanded?: boolean
}

const INDENT_STEP = 14
/** Indentation stops growing past this depth so deep trees stay readable. */
const MAX_INDENT_DEPTH = 6
/** Roughly the menu's width, so a menu opened from the ⋯ button stays on screen. */
const MENU_WIDTH = 176

function InlineName({
  value,
  onCommit,
  onCancel,
}: {
  value: string
  onCommit: (name: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(value)
  const ref = useRef<HTMLInputElement>(null)

  // Selected, not just focused: renaming usually means replacing the name, and
  // "Untitled note" is the case this exists for.
  useEffect(() => ref.current?.select(), [])

  function commit() {
    const next = draft.trim()
    if (next && next !== value) onCommit(next)
    else onCancel()
  }

  return (
    <input
      ref={ref}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        // Kept off the document: Escape would otherwise reach whatever else is
        // listening for it, and a typed letter would fire a single-key hotkey.
        event.stopPropagation()
        if (event.key === 'Enter') {
          event.preventDefault()
          commit()
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          onCancel()
        }
      }}
      aria-label={`Rename ${value}`}
      className="min-w-0 flex-1 rounded-md bg-surface px-1.5 py-0.5 text-[13.5px] font-medium text-fg outline-none ring-1 ring-line-strong"
    />
  )
}

function indentFor(depth: number): number {
  return Math.min(depth, MAX_INDENT_DEPTH) * INDENT_STEP
}

function menuAnchorFor(element: HTMLElement): ContextMenuState {
  const box = element.getBoundingClientRect()
  return { x: box.right - MENU_WIDTH, y: box.bottom + 4 }
}

export function FolderTree(props: FolderTreeProps) {
  const { roots, unfiled, selectedNoteId, activeFolderId } = props
  const [rootOver, setRootOver] = useState(false)
  const [renaming, setRenaming] = useState<RenameTarget>(null)

  return (
    <ul className="space-y-0.5" role="tree" aria-label="Folders and notes">
      {/* The root is a real row: it can be selected as a destination and it
          accepts a drop, so moving something back out needs no dialog. */}
      <li role="none">
        <div
          onDragOver={(event) => {
            if (!isTreeDrag(event)) return
            event.preventDefault()
            setRootOver(true)
          }}
          onDragLeave={() => setRootOver(false)}
          onDrop={(event) => {
            setRootOver(false)
            const payload = readDragPayload(event)
            if (!payload) return
            event.preventDefault()
            props.onDrop(payload, null)
          }}
          className={cn(
            'flex items-center rounded-xl transition-colors',
            rootOver && 'ring-1 ring-accent',
            activeFolderId === null ? 'bg-bg-sunk' : 'hover:bg-surface-hover',
          )}
        >
          <button
            type="button"
            role="treeitem"
            aria-selected={activeFolderId === null}
            onClick={() => props.onSelectFolder(null)}
            className="flex min-w-0 flex-1 items-center gap-2 py-2 pl-[19px] text-left"
          >
            <Home
              className={cn(
                'size-4 shrink-0',
                activeFolderId === null ? 'text-fg' : 'text-fg-subtle',
              )}
              strokeWidth={2}
              aria-hidden="true"
            />
            <span
              className={cn(
                'text-[13.5px] font-medium',
                activeFolderId === null ? 'text-fg' : 'text-fg',
              )}
            >
              All notes
            </span>
          </button>
        </div>
      </li>

      {roots.map((node) => (
        <FolderRow
          key={node.folder.id}
          node={node}
          renaming={renaming}
          setRenaming={setRenaming}
          {...props}
        />
      ))}

      {unfiled.map((note) => (
        <NoteRow
          key={note.id}
          note={note}
          depth={0}
          selected={note.id === selectedNoteId}
          renaming={renaming}
          setRenaming={setRenaming}
          {...props}
        />
      ))}
    </ul>
  )
}

interface RowRenameProps {
  renaming: RenameTarget
  setRenaming: (target: RenameTarget) => void
}

function FolderRow({
  node,
  renaming,
  setRenaming,
  ...props
}: { node: FolderNode } & RowRenameProps & FolderTreeProps) {
  const [menu, setMenu] = useState<ContextMenuState | null>(null)
  const [dragOver, setDragOver] = useState(false)

  const open = props.forceExpanded || !props.collapsed.has(node.folder.id)
  const active = props.activeFolderId === node.folder.id
  const editing = renaming?.kind === 'folder' && renaming.id === node.folder.id
  // Lifted out so the icon is identical whether the name is a button or a field.
  const folderIcon =
    open && node.children.length + node.notes.length > 0 ? (
      <FolderOpen
        className={cn('size-4 shrink-0', active ? 'text-fg' : 'text-fg-subtle')}
        strokeWidth={2}
        aria-hidden="true"
      />
    ) : (
      <FolderIcon
        className={cn('size-4 shrink-0', active ? 'text-fg' : 'text-fg-subtle')}
        strokeWidth={2}
        aria-hidden="true"
      />
    )
  const total = noteCount(node)
  const hasChildren = node.children.length > 0 || node.notes.length > 0
  const close = () => setMenu(null)

  return (
    <li role="none">
      <div
        draggable
        onDragStart={(event) => {
          event.stopPropagation()
          setDragPayload(event, { kind: 'folder', id: node.folder.id })
        }}
        onDragOver={(event) => {
          if (!isTreeDrag(event)) return
          event.preventDefault()
          event.stopPropagation()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          setDragOver(false)
          const payload = readDragPayload(event)
          if (!payload) return
          event.preventDefault()
          event.stopPropagation()
          props.onDrop(payload, node.folder.id)
        }}
        onContextMenu={(event) => {
          event.preventDefault()
          setMenu({ x: event.clientX, y: event.clientY })
        }}
        className={cn(
          'group/row flex items-center gap-1 rounded-xl pr-1 transition-colors',
          dragOver && 'ring-1 ring-accent',
          active ? 'bg-bg-sunk' : 'hover:bg-surface-hover',
        )}
        style={{ paddingLeft: indentFor(node.depth) }}
      >
        {/* The chevron only expands. Clicking the name selects the folder as
            the destination new notes and folders go into. */}
        <button
          type="button"
          aria-label={`${open ? 'Collapse' : 'Expand'} ${node.folder.name}`}
          onClick={() => props.onToggleFolder(node.folder.id)}
          disabled={!hasChildren}
          className="ml-1 grid size-4 shrink-0 place-items-center rounded"
        >
          <ChevronRight
            className={cn(
              'size-3 text-fg-faint transition-transform duration-200',
              open && 'rotate-90',
              !hasChildren && 'opacity-0',
            )}
            strokeWidth={2.5}
            aria-hidden="true"
          />
        </button>

        {editing ? (
          <div className="flex min-w-0 flex-1 items-center gap-2 py-1.5">
            {folderIcon}
            <InlineName
              value={node.folder.name}
              onCommit={(name) => {
                props.onRename('folder', node.folder.id, name)
                setRenaming(null)
              }}
              onCancel={() => setRenaming(null)}
            />
          </div>
        ) : (
        <button
          type="button"
          role="treeitem"
          aria-expanded={open}
          aria-selected={active}
          onClick={() => props.onSelectFolder(node.folder.id)}
          onDoubleClick={() => setRenaming({ kind: 'folder', id: node.folder.id })}
          title="Double-click to rename"
          className="flex min-w-0 flex-1 items-center gap-2 py-2 text-left"
        >
          {folderIcon}
          <span
            className={cn('truncate text-[13.5px] font-medium text-fg')}
          >
            {node.folder.name}
          </span>
          {total > 0 && (
            <span className="tnum grid h-5 min-w-5 shrink-0 place-items-center rounded-full bg-bg-sunk px-1.5 text-[11px] font-semibold text-fg-muted">
              {total}
            </span>
          )}
        </button>
        )}

        <div className="flex shrink-0 items-center opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
          <IconButton
            label={`New note in ${node.folder.name}`}
            size="sm"
            onClick={() => props.onNewNote(node.folder.id)}
          >
            <FilePlus2 className="size-3.5" strokeWidth={2.2} />
          </IconButton>

          <IconButton
            label={`Options for ${node.folder.name}`}
            size="sm"
            onClick={(event) => setMenu(menuAnchorFor(event.currentTarget))}
          >
            <MoreHorizontal className="size-3.5" strokeWidth={2} />
          </IconButton>
        </div>
      </div>

      <ContextMenu state={menu} onClose={close} label={`Actions for ${node.folder.name}`}>
        <ContextMenuItem onClick={() => { close(); props.onNewNote(node.folder.id) }}>
          <FilePlus2 className="size-3.5 text-fg-subtle" strokeWidth={2} />
          New note inside
        </ContextMenuItem>
        <ContextMenuItem onClick={() => { close(); props.onNewFolder(node.folder.id) }}>
          <FolderPlus className="size-3.5 text-fg-subtle" strokeWidth={2} />
          New folder inside
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem onClick={() => { close(); setRenaming({ kind: 'folder', id: node.folder.id }) }}>
          <Pencil className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Rename
        </ContextMenuItem>
        <ContextMenuItem onClick={() => { close(); props.onDuplicateFolder(node) }}>
          <Copy className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Duplicate
        </ContextMenuItem>
        <ContextMenuItem onClick={() => { close(); props.onMoveFolder(node) }}>
          <FolderOpen className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Move to…
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem danger onClick={() => { close(); props.onDeleteFolder(node) }}>
          <Trash2 className="size-3.5" strokeWidth={2} />
          Delete folder
        </ContextMenuItem>
      </ContextMenu>

      {open && (
        <ul className="space-y-0.5" role="group">
          {node.children.map((child) => (
            <FolderRow
              key={child.folder.id}
              node={child}
              renaming={renaming}
              setRenaming={setRenaming}
              {...props}
            />
          ))}
          {node.notes.map((note) => (
            <NoteRow
              key={note.id}
              note={note}
              depth={node.depth + 1}
              selected={note.id === props.selectedNoteId}
              renaming={renaming}
              setRenaming={setRenaming}
              {...props}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function NoteRow({
  note,
  depth,
  selected,
  renaming,
  setRenaming,
  onSelectNote,
  onMoveNote,
  onDuplicateNote,
  onDeleteNote,
  onRename,
}: { note: Note; depth: number; selected: boolean } & RowRenameProps & TreeActions) {
  const [menu, setMenu] = useState<ContextMenuState | null>(null)
  const preview = plainTextPreview(note.body, 60)
  const close = () => setMenu(null)
  const editing = renaming?.kind === 'note' && renaming.id === note.id
  const noteIcon = (
    <FileText
      className={cn('size-4 shrink-0', selected ? 'text-fg' : 'text-fg-faint')}
      strokeWidth={2}
      aria-hidden="true"
    />
  )

  return (
    <li role="none">
      <div
        draggable
        onDragStart={(event) => {
          event.stopPropagation()
          setDragPayload(event, { kind: 'note', id: note.id })
        }}
        onContextMenu={(event) => {
          event.preventDefault()
          setMenu({ x: event.clientX, y: event.clientY })
        }}
        className={cn(
          'group/row flex items-center gap-1 rounded-xl pr-1 transition-colors',
          selected ? 'bg-bg-sunk' : 'hover:bg-surface-hover',
        )}
        style={{ paddingLeft: indentFor(depth) }}
      >
        {editing ? (
          <div className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pl-[17px]">
            {noteIcon}
            <InlineName
              value={note.title}
              onCommit={(title) => {
                onRename('note', note.id, title)
                setRenaming(null)
              }}
              onCancel={() => setRenaming(null)}
            />
          </div>
        ) : (
          <button
            type="button"
            role="treeitem"
            aria-selected={selected}
            onClick={() => onSelectNote(note.id)}
            onDoubleClick={() => setRenaming({ kind: 'note', id: note.id })}
            title="Double-click to rename"
            className="flex min-w-0 flex-1 items-center gap-2 py-2 pl-[17px] text-left"
          >
            {noteIcon}
            <span className="min-w-0 flex-1 truncate">
              <span className={cn('text-[13.5px] text-fg', selected && 'font-medium')}>
                {note.title}
              </span>
              {preview && <span className="ml-2 text-[11.5px] text-fg-faint">{preview}</span>}
            </span>
          </button>
        )}

        <div className="shrink-0 opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
          <IconButton
            label={`Options for ${note.title}`}
            size="sm"
            onClick={(event) => setMenu(menuAnchorFor(event.currentTarget))}
          >
            <MoreHorizontal className="size-3.5" strokeWidth={2} />
          </IconButton>
        </div>
      </div>

      <ContextMenu state={menu} onClose={close} label={`Actions for ${note.title}`}>
        <ContextMenuItem onClick={() => { close(); onSelectNote(note.id) }}>
          <FileText className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Open
        </ContextMenuItem>
        <ContextMenuItem onClick={() => { close(); setRenaming({ kind: 'note', id: note.id }) }}>
          <Pencil className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Rename
        </ContextMenuItem>
        <ContextMenuItem onClick={() => { close(); onDuplicateNote(note) }}>
          <Copy className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Duplicate
        </ContextMenuItem>
        <ContextMenuItem onClick={() => { close(); onMoveNote(note) }}>
          <FolderOpen className="size-3.5 text-fg-subtle" strokeWidth={2} />
          Move to…
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem danger onClick={() => { close(); onDeleteNote(note) }}>
          <Trash2 className="size-3.5" strokeWidth={2} />
          Delete note
        </ContextMenuItem>
      </ContextMenu>
    </li>
  )
}
