'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowLeft,
  Bold,
  Heading1,
  Heading2,
  Italic,
  List,
  ListOrdered,
  Quote,
  Strikethrough,
} from 'lucide-react'
import { IconButton } from '@/components/common/icon-button'
import { updateNote } from '@/features/notes/api'
import { folderPath } from '@/features/notes/tree'
import { renderMarkdown } from '@/lib/markdown/render'
import { htmlToMarkdown } from '@/lib/markdown/serialize'
import { cn } from '@/lib/utils/cn'
import type { Folder, Note } from '@/types'

const AUTOSAVE_DELAY_MS = 600

interface NoteEditorProps {
  note: Note
  folders: Folder[]
  /** Rendered on mobile, where the editor is a pushed screen. */
  onBack?: () => void
}

/**
 * One surface: what you type is already formatted, with no read/edit toggle to
 * flip. What gets stored is still markdown — the DOM is serialised back on every
 * change — so notes stay portable, exportable and readable outside this app.
 *
 * Formatting is applied through `document.execCommand`. It is deprecated and
 * every browser still implements it; the alternative is a selection-and-range
 * engine of our own, which is a great deal of code to reproduce behaviour that
 * already works everywhere.
 */
export function NoteEditor({ note, folders, onBack }: NoteEditorProps) {
  const [title, setTitle] = useState(note.title)
  const [draft, setDraft] = useState(note.body)
  const bodyRef = useRef<HTMLDivElement | null>(null)

  const path = folderPath(folders, note.folderId)
  const dirty = title !== note.title || draft !== note.body

  // Captured at mount, so writing the initial HTML never reruns against a
  // later save and clobbers what the user is typing.
  const initialBody = useRef(note.body)

  /**
   * The initial HTML is written once, through a callback ref. Setting it on
   * every render would destroy the caret on each keystroke; the parent keys
   * this component by note id, so a different note remounts it.
   */
  const attach = useCallback((node: HTMLDivElement | null) => {
    bodyRef.current = node
    if (node) node.innerHTML = renderMarkdown(initialBody.current)
  }, [])

  const readBack = useCallback(() => {
    if (bodyRef.current) setDraft(htmlToMarkdown(bodyRef.current))
  }, [])

  useEffect(() => {
    if (!dirty) return
    const id = window.setTimeout(() => {
      void updateNote(note.id, { title, body: draft })
    }, AUTOSAVE_DELAY_MS)
    return () => window.clearTimeout(id)
  }, [dirty, title, draft, note.id])

  /** Applies a command to the current selection without losing it. */
  function run(command: string, value?: string) {
    bodyRef.current?.focus()
    document.execCommand(command, false, value)
    readBack()
  }

  return (
    <article className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-line pb-3">
        <div className="flex items-start gap-3">
          {onBack && (
            <IconButton label="Back to notes" size="sm" onClick={onBack} className="mt-1 lg:hidden">
              <ArrowLeft className="size-4" strokeWidth={2} />
            </IconButton>
          )}

          <div className="min-w-0 flex-1">
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              aria-label="Note title"
              placeholder="Untitled note"
              className="w-full bg-transparent text-[19px] font-semibold tracking-[-0.02em] text-fg outline-none placeholder:text-fg-faint"
            />
            <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11.5px] text-fg-faint">
              {path.length > 0 ? (
                path.map((folder, index) => (
                  <span key={folder.id}>
                    {index > 0 && <span className="mx-1">/</span>}
                    {folder.name}
                  </span>
                ))
              ) : (
                <span>Unfiled</span>
              )}
              <span className="mx-1">·</span>
              <span className={cn('transition-colors', dirty ? 'text-accent' : 'text-fg-faint')}>
                {dirty ? 'Saving…' : 'Saved'}
              </span>
            </p>
          </div>
        </div>

        {/* Acts on whatever is selected. The mousedown default is prevented so
            the selection survives the click that formats it. */}
        <div
          role="toolbar"
          aria-label="Formatting"
          onMouseDown={(event) => event.preventDefault()}
          className="no-scrollbar edge-fade-x mt-2.5 flex items-center gap-0.5 overflow-x-auto"
        >
          <ToolbarButton label="Heading" onClick={() => run('formatBlock', 'h2')}>
            <Heading1 className="size-4" strokeWidth={2} />
          </ToolbarButton>
          <ToolbarButton label="Subheading" onClick={() => run('formatBlock', 'h3')}>
            <Heading2 className="size-4" strokeWidth={2} />
          </ToolbarButton>
          <ToolbarButton label="Body text" onClick={() => run('formatBlock', 'p')}>
            <span className="text-[13px] font-semibold leading-none">¶</span>
          </ToolbarButton>

          <span className="mx-1 h-4 w-px shrink-0 bg-line" aria-hidden="true" />

          <ToolbarButton label="Bold" shortcut="⌘B" onClick={() => run('bold')}>
            <Bold className="size-4" strokeWidth={2.6} />
          </ToolbarButton>
          <ToolbarButton label="Italic" shortcut="⌘I" onClick={() => run('italic')}>
            <Italic className="size-4" strokeWidth={2.2} />
          </ToolbarButton>
          <ToolbarButton label="Strikethrough" onClick={() => run('strikeThrough')}>
            <Strikethrough className="size-4" strokeWidth={2.2} />
          </ToolbarButton>

          <span className="mx-1 h-4 w-px shrink-0 bg-line" aria-hidden="true" />

          <ToolbarButton label="Bulleted list" onClick={() => run('insertUnorderedList')}>
            <List className="size-4" strokeWidth={2} />
          </ToolbarButton>
          <ToolbarButton label="Numbered list" onClick={() => run('insertOrderedList')}>
            <ListOrdered className="size-4" strokeWidth={2} />
          </ToolbarButton>
          <ToolbarButton label="Quote" onClick={() => run('formatBlock', 'blockquote')}>
            <Quote className="size-4" strokeWidth={2} />
          </ToolbarButton>
        </div>
      </header>

      <div
        ref={attach}
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        aria-label="Note body"
        spellCheck
        data-placeholder="Start writing. Use the bar above, or ⌘B for bold."
        onInput={readBack}
        onBlur={readBack}
        onPaste={(event) => {
          // Plain text only: pasted markup would put tags in the note that the
          // serialiser cannot represent, and that the renderer would escape on
          // the next load anyway.
          event.preventDefault()
          document.execCommand('insertText', false, event.clipboardData.getData('text/plain'))
          readBack()
        }}
        onKeyDown={(event) => {
          // Escape belongs to the note, not to the dialog stack above it.
          if (event.key === 'Escape') event.stopPropagation()
        }}
        onClick={(event) => {
          // Task boxes are the one interactive thing inside a note.
          const target = event.target as HTMLElement
          if (target instanceof HTMLInputElement && target.type === 'checkbox') {
            target.toggleAttribute('checked', target.checked)
            readBack()
          }
        }}
        className="note-prose note-editable mt-4 min-h-[20rem] flex-1 outline-none"
      />
    </article>
  )
}

function ToolbarButton({
  label,
  shortcut,
  onClick,
  children,
}: {
  label: string
  shortcut?: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <IconButton
      label={shortcut ? `${label} (${shortcut})` : label}
      size="sm"
      onClick={onClick}
      className="shrink-0"
    >
      {children}
    </IconButton>
  )
}
