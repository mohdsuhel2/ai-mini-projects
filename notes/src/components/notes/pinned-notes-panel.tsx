'use client'

import { LayoutListIcon, PenLineIcon, XIcon } from '@/lib/app-icons'
import { unpinNoteFromDay } from '@/features/notes/api'
import { listNotePreview } from '@/features/notes/list-note'
import { useUi } from '@/store/ui-context'
import { IconButton } from '@/components/common/icon-button'
import type { Note } from '@/types'

export function PinnedNotesPanel({ day, notes }: { day: string; notes: Note[] }) {
  const { openNote } = useUi()
  if (notes.length === 0) return null

  return (
    <section className="rounded-2xl border border-card-line bg-surface p-3.5 sm:p-4">
      <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
        Pinned for this day
      </h3>
      <ul className="space-y-1.5">
        {notes.map((note) => (
          <li key={note.id} className="group flex items-start gap-2 rounded-xl px-1 py-0.5">
            <button
              type="button"
              onClick={() => openNote(note.id)}
              className="flex min-w-0 flex-1 cursor-pointer items-start gap-2 rounded-lg py-1.5 pl-1 text-left transition-colors hover:bg-surface-hover hover:text-accent"
            >
              {note.kind === 'list' ? (
                <LayoutListIcon size="sm" className="mt-0.5 shrink-0 text-fg-subtle" />
              ) : (
                <PenLineIcon size="sm" className="mt-0.5 shrink-0 text-fg-subtle" />
              )}
              <span className="min-w-0">
                <span className="block truncate text-[14px] font-medium text-fg">{note.title}</span>
                <span className="block truncate text-[12px] text-fg-subtle">
                  {note.kind === 'list' ? listNotePreview(note, 64) || 'List note' : 'Document'}
                </span>
              </span>
            </button>
            <IconButton
              label={`Unpin “${note.title}” from ${day}`}
              size="sm"
              onClick={() => void unpinNoteFromDay(note.id)}
              className="shrink-0 opacity-70 transition-opacity group-hover:opacity-100"
            >
              <XIcon size="sm" />
            </IconButton>
          </li>
        ))}
      </ul>
    </section>
  )
}
