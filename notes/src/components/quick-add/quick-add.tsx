'use client'

import { useMemo, useState } from 'react'
import { CalendarDays, Check, Clock, Hourglass, ListTodo, Sparkles } from 'lucide-react'
import { Dialog } from '@/components/common/dialog'
import { CategoryPicker } from '@/components/todo/category-picker'
import { createTodo } from '@/features/todos/api'
import { createActivity } from '@/features/activities/api'
import { parseNaturalInput } from '@/lib/parse/natural-input'
import { todayKey } from '@/lib/date/day-key'
import { track } from '@/lib/analytics'
import { useUi } from '@/store/ui-context'
import { cn } from '@/lib/utils/cn'
import type { Id } from '@/types'

type Mode = 'todo' | 'activity'

const EXAMPLES = ['Buy groceries tomorrow', 'Gym at 6 pm', 'Watched YouTube for 1 hour']

const TOKEN_ICONS = {
  date: CalendarDays,
  time: Clock,
  duration: Hourglass,
} as const

/**
 * One field that decides for itself whether you are planning something or
 * recording it — and shows you which, before you commit. The preview is the
 * point: a parser that guesses silently is a parser you cannot trust.
 */
export function QuickAdd() {
  const { quickAddOpen, closeQuickAdd } = useUi()

  return (
    <Dialog
      open={quickAddOpen}
      onClose={closeQuickAdd}
      title="Quick add"
      hideTitle
      variant="command"
      className="sm:w-[min(100vw-1.5rem,32rem)]"
    >
      {/* Mounted only while open: closing unmounts the form, which is what
          clears it. No effect needed to reset state. */}
      {quickAddOpen && <QuickAddForm />}
    </Dialog>
  )
}

function QuickAddForm() {
  const { closeQuickAdd, notify, quickAddMode } = useUi()
  const [value, setValue] = useState('')
  const [modeOverride, setModeOverride] = useState<Mode | null>(quickAddMode)
  const [categoryId, setCategoryId] = useState<Id | null>(null)

  const parsed = useMemo(() => parseNaturalInput(value), [value])
  const mode: Mode = modeOverride ?? (parsed.suggestsActivity ? 'activity' : 'todo')
  const canSubmit = (parsed.title.trim() || value.trim()).length > 0

  async function submit() {
    const title = parsed.title.trim() || value.trim()
    if (!title) return

    if (mode === 'todo') {
      await createTodo({
        title,
        plannedDate: parsed.dayKey ?? todayKey(),
        plannedTime: parsed.minuteOfDay,
        estimatedDuration: parsed.durationMinutes,
        categoryId,
      })
      notify('Task added')
    } else {
      await createActivity({
        title,
        date: parsed.dayKey ?? todayKey(),
        duration: parsed.durationMinutes,
        startTime: parsed.minuteOfDay,
        categoryId,
      })
      notify('Activity logged')
    }

    track('quick_add_used', { mode, parsed_tokens: parsed.tokens.length })
    closeQuickAdd()
  }

  return (
    <>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <Sparkles className="size-4 shrink-0 text-accent" strokeWidth={2} aria-hidden="true" />
        <input
          autoFocus
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              void submit()
            }
            if (event.key === 'Tab') {
              event.preventDefault()
              setModeOverride(mode === 'todo' ? 'activity' : 'todo')
            }
          }}
          placeholder="Add a task, or log what you just did…"
          aria-label="Quick add"
          className="min-w-0 flex-1 bg-transparent text-[15px] text-fg outline-none"
        />
        <kbd className="hidden shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-faint sm:block">
          Esc
        </kbd>
      </div>

      <div className="border-t border-line px-4 py-3">
        <div
          role="radiogroup"
          aria-label="What are you adding"
          className="flex items-center gap-1.5"
        >
          {(['todo', 'activity'] as const).map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={mode === option}
              onClick={() => setModeOverride(option)}
              className={cn(
                'inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] font-medium transition-colors duration-150',
                mode === option
                  ? 'border-accent-line bg-accent-soft text-accent'
                  : 'border-line text-fg-muted hover:text-fg',
              )}
            >
              {option === 'todo' ? (
                <ListTodo className="size-3.5" strokeWidth={2} aria-hidden="true" />
              ) : (
                <Check className="size-3.5" strokeWidth={2.2} aria-hidden="true" />
              )}
              {option === 'todo' ? 'Task' : 'Activity'}
            </button>
          ))}

          <span className="mx-0.5 h-4 w-px bg-line" aria-hidden="true" />
          <CategoryPicker
            value={categoryId}
            onChange={setCategoryId}
            scope={mode === 'todo' ? 'todo' : 'activity'}
          />

          <kbd className="ml-auto hidden rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-faint sm:block">
            Tab to switch
          </kbd>
        </div>

        {canSubmit ? (
          <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px]">
            <span className="text-fg-faint">Will add</span>
            <span className="font-medium text-fg">{parsed.title.trim() || value.trim()}</span>
            {parsed.tokens.map((token) => {
              const Icon = TOKEN_ICONS[token.kind]
              return (
                <span
                  key={token.kind}
                  className="inline-flex items-center gap-1 rounded-sm bg-accent-soft px-1.5 py-0.5 text-[11.5px] text-accent"
                >
                  <Icon className="size-3" strokeWidth={2} aria-hidden="true" />
                  {token.label}
                </span>
              )
            })}
          </div>
        ) : (
          <p className="mt-3 text-[12.5px] text-fg-faint">
            Try{' '}
            {EXAMPLES.map((example, index) => (
              <span key={example}>
                {index > 0 && ' · '}
                <button
                  type="button"
                  onClick={() => setValue(example)}
                  className="underline decoration-line-strong underline-offset-2 transition-colors hover:text-fg-muted"
                >
                  {example}
                </button>
              </span>
            ))}
          </p>
        )}
      </div>
    </>
  )
}
