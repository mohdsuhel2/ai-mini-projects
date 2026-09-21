'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { ClockIcon, LayoutListIcon, ListTodoIcon, PenLineIcon, SearchIcon } from '@/lib/app-icons'
import { Dialog } from '@/components/common/dialog'
import { globalSearch, type SearchResult } from '@/features/search/global-search'
import { useNotes, useOpenTodos } from '@/hooks/use-data'
import { useUi } from '@/store/ui-context'
import { db } from '@/lib/db/database'
import { liveOnly } from '@/lib/db/records'
import { useLiveQuery } from 'dexie-react-hooks'
import { cn } from '@/lib/utils/cn'

const KIND_LABEL: Record<SearchResult['kind'], string> = {
  note: 'Notes',
  todo: 'Tasks',
  activity: 'Logged',
}

function ResultIcon({ result }: { result: SearchResult }) {
  if (result.kind === 'note') {
    return result.noteKind === 'list' ? (
      <LayoutListIcon size="sm" className="text-fg-subtle" />
    ) : (
      <PenLineIcon size="sm" className="text-fg-subtle" />
    )
  }
  if (result.kind === 'todo') return <ListTodoIcon size="sm" className="text-fg-subtle" />
  return <ClockIcon size="sm" className="text-fg-subtle" />
}

export function GlobalSearchDialog() {
  const { globalSearchOpen, closeGlobalSearch, openNote, showDay } = useUi()
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const notes = useNotes()
  const todos = useOpenTodos()
  const activities = useLiveQuery(async () => liveOnly(await db().activities.toArray()), [])

  const results = useMemo(() => {
    if (!notes || !todos || !activities) return []
    return globalSearch(query, notes, todos, activities)
  }, [query, notes, todos, activities])

  useEffect(() => {
    if (!globalSearchOpen) return
    window.setTimeout(() => inputRef.current?.focus(), 0)
  }, [globalSearchOpen])

  function choose(result: SearchResult) {
    closeGlobalSearch()
    if (result.kind === 'note') {
      openNote(result.id)
      return
    }
    if (result.kind === 'todo') {
      showDay('plan')
      return
    }
    showDay('today')
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, Math.max(results.length - 1, 0)))
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    }
    if (event.key === 'Enter' && results[activeIndex]) {
      event.preventDefault()
      choose(results[activeIndex])
    }
  }

  const grouped = useMemo(() => {
    const map = new Map<SearchResult['kind'], SearchResult[]>()
    for (const result of results) {
      const list = map.get(result.kind) ?? []
      list.push(result)
      map.set(result.kind, list)
    }
    return map
  }, [results])

  let rowIndex = 0

  return (
    <Dialog
      open={globalSearchOpen}
      onClose={closeGlobalSearch}
      title="Search everything"
      description="Notes, tasks, and logged activities"
      className="max-w-lg"
    >
      <div className="border-b border-line px-4 py-3">
        <div className="flex items-center gap-2 rounded-xl border border-line bg-bg-sunk px-3 py-2">
          <SearchIcon size="sm" className="shrink-0 text-fg-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setActiveIndex(0)
            }}
            onKeyDown={onKeyDown}
            placeholder="Search notes, tasks, activities…"
            className="min-w-0 flex-1 bg-transparent text-[14px] text-fg outline-none placeholder:text-fg-faint"
          />
        </div>
      </div>

      <div className="max-h-[min(50vh,360px)] overflow-y-auto px-2 py-2">
        {query.trim() && results.length === 0 && (
          <p className="px-3 py-6 text-center text-[13px] text-fg-subtle">No matches.</p>
        )}
        {!query.trim() && (
          <p className="px-3 py-6 text-center text-[13px] text-fg-subtle">
            Type to search across your whole workspace.
          </p>
        )}
        {(['note', 'todo', 'activity'] as const).map((kind) => {
          const items = grouped.get(kind)
          if (!items?.length) return null
          return (
            <section key={kind} className="mb-2">
              <h3 className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                {KIND_LABEL[kind]}
              </h3>
              <ul>
                {items.map((result) => {
                  const index = rowIndex++
                  const active = index === activeIndex
                  return (
                    <li key={`${result.kind}-${result.id}`}>
                      <button
                        type="button"
                        onClick={() => choose(result)}
                        className={cn(
                          'flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left transition-colors',
                          active ? 'bg-accent-soft/60' : 'hover:bg-surface-hover',
                        )}
                      >
                        <span className="mt-0.5">
                          <ResultIcon result={result} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[14px] font-medium text-fg">
                            {result.title}
                          </span>
                          <span className="block truncate text-[12px] text-fg-subtle">
                            {result.subtitle}
                          </span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>
          )
        })}
      </div>
    </Dialog>
  )
}
