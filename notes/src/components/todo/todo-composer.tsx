'use client'

import { useRef, useState, type FormEvent } from 'react'
import { CornerDownLeft, Plus } from 'lucide-react'
import { CategorySelect } from '@/components/common/category-select'
import { DateChips } from './date-chips'
import { createTodo } from '@/features/todos/api'
import { todayKey } from '@/lib/date/day-key'
import { formatClock, formatDayLabel, formatDuration } from '@/lib/date/format'
import { parseNaturalInput } from '@/lib/parse/natural-input'
import { useDismiss } from '@/hooks/use-dismiss'
import { track } from '@/lib/analytics'
import { cn } from '@/lib/utils/cn'
import type { DayKey, Id } from '@/types'

interface TodoComposerProps {
  defaultDay?: DayKey
  onCreated?: (day: DayKey) => void
}

/**
 * One field, Enter to commit. Everything else is optional and only appears once
 * the field is in use, so the resting state of the app is a single line.
 */
export function TodoComposer({ defaultDay, onCreated }: TodoComposerProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const formRef = useRef<HTMLFormElement>(null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [day, setDay] = useState<DayKey | null>(defaultDay ?? todayKey())
  const [categoryId, setCategoryId] = useState<Id | null>(null)
  const [dayTouched, setDayTouched] = useState(false)

  const parsed = parseNaturalInput(title)
  // A category is required, so the button says why it is disabled rather than
  // just being dead.
  const missingCategory = categoryId === null
  // What the user typed wins over what they clicked, unless they clicked last.
  const effectiveDay = dayTouched ? day : (parsed.dayKey ?? day)
  useDismiss(formRef, panelOpen, () => setPanelOpen(false))

  const expanded = panelOpen || title.length > 0

  async function submit(event: FormEvent) {
    event.preventDefault()
    const finalTitle = parsed.title.trim() || title.trim()
    if (!finalTitle || !categoryId) return

    await createTodo({
      title: finalTitle,
      plannedDate: effectiveDay,
      categoryId,
      plannedTime: parsed.minuteOfDay,
      estimatedDuration: parsed.durationMinutes,
    })
    track('todo_created', {
      has_category: Boolean(categoryId),
      has_time: parsed.minuteOfDay != null,
      parsed_tokens: parsed.tokens.length,
    })

    setTitle('')
    setDayTouched(false)
    setDay(defaultDay ?? todayKey())
    setCategoryId(null)
    onCreated?.(effectiveDay ?? todayKey())
    inputRef.current?.focus()
  }

  const hints = [
    effectiveDay == null
      ? 'Someday'
      : effectiveDay !== todayKey() || parsed.dayKey
        ? formatDayLabel(effectiveDay)
        : null,
    parsed.minuteOfDay != null ? formatClock(parsed.minuteOfDay) : null,
    parsed.durationMinutes != null ? formatDuration(parsed.durationMinutes) : null,
  ].filter(Boolean) as string[]

  return (
    <form
      ref={formRef}
      onSubmit={submit}
      className={cn(
        'rounded-xl border bg-surface transition-[border-color,box-shadow] duration-200',
        expanded ? 'border-line-strong' : 'border-line',
      )}
    >
      <div className="flex items-center gap-2.5 px-3.5 py-3">
        <Plus
          className={cn(
            'size-4 shrink-0 transition-colors duration-200',
            expanded ? 'text-accent' : 'text-fg-faint',
          )}
          strokeWidth={2.2}
          aria-hidden="true"
        />
        <input
          ref={inputRef}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onFocus={() => setPanelOpen(true)}
          placeholder="What do you need to do?"
          aria-label="New task"
          enterKeyHint="done"
          className="min-w-0 flex-1 text-[14px] text-fg outline-none"
        />
        {title.trim().length > 0 && (
          <button
            type="submit"
            aria-label="Add task"
            disabled={missingCategory}
            title={missingCategory ? 'Pick a category first' : undefined}
            className="inline-flex h-6 items-center gap-1 rounded-md bg-accent px-2 text-[11.5px] font-medium text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-40 animate-fade-in"
          >
            Add
            <CornerDownLeft className="size-3" strokeWidth={2.4} aria-hidden="true" />
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2.5 border-t border-line px-3.5 py-2.5 animate-fade-in">
          <div className="flex flex-wrap items-center gap-1.5">
            <DateChips
              value={effectiveDay}
              allowNone
              onChange={(next) => {
                setDay(next)
                setDayTouched(true)
              }}
            />
            {hints.length > 0 && (
              <span className="ml-auto text-[11.5px] text-fg-faint">{hints.join(' · ')}</span>
            )}
          </div>

          <div className="space-y-1.5">
            <span className="block text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              Category {missingCategory && <span className="text-accent">· required</span>}
            </span>
            <CategorySelect value={categoryId} onChange={setCategoryId} scope="todo" />
          </div>
        </div>
      )}
    </form>
  )
}
