'use client'

import { ListTodoIcon } from '@/lib/app-icons'
import { useTodosLinkedToNote } from '@/hooks/use-data'
import { useUi } from '@/store/ui-context'
import { formatDayLabel } from '@/lib/date/format'
import type { Id } from '@/types'

export function LinkedTodosPanel({ noteId }: { noteId: Id }) {
  const linked = useTodosLinkedToNote(noteId)
  const { showDay } = useUi()
  if (!linked || linked.length === 0) return null

  return (
    <section className="mb-4 rounded-2xl border border-card-line bg-bg-sunk/60 px-3.5 py-3">
      <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
        <ListTodoIcon size="xs" />
        Linked tasks
      </h3>
      <ul className="space-y-1">
        {linked.map((todo) => (
          <li key={todo.id}>
            <button
              type="button"
              onClick={() => showDay('plan')}
              className="flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg px-1 py-1.5 text-left text-[13px] transition-colors hover:bg-surface-hover hover:text-accent"
            >
              <span className="truncate font-medium text-fg">{todo.title}</span>
              <span className="shrink-0 text-[12px] text-fg-subtle">
                {todo.plannedDate ? formatDayLabel(todo.plannedDate) : 'Someday'}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
