'use client'

import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import {
  ArchiveIcon,
  ArrowDownLeftIcon,
  ChevronLeftIcon,
  EllipsisIcon,
  GripVerticalIcon,
  ICON_STROKE_STRONG,
  PinIcon,
  StarIcon,
  Trash2Icon,
} from '@/lib/app-icons'
import { Checkbox } from '@/components/common/checkbox'
import { IconButton } from '@/components/common/icon-button'
import { Popover, PopoverItem } from '@/components/common/popover'
import {
  addListItem,
  archiveListItem,
  deleteListItem,
  reorderListItem,
  restoreListItem,
  restoreListItemSnapshot,
  toggleListItemImportant,
  toggleListItemPin,
  updateListItemText,
  updateNote,
} from '@/features/notes/api'
import {
  archivedListItems,
  isItemImportant,
  isItemPinned,
  pinnedListItems,
  unpinnedListItems,
} from '@/features/notes/list-note'
import { folderPath } from '@/features/notes/tree'
import { formatInstantStamp } from '@/lib/date/format'
import { usePointerListReorder } from '@/hooks/use-pointer-list-reorder'
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

  const pinned = pinnedListItems(note)
  const unpinned = unpinnedListItems(note)
  const hasActive = pinned.length > 0 || unpinned.length > 0

  const commitReorder = useCallback(
    async (draggedId: Id, insertBeforeId: Id | null) => {
      if (draggedId === insertBeforeId) return
      await reorderListItem(note.id, draggedId, insertBeforeId)
    },
    [note.id],
  )

  const { draggingId, dragDeltaY, layoutShiftById, setRowRef, startDrag } = usePointerListReorder(
    unpinned.map((item) => item.id),
    (draggedId, beforeId) => void commitReorder(draggedId, beforeId),
  )
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
            <ChevronLeftIcon size="md" />
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
            <ChevronLeftIcon size="md" />
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
              <ArchiveIcon size="sm" />
              Archive
              {archived.length > 0 && (
                <span className="tnum grid h-5 min-w-5 place-items-center rounded-full bg-surface px-1.5 text-[10px] font-semibold text-fg">
                  {archived.length}
                </span>
              )}
            </button>
          </div>
          <p className="mt-1 text-[12px] text-fg-subtle">
            Use ⋯ for pin, important, or delete. Drag to reorder, tap text to edit, or archive when done.
          </p>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {!hasActive && archived.length > 0 && (
          <p className="mb-3 px-1 text-center text-[13px] text-fg-subtle">
            No active items. Use Archive on the right to see or restore earlier notes.
          </p>
        )}

        {pinned.length > 0 && (
          <ListSection title="Pinned" label="Pinned items" variant="pinned">
            {pinned.map((item) => (
              <ListItemRow
                key={item.id}
                item={item}
                editing={editingId === item.id}
                showPinnedBadge={false}
                actionLabel={`Mark done: ${previewLabel(item.text)}`}
                onToggle={() => void archiveListItem(note.id, item.id)}
                onTogglePin={() => void toggleListItemPin(note.id, item.id)}
                onToggleImportant={() => void toggleListItemImportant(note.id, item.id)}
                onStartEdit={() => setEditingId(item.id)}
                onEndEdit={() => setEditingId(null)}
                onSaveText={(text) => void updateListItemText(note.id, item.id, text)}
                onDelete={() => void handleDeleteItem(item)}
              />
            ))}
          </ListSection>
        )}

        <ListSection
          title={pinned.length > 0 ? 'Items' : undefined}
          label={pinned.length > 0 ? 'List items' : 'Active items'}
        >
          {unpinned.map((item) => (
            <ListItemRow
              key={item.id}
              item={item}
              rowRef={(node) => setRowRef(item.id, node)}
              editing={editingId === item.id}
              sortable={editingId !== item.id}
              dragging={draggingId === item.id}
              dragDeltaY={draggingId === item.id ? dragDeltaY : 0}
              layoutShiftY={layoutShiftById.get(item.id) ?? 0}
              reordering={draggingId != null}
              actionLabel={`Mark done: ${previewLabel(item.text)}`}
              onToggle={() => void archiveListItem(note.id, item.id)}
              onTogglePin={() => void toggleListItemPin(note.id, item.id)}
              onToggleImportant={() => void toggleListItemImportant(note.id, item.id)}
              onStartEdit={() => setEditingId(item.id)}
              onEndEdit={() => setEditingId(null)}
              onSaveText={(text) => void updateListItemText(note.id, item.id, text)}
              onDelete={() => void handleDeleteItem(item)}
              onGripPointerDown={(event) => {
                event.preventDefault()
                event.currentTarget.setPointerCapture(event.pointerId)
                startDrag(item.id, event.clientY)
              }}
            />
          ))}
          <ListItemComposer
            noteId={note.id}
            inputRef={composerRef}
            embedded={hasActive}
          />
        </ListSection>
      </div>
    </div>
  )
}

