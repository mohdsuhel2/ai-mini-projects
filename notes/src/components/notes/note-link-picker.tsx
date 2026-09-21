'use client'

import { useMemo, useState } from 'react'
import { SearchIcon } from '@/lib/app-icons'
import { searchNotes } from '@/features/notes/api'
import { useNotes } from '@/hooks/use-data'
import type { Id } from '@/types'

interface NoteLinkPickerProps {
  value: Id | null
  onChange: (noteId: Id | null) => void
  excludeId?: Id | null
}

export function NoteLinkPicker({ value, onChange, excludeId }: NoteLinkPickerProps) {
  const notes = useNotes()
  const [query, setQuery] = useState('')

  const options = useMemo(() => {
    if (!notes) return []
    const pool = excludeId ? notes.filter((n) => n.id !== excludeId) : notes
    const matches = query.trim() ? searchNotes(pool, query) : pool
    return matches.slice(0, 8)
  }, [notes, query, excludeId])

  const selected = notes?.find((n) => n.id === value)

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 rounded-md border border-line bg-bg px-2.5 py-1.5">
        <SearchIcon size="sm" className="shrink-0 text-fg-faint" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find a note to link…"
          className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-fg-faint"
        />
      </div>
      {selected && (
        <p className="text-[12px] text-fg-subtle">
          Linked: <span className="font-medium text-fg">{selected.title}</span>
        </p>
      )}
      <ul className="max-h-36 overflow-y-auto rounded-md border border-line">
        <li>
          <button
            type="button"
            onClick={() => onChange(null)}
            className="w-full px-3 py-2 text-left text-[13px] text-fg-subtle hover:bg-surface-hover"
          >
            No linked note
          </button>
        </li>
        {options.map((note) => (
          <li key={note.id}>
            <button
              type="button"
              onClick={() => onChange(note.id)}
              className="w-full truncate px-3 py-2 text-left text-[13px] hover:bg-surface-hover"
            >
              {note.title}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
