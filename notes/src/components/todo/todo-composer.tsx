'use client'

import { useRef, useState, type FormEvent } from 'react'
import { CornerDownLeft, Plus } from 'lucide-react'
import { CategorySelect } from '@/components/common/category-select'
import { DateChips } from './date-chips'
import { RepeatPicker } from './repeat-picker'
import { createTodo } from '@/features/todos/api'
import { todayKey } from '@/lib/date/day-key'
import { formatClock, formatDayLabel, formatDuration } from '@/lib/date/format'
import { parseNaturalInput } from '@/lib/parse/natural-input'
import { useDismiss } from '@/hooks/use-dismiss'
import { track } from '@/lib/analytics'
import { cn } from '@/lib/utils/cn'
import type { DayKey, Id, Recurrence } from '@/types'

interface TodoComposerProps {
  defaultDay?: DayKey
  onCreated?: (day: DayKey) => void
}

/**
 * One field, Enter to commit. Everything else is optional and only appears once
 * the field is in use, so the resting state of the app is a single line.
 *
 * Clicking away commits too. A half-typed task is still a task you thought of,
 * and the only way to recover one this box threw out is to remember it again —
 * whereas an unwanted row is one tap to delete. The Add button stays because a
 * deliberate way to finish is worth having even when it is not the only one.
 */
export function TodoComposer({ defaultDay, onCreated }: TodoComposerProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const formRef = useRef<HTMLFormElement>(null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [day, setDay] = useState<DayKey | null>(defaultDay ?? todayKey())
  const [categoryId, setCategoryId] = useState<Id | null>(null)
  const [recurrence, setRecurrence] = useState<Recurrence | null>(null)
  const [dayTouched, setDayTouched] = useState(false)

  const parsed = parseNaturalInput(title)
  // What the user typed wins over what they clicked, unless they clicked last.
  const effectiveDay = dayTouched ? day : (parsed.dayKey ?? day)
  // Guards the one case where both paths can fire: clicking Add is a pointer
  // event outside nothing, but a blur racing the submit would save twice.
  const saving = useRef(false)

  const expanded = panelOpen || title.length > 0

  async function commit(): Promise<void> {
    const finalTitle = parsed.title.trim() || title.trim()
    if (!finalTitle || saving.current) return
    saving.current = true

    try {
      await createTodo({
        title: finalTitle,
        plannedDate: effectiveDay,
        categoryId,
        plannedTime: parsed.minuteOfDay,
        estimatedDuration: parsed.durationMinutes,
        recurrence,
      })
      track('todo_created', {
        has_category: Boolean(categoryId),
        repeats: recurrence?.kind ?? 'never',
        has_time: parsed.minuteOfDay != null,
        parsed_tokens: parsed.tokens.length,
      })

      setTitle('')
      setDayTouched(false)
      setDay(defaultDay ?? todayKey())
      setCategoryId(null)
      setRecurrence(null)
      onCreated?.(effectiveDay ?? todayKey())
    } finally {
      saving.current = false
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    await commit()
    inputRef.current?.focus()
  }

  // Leaving the box is a way of finishing, not of cancelling.
  useDismiss(formRef, panelOpen, () => {
    setPanelOpen(false)
    void commit()
  })

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
        'rounded-2xl border bg-surface transition-[border-color] duration-200',
        expanded ? 'border-line-strong' : 'border-card-line',
      )}
    >
      <div className="flex items-center gap-3 px-3 py-3">
        <span
          aria-hidden="true"
          className={cn(
            'grid size-9 shrink-0 place-items-center rounded-full transition-colors duration-200',
            expanded ? 'bg-accent text-accent-fg' : 'bg-accent-soft text-accent',
          )}
        >
          <Plus className="size-[18px]" strokeWidth={2.4} />
        </span>
        <input
          ref={inputRef}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onFocus={() => setPanelOpen(true)}
          placeholder="What do you need to do?"
          aria-label="New task"
          enterKeyHint="done"
          className="min-w-0 flex-1 text-[14.5px] text-fg outline-none placeholder:text-fg-faint"
        />
        {/* The palette is the faster path once you know it exists, so the row
            that would replace it is where the shortcut is advertised. */}
        {title.length === 0 && (
          <kbd className="mr-1 hidden shrink-0 rounded-md bg-bg-sunk px-2 py-1 text-[11px] font-medium text-fg-faint sm:block">
            ⌘ + K
          </kbd>
        )}
        {title.trim().length > 0 && (
          <button
            type="submit"
            aria-label="Add task"
            className="mr-1 inline-flex h-7 items-center gap-1 rounded-full bg-accent px-3 text-[12px] font-medium text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-40 animate-fade-in"
          >
            Add
            <CornerDownLeft className="size-3" strokeWidth={2.4} aria-hidden="true" />
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2.5 border-t border-card-line px-4 py-3.5 animate-fade-in">
          <div className="flex flex-wrap items-center gap-1.5">
            <DateChips
              value={effectiveDay}
              allowNone
              onChange={(next) => {
                setDay(next)
                setDayTouched(true)
              }}
            />
            <RepeatPicker value={recurrence} onChange={setRecurrence} anchor={effectiveDay} />
            {hints.length > 0 && (
              <span className="ml-auto text-[11.5px] text-fg-faint">{hints.join(' · ')}</span>
            )}
          </div>

          <div className="space-y-1.5">
            <span className="block text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
              Category
            </span>
            <CategorySelect value={categoryId} onChange={setCategoryId} scope="todo" />
          </div>
        </div>
      )}
    </form>
  )
}
