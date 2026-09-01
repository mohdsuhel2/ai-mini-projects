'use client'

import { useState } from 'react'
import { Dialog } from '@/components/common/dialog'
import { Button } from '@/components/common/button'
import { CategoryPicker } from './category-picker'
import { DateChips } from './date-chips'
import { DurationChips } from '@/components/common/duration-chips'
import { updateTodo } from '@/features/todos/api'
import { formatClock } from '@/lib/date/format'
import type { Id, Todo } from '@/types'

interface TodoDetailProps {
  todo: Todo | null
  onClose: () => void
  onDelete: (todo: Todo) => void
}

function toTimeInput(minute: number | null | undefined): string {
  if (minute == null) return ''
  const h = String(Math.floor(minute / 60)).padStart(2, '0')
  const m = String(minute % 60).padStart(2, '0')
  return `${h}:${m}`
}

function fromTimeInput(value: string): number | null {
  if (!value) return null
  const [h, m] = value.split(':').map(Number)
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null
  return h * 60 + m
}

/** Everything a task can carry, in the one place it is worth showing at once. */
export function TodoDetail({ todo, onClose, onDelete }: TodoDetailProps) {
  return (
    <Dialog
      open={todo !== null}
      onClose={onClose}
      title="Task details"
      description={
        todo?.plannedTime != null ? `Scheduled for ${formatClock(todo.plannedTime)}` : undefined
      }
    >
      {/* Keyed by task, and mounted only when there is one: the form seeds its
          own state from props at mount instead of syncing in an effect. */}
      {todo && <TodoDetailForm key={todo.id} todo={todo} onClose={onClose} onDelete={onDelete} />}
    </Dialog>
  )
}

function TodoDetailForm({
  todo,
  onClose,
  onDelete,
}: {
  todo: Todo
  onClose: () => void
  onDelete: (todo: Todo) => void
}) {
  const [title, setTitle] = useState(todo.title)
  const [notes, setNotes] = useState(todo.notes ?? '')
  const [day, setDay] = useState<string | null>(todo.plannedDate)
  const [time, setTime] = useState(() => toTimeInput(todo.plannedTime))
  const [duration, setDuration] = useState<number | null>(todo.estimatedDuration ?? null)
  const [categoryId, setCategoryId] = useState<Id | null>(todo.categoryId ?? null)

  async function save() {
    const trimmed = title.trim()
    if (!trimmed) return
    await updateTodo(todo.id, {
      title: trimmed,
      notes: notes.trim() || null,
      plannedDate: day,
      plannedTime: fromTimeInput(time),
      estimatedDuration: duration,
      categoryId,
    })
    onClose()
  }

  return (
    <>
      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
        <div className="space-y-1.5">
          <label htmlFor="todo-title" className="text-[12px] font-medium text-fg-muted">
            Title
          </label>
          <input
            id="todo-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void save()
            }}
            className="w-full rounded-md border border-line bg-bg px-3 py-2 text-[14px] outline-none transition-colors focus:border-line-strong"
          />
        </div>

        <div className="space-y-2">
          <span className="text-[12px] font-medium text-fg-muted">When</span>
          <DateChips value={day} onChange={setDay} allowNone />
          <label className="flex items-center gap-2 pt-1 text-[12.5px] text-fg-muted">
            At
            <input
              type="time"
              value={time}
              onChange={(event) => setTime(event.target.value)}
              aria-label="Planned time"
              className="tnum rounded-md border border-line bg-bg px-2 py-1 text-[12.5px] text-fg outline-none focus:border-line-strong"
            />
            {time && (
              <button
                type="button"
                onClick={() => setTime('')}
                className="text-[12px] text-fg-subtle underline-offset-2 hover:text-fg hover:underline"
              >
                Clear
              </button>
            )}
          </label>
        </div>

        <div className="space-y-2">
          <span className="text-[12px] font-medium text-fg-muted">Category</span>
          <div>
            <CategoryPicker value={categoryId} onChange={setCategoryId} scope="todo" />
          </div>
        </div>

        <div className="space-y-2">
          <span className="text-[12px] font-medium text-fg-muted">Expected to take</span>
          <DurationChips value={duration} onChange={setDuration} />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="todo-notes" className="text-[12px] font-medium text-fg-muted">
            Notes
          </label>
          <textarea
            id="todo-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={3}
            placeholder="Anything worth remembering"
            className="w-full resize-none rounded-md border border-line bg-bg px-3 py-2 text-[13.5px] leading-relaxed outline-none transition-colors focus:border-line-strong"
          />
        </div>
      </div>

      <footer className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">
        <Button
          variant="ghost"
          size="sm"
          className="text-danger hover:bg-danger-soft hover:text-danger"
          onClick={() => onDelete(todo)}
        >
          Delete
        </Button>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={save}>
            Save
          </Button>
        </div>
      </footer>
    </>
  )
}