function previewLabel(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > 48 ? `${flat.slice(0, 47)}…` : flat
}

function ListSection({
  title,
  label,
  variant = 'default',
  children,
}: {
  title?: string
  label: string
  variant?: 'default' | 'pinned'
  children: ReactNode
}) {
  return (
    <section className={cn(title ? 'mb-3' : '')} data-tone={variant === 'pinned' ? 'blue' : undefined}>
      {title && (
        <h3 className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          {variant === 'pinned' && (
            <PinIcon size="xs" className="text-[var(--tone-fg)]" strokeWidth={ICON_STROKE_STRONG} />
          )}
          {title}
        </h3>
      )}
      <ul
        aria-label={label}
        className={cn(
          'overflow-hidden rounded-2xl border border-card-line bg-surface shadow-[0_1px_0_rgba(15,23,42,0.04)]',
          variant === 'pinned' && 'border-[var(--tone-line)] bg-[var(--tone-wash)]',
        )}
      >
        {children}
      </ul>
    </section>
  )
}

function ListItemsPanel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ListSection label={label}>
      {children}
    </ListSection>
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
            <ArrowDownLeftIcon size="xs" strokeWidth={ICON_STROKE_STRONG} />
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
  rowRef,
  done = false,
  editing = false,
  showPinnedBadge = true,
  sortable = false,
  dragging = false,
  dragDeltaY = 0,
  layoutShiftY = 0,
  reordering = false,
  actionLabel,
  onToggle,
  onTogglePin,
  onToggleImportant,
  onStartEdit,
  onEndEdit,
  onSaveText,
  onDelete,
  onGripPointerDown,
}: {
  item: ListNoteItem
  rowRef?: (node: HTMLLIElement | null) => void
  done?: boolean
  editing?: boolean
  showPinnedBadge?: boolean
  sortable?: boolean
  dragging?: boolean
  dragDeltaY?: number
  layoutShiftY?: number
  reordering?: boolean
  actionLabel: string
  onToggle: () => void
  onTogglePin?: () => void
  onToggleImportant?: () => void
  onStartEdit: () => void
  onEndEdit: () => void
  onSaveText: (text: string) => void
  onDelete: () => void
  onGripPointerDown?: (event: React.PointerEvent<HTMLButtonElement>) => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const pinned = isItemPinned(item)
  const important = isItemImportant(item)
  const createdLabel = formatInstantStamp(item.createdAt)
  const label = previewLabel(item.text)

  if (editing) {
    return (
      <li className="border-b border-line last:border-b-0">
        <ListItemEditRow item={item} onSaveText={onSaveText} onEndEdit={onEndEdit} />
      </li>
    )
  }

  return (
    <li
      ref={rowRef}
      data-tone={
        important && !done
          ? 'amber'
          : showPinnedBadge && pinned && !done
            ? 'blue'
            : undefined
      }
      className={cn(
        'group/row relative flex items-start gap-2 border-b border-line py-2.5 last:border-b-0 sm:py-3',
        important && !done ? 'pl-3 pr-2.5 sm:pl-3.5 sm:pr-3.5' : 'px-2.5 sm:px-3.5',
        dragging
          ? 'z-20 touch-none rounded-xl border-transparent bg-surface shadow-pop ring-1 ring-accent/20'
          : cn(
              'transition-[transform,colors] duration-200 ease-out hover:bg-surface-hover',
              reordering && layoutShiftY !== 0 && 'relative z-10',
            ),
        showPinnedBadge && pinned && !done && !dragging && 'bg-[var(--tone-wash)]',
        important && !done && !dragging && 'bg-[var(--tone-wash)]',
      )}
      style={{
        transform: dragging
          ? `translateY(${dragDeltaY}px) scale(1.01)`
          : layoutShiftY !== 0
            ? `translateY(${layoutShiftY}px)`
            : undefined,
      }}
    >
      {important && !done && (
        <span
          data-tone="amber"
          aria-hidden="true"
          className="pointer-events-none absolute bottom-2 left-0 top-2 w-[3px] rounded-r-full bg-[var(--tone-solid)]"
        />
      )}

      <Checkbox
        checked={done}
        onChange={onToggle}
        label={actionLabel}
        className="mt-0.5"
      />

      <button
        type="button"
        onClick={onStartEdit}
        className={cn(
          'min-w-0 flex-1 rounded-lg py-0.5 text-left transition-colors hover:bg-bg-sunk/80',
          'text-fg',
        )}
      >
        <span className="block text-[14px] font-[450] leading-[1.45] whitespace-pre-wrap">
          {item.text}
        </span>
        {(createdLabel || (showPinnedBadge && pinned && !done)) && (
          <span className="mt-1 flex flex-wrap items-center gap-1.5">
            {createdLabel && (
              <time
                dateTime={new Date(item.createdAt!).toISOString()}
                className="text-[11px] leading-none text-fg-faint"
              >
                Added {createdLabel}
              </time>
            )}
            {showPinnedBadge && pinned && !done && (
              <span
                data-tone="blue"
                className="inline-flex rounded-full bg-[var(--tone-bg)] px-2 py-0.5 text-[10px] font-semibold leading-none text-[var(--tone-fg)]"
              >
                Pinned
              </span>
            )}
          </span>
        )}
      </button>

      <div className="flex shrink-0 items-center gap-0.5">
        <Popover
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          align="end"
          side="top"
          portal
          trigger={
            <IconButton
              label={`Options for “${label}”`}
              size="sm"
              onClick={() => setMenuOpen((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
            >
              <EllipsisIcon size="md" />
            </IconButton>
          }
        >
          {!done && onTogglePin && (
            <PopoverItem
              onClick={() => {
                setMenuOpen(false)
                onTogglePin()
              }}
            >
              <PinIcon
                size="sm"
                className={cn(pinned && 'text-accent')}
                strokeWidth={pinned ? ICON_STROKE_STRONG : undefined}
              />
              {pinned ? 'Unpin' : 'Pin to top'}
            </PopoverItem>
          )}
          {!done && onToggleImportant && (
            <PopoverItem
              onClick={() => {
                setMenuOpen(false)
                onToggleImportant()
              }}
            >
              <StarIcon
                size="sm"
                className={cn(important && 'text-amber-600')}
                strokeWidth={important ? ICON_STROKE_STRONG : undefined}
              />
              {important ? 'Remove important' : 'Mark important'}
            </PopoverItem>
          )}
          {(!done && (onTogglePin || onToggleImportant)) && <div className="my-1 h-px bg-line" />}
          <PopoverItem
            onClick={() => {
              setMenuOpen(false)
              onDelete()
            }}
            className="text-danger hover:bg-danger-soft"
          >
            <Trash2Icon size="sm" />
            Delete
          </PopoverItem>
        </Popover>
        {sortable && (
          <button
            type="button"
            aria-label={`Drag to reorder “${previewLabel(item.text)}”`}
            onPointerDown={onGripPointerDown}
            className={cn(
              'mt-0.5 inline-flex size-8 shrink-0 cursor-grab touch-none items-center justify-center rounded-xl text-fg-faint transition-[color,background-color,box-shadow,transform]',
              'hover:bg-bg-sunk hover:text-fg-subtle active:cursor-grabbing',
              dragging && 'cursor-grabbing bg-bg-sunk text-fg',
            )}
          >
            <GripVerticalIcon size="md" />
          </button>
        )}
      </div>
    </li>
  )
}
