'use client'

import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Archive, ArrowLeft, CornerDownLeft, Flag, Pin, Trash2 } from 'lucide-react'
import { Checkbox } from '@/components/common/checkbox'
import { IconButton } from '@/components/common/icon-button'
import {
  addListItem,
  archiveListItem,
  deleteListItem,
  restoreListItem,
  restoreListItemSnapshot,
  toggleListItemFlag,
  toggleListItemPin,
  updateListItemText,
  updateNote,
} from '@/features/notes/api'
import {
  activeListItems,
  archivedListItems,
  isItemFlagged,
  isItemPinned,
} from '@/features/notes/list-note'
import { folderPath } from '@/features/notes/tree'
import { useHasHover } from '@/hooks/use-media-query'
import { useUi } from '@/store/ui-context'
import { cn } from '@/lib/utils/cn'
import type { Folder, Id, ListNoteItem, Note } from '@/types'

interface ListNoteEditorProps {
  note: Note
  folders: Folder[]
  onBack?: () => void
}

type ListScreen = 'active' | 'archive'

export function ListNoteEditor({ note, folders, onBack }: ListNoteEditorProps) {
  const { notify } = useUi()
  const [title, setTitle] = useState(note.title)
  const [screen, setScreen] = useState<ListScreen>('active')
  const [editingId, setEditingId] = useState<Id | null>(null)
  const lastWrittenTitle = useRef(note.title)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const path = folderPath(folders, note.folderId)

  async function handleDeleteItem(item: ListNoteItem) {
    if (editingId === item.id) setEditingId(null)
    const removed = await deleteListItem(note.id, item.id)
    if (!removed) return
    notify('Item deleted', {
      label: 'Undo',
      onClick: () => void restoreListItemSnapshot(note.id, removed),
    })
  }

  const active = activeListItems(note)
  const archived = archivedListItems(note)
  const visibleScreen: ListScreen =
    screen === 'archive' && archived.length === 0 ? 'active' : screen

  useEffect(() => {
    if (title === lastWrittenTitle.current) return
    const id = window.setTimeout(() => {
      lastWrittenTitle.current = title
      void updateNote(note.id, { title })
    }, 400)
    return () => window.clearTimeout(id)
  }, [title, note.id])

  // Adopt title renames from elsewhere (folder tree) without writing stale text back.
  useEffect(() => {
    if (note.title === lastWrittenTitle.current) return
    lastWrittenTitle.current = note.title
    setTitle(note.title)
  }, [note.title])

  useEffect(() => {
    if (visibleScreen !== 'active') return
    composerRef.current?.focus()
  }, [note.id, visibleScreen])

  if (visibleScreen === 'archive') {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <header className="mb-4 flex items-center gap-2">
          <IconButton label="Back to list" size="sm" onClick={() => setScreen('active')}>
            <ArrowLeft className="size-4" strokeWidth={2} />
          </IconButton>
          <div className="min-w-0 flex-1">
            <h2 className="text-[18px] font-semibold tracking-tight text-fg">Archived</h2>
            <p className="text-[12px] text-fg-subtle">
              {archived.length} item{archived.length === 1 ? '' : 's'} in {title || 'this list'} — tap to restore.
            </p>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto pb-4">
          <ListItemsPanel label="Archived items">
            {archived.map((item) => (
              <ListItemRow
                key={item.id}
                item={item}
                done
                editing={editingId === item.id}
                actionLabel={`Restore “${previewLabel(item.text)}”`}
                onToggle={() => void restoreListItem(note.id, item.id)}
                onToggleFlag={() => void toggleListItemFlag(note.id, item.id)}
                onStartEdit={() => setEditingId(item.id)}
                onEndEdit={() => setEditingId(null)}
                onSaveText={(text) => void updateListItemText(note.id, item.id, text)}
                onDelete={() => void handleDeleteItem(item)}
              />
            ))}
          </ListItemsPanel>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="mb-4 flex items-start gap-2">
        {onBack && (
          <IconButton label="Back to folders" size="sm" className="mt-0.5 shrink-0" onClick={onBack}>
            <ArrowLeft className="size-4" strokeWidth={2} />
          </IconButton>
        )}
        <div className="min-w-0 flex-1">
          {path.length > 0 && (
            <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.08em] text-fg-faint">
              {path.map((folder, index) => (
                <span key={folder.id}>
                  {index > 0 && ' / '}
                  {folder.name}
                </span>
              ))}
            </p>
          )}
          <div className="flex items-center gap-2">
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              aria-label="List title"
              placeholder="Discuss"
              className="min-w-0 flex-1 bg-transparent text-[22px] font-semibold tracking-tight text-fg outline-none placeholder:text-fg-faint"
            />
            <button
              type="button"
              onClick={() => setScreen('archive')}
              className={cn(
                'inline-flex shrink-0 items-center gap-1.5 rounded-full border border-card-line bg-bg-sunk px-3 py-1.5',
                'text-[12px] font-medium text-fg-subtle transition-colors hover:border-line-strong hover:bg-surface-hover hover:text-fg',
              )}
            >
              <Archive className="size-3.5" strokeWidth={2} aria-hidden="true" />
              Archive
              {archived.length > 0 && (
                <span className="tnum grid h-5 min-w-5 place-items-center rounded-full bg-surface px-1.5 text-[10px] font-semibold text-fg">
                  {archived.length}
                </span>
              )}
            </button>
          </div>
          <p className="mt-1 text-[12px] text-fg-subtle">
            Tick to complete, tap text to edit, flag or pin items, or delete permanently.
          </p>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {active.length === 0 && archived.length > 0 && (
          <p className="mb-3 px-1 text-center text-[13px] text-fg-subtle">
            No active items. Use Archive on the right to see or restore earlier notes.
          </p>
        )}

        <ListItemsPanel label="Active items">
          {active.map((item) => (
            <ListItemRow
              key={item.id}
              item={item}
              editing={editingId === item.id}
              actionLabel={`Mark done: ${previewLabel(item.text)}`}
              onToggle={() => void archiveListItem(note.id, item.id)}
              onToggleFlag={() => void toggleListItemFlag(note.id, item.id)}
              onTogglePin={() => void toggleListItemPin(note.id, item.id)}
              onStartEdit={() => setEditingId(item.id)}
              onEndEdit={() => setEditingId(null)}
              onSaveText={(text) => void updateListItemText(note.id, item.id, text)}
              onDelete={() => void handleDeleteItem(item)}
            />
          ))}
          <ListItemComposer
            noteId={note.id}
            inputRef={composerRef}
            embedded={active.length > 0}
          />
        </ListItemsPanel>
      </div>
    </div>
  )
}

function previewLabel(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > 48 ? `${flat.slice(0, 47)}…` : flat
}

function ListItemsPanel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ul
      aria-label={label}
      className="overflow-hidden rounded-2xl border border-card-line bg-surface shadow-[0_1px_0_rgba(15,23,42,0.04)]"
    >
      {children}
    </ul>
  )
}

function ListItemDraftForm({
  fieldId,
  draft,
  onDraftChange,
  inputRef,
  placeholder,
  submitLabel,
  submitAriaLabel,
  hint,
  active,
  autoFocus,
  onSubmit,
  onFocus,
  onBlur,
  onKeyDown,
}: {
  fieldId: string
  draft: string
  onDraftChange: (value: string) => void
  inputRef: RefObject<HTMLTextAreaElement | null>
  placeholder: string
  submitLabel: string
  submitAriaLabel: string
  hint: string
  active: boolean
  autoFocus?: boolean
  onSubmit: () => void
  onFocus?: () => void
  onBlur?: () => void
  onKeyDown?: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void
}) {
  const resize = useCallback(() => {
    const node = inputRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, 132)}px`
  }, [inputRef])

  useEffect(() => {
    resize()
  }, [draft, resize])

  useEffect(() => {
    if (!autoFocus) return
    const node = inputRef.current
    if (!node) return
    node.focus()
    node.setSelectionRange(node.value.length, node.value.length)
    resize()
  }, [autoFocus, inputRef, resize])

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
      className={cn(
        'transition-[background-color] duration-200',
        active && 'bg-surface-hover/60',
      )}
    >
      <div className="flex items-start gap-3 px-3.5 py-3">
        <span
          aria-hidden="true"
          className={cn(
            'mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-full border border-dashed border-control-line',
            active ? 'border-accent/40 bg-accent-soft/50' : 'bg-transparent',
          )}
        />
        <div className="min-w-0 flex-1">
          <label htmlFor={fieldId} className="sr-only">{submitAriaLabel}</label>
          <textarea
            ref={inputRef}
            id={fieldId}
            value={draft}
            rows={1}
            enterKeyHint="done"
            placeholder={placeholder}
            onFocus={onFocus}
            onBlur={onBlur}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={onKeyDown}
            className="block w-full resize-none overflow-y-auto bg-transparent text-[14px] font-[450] leading-[1.45] text-fg outline-none placeholder:text-fg-faint"
          />
          {active && (
            <p className="mt-1.5 text-[11px] text-fg-faint">{hint}</p>
          )}
        </div>
        {draft.trim().length > 0 && (
          <button
            type="submit"
            aria-label={submitAriaLabel}
            className="mt-0.5 inline-flex h-8 shrink-0 items-center gap-1 rounded-full bg-accent px-3 text-[12px] font-medium text-accent-fg transition-colors hover:bg-accent-hover animate-fade-in"
          >
            {submitLabel}
            <CornerDownLeft className="size-3" strokeWidth={2.4} aria-hidden="true" />
          </button>
        )}
      </div>
    </form>
  )
}

function ListItemComposer({
  noteId,
  inputRef,
  embedded,
}: {
  noteId: string
  inputRef: RefObject<HTMLTextAreaElement | null>
  embedded: boolean
}) {
  const [draft, setDraft] = useState('')
  const [focused, setFocused] = useState(false)
  const saving = useRef(false)
  const expanded = focused || draft.length > 0

  async function commit() {
    const text = draft.trim()
    if (!text || saving.current) return
    saving.current = true
    try {
      await addListItem(noteId, text)
      setDraft('')
      inputRef.current?.focus()
    } finally {
      saving.current = false
    }
  }

  return (
    <li className={cn(embedded && 'border-t border-line')}>
      <ListItemDraftForm
        fieldId={`list-add-${noteId}`}
        draft={draft}
        onDraftChange={setDraft}
        inputRef={inputRef}
        placeholder={embedded ? 'Add another item…' : 'Type a note, idea, or paragraph…'}
        submitLabel="Add"
        submitAriaLabel="Add list item"
        hint="Enter to add · Shift+Enter for a new line"
        active={expanded}
        onSubmit={() => void commit()}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            void commit()
          }
        }}
      />
    </li>
  )
}

function ListItemEditRow({
  item,
  onSaveText,
  onEndEdit,
}: {
  item: ListNoteItem
  onSaveText: (text: string) => void
  onEndEdit: () => void
}) {
  const [draft, setDraft] = useState(item.text)
  const editRef = useRef<HTMLTextAreaElement>(null)
  const savingEdit = useRef(false)
  const dismissEdit = useRef(false)

  function commitEdit() {
    if (savingEdit.current || dismissEdit.current) {
      dismissEdit.current = false
      return
    }
    const next = draft.trim()
    if (!next) {
      onEndEdit()
      return
    }
    savingEdit.current = true
    if (next !== item.text) onSaveText(next)
    onEndEdit()
    savingEdit.current = false
  }

  function cancelEdit() {
    dismissEdit.current = true
    onEndEdit()
  }

  return (
    <ListItemDraftForm
      fieldId={`edit-${item.id}`}
      draft={draft}
      onDraftChange={setDraft}
      inputRef={editRef}
      placeholder="Type a note, idea, or paragraph…"
      submitLabel="Save"
      submitAriaLabel="Edit list item"
      hint="Enter to save · Shift+Enter for a new line · Esc to cancel"
      active
      autoFocus
      onSubmit={commitEdit}
      onBlur={commitEdit}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault()
          cancelEdit()
        }
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault()
          commitEdit()
        }
      }}
    />
  )
}

function ListItemRow({
  item,
  done = false,
  editing = false,
  actionLabel,
  onToggle,
  onToggleFlag,
  onTogglePin,
  onStartEdit,
  onEndEdit,
  onSaveText,
  onDelete,
}: {
  item: ListNoteItem
  done?: boolean
  editing?: boolean
  actionLabel: string
  onToggle: () => void
  onToggleFlag?: () => void
  onTogglePin?: () => void
  onStartEdit: () => void
  onEndEdit: () => void
  onSaveText: (text: string) => void
  onDelete: () => void
}) {
  const hasHover = useHasHover()
  const flagged = isItemFlagged(item)
  const pinned = isItemPinned(item)

  if (editing) {
    return (
      <li className="border-b border-line last:border-b-0">
        <ListItemEditRow item={item} onSaveText={onSaveText} onEndEdit={onEndEdit} />
      </li>
    )
  }

  return (
    <li
      className={cn(
        'group/row flex items-start gap-2 border-b border-line px-2.5 py-2.5 last:border-b-0 sm:px-3.5 sm:py-3',
        'transition-colors hover:bg-surface-hover',
        pinned && !done && 'bg-accent-soft/35',
        flagged && !done && !pinned && 'bg-amber-500/[0.06]',
      )}
    >
      <Checkbox
        checked={done}
        onChange={onToggle}
        label={actionLabel}
        className="mt-0.5"
      />

      {(pinned || flagged) && (
        <div className="mt-1 flex shrink-0 flex-col gap-0.5 sm:hidden">
          {pinned && <Pin className="size-3 text-accent" strokeWidth={2.4} aria-hidden="true" />}
          {flagged && <Flag className="size-3 text-amber-600" strokeWidth={2.4} aria-hidden="true" />}
        </div>
      )}

      <button
        type="button"
        onClick={onStartEdit}
        className={cn(
          'min-w-0 flex-1 rounded-lg py-0.5 text-left transition-colors hover:bg-bg-sunk/80',
          'text-fg',
          flagged && !done && 'font-medium',
        )}
      >
        <span className="block text-[14px] font-[450] leading-[1.45] whitespace-pre-wrap">
          {item.text}
        </span>
      </button>

      <div
        className={cn(
          'flex shrink-0 items-center gap-0.5 transition-opacity',
          hasHover
            ? cn(
                'opacity-0 group-hover/row:opacity-100 focus-within:opacity-100',
                (flagged || pinned) && 'opacity-100',
              )
            : 'opacity-100',
        )}
      >
        {onToggleFlag && (
          <IconButton
            label={flagged ? `Unflag “${previewLabel(item.text)}”` : `Flag “${previewLabel(item.text)}”`}
            size="sm"
            onClick={onToggleFlag}
            className={cn(
              flagged && 'text-amber-600 hover:bg-amber-500/10 hover:text-amber-700',
            )}
          >
            <Flag
              className={cn('size-3.5', flagged && 'fill-amber-500/25')}
              strokeWidth={flagged ? 2.4 : 2}
            />
          </IconButton>
        )}
        {onTogglePin && !done && (
          <IconButton
            label={pinned ? `Unpin “${previewLabel(item.text)}”` : `Pin “${previewLabel(item.text)}”`}
            size="sm"
            onClick={onTogglePin}
            className={cn(pinned && 'text-accent hover:bg-accent-soft hover:text-accent')}
          >
            <Pin
              className={cn('size-3.5', pinned && 'fill-accent/20')}
              strokeWidth={pinned ? 2.4 : 2}
            />
          </IconButton>
        )}
        <IconButton
          label={`Delete “${previewLabel(item.text)}”`}
          size="sm"
          onClick={onDelete}
          className="text-danger hover:bg-danger-soft hover:text-danger"
        >
          <Trash2 className="size-3.5" strokeWidth={2} />
        </IconButton>
      </div>
    </li>
  )
}
