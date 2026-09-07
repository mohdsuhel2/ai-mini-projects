'use client'

import { useState } from 'react'
import { ChevronRight, CornerUpRight } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { TodoComposer } from './todo-composer'
import { TodoRow } from './todo-row'
import { TodoDetail } from './todo-detail'
import { EmptyState } from '@/components/common/empty-state'
import { PaneSkeleton } from '@/components/common/skeleton'
import { groupImpliesDate, groupTodos, type GroupId } from '@/features/todos/grouping'
import {
  completeTodo,
  deleteTodo,
  reopenTodo,
  rollOverOverdue,
  updateTodo,
} from '@/features/todos/api'
import { startTimer } from '@/features/timer/api'
import { useCategoryMap, useOpenTodos, useSettings } from '@/hooks/use-data'
import { useUi } from '@/store/ui-context'
import { track } from '@/lib/analytics'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { cn } from '@/lib/utils/cn'
import type { Todo } from '@/types'

export function PlanPane() {
  const todos = useOpenTodos()
  const categories = useCategoryMap()
  const { notify } = useUi()
  const settings = useSettings()
  const reducedMotion = usePrefersReducedMotion()
  // One group at a time. With every bucket open the pane becomes a wall of
  // tasks and "today" stops being the thing you are looking at; an accordion
  // keeps the answer to "what now?" on one screen. `null` means all closed —
  // clicking the open group shuts it rather than trapping you in it.
  const [openGroup, setOpenGroup] = useState<GroupId | null>(null)
  const [touched, setTouched] = useState(false)
  const [editing, setEditing] = useState<Todo | null>(null)

  const groups = todos ? groupTodos(todos, undefined, settings.firstDayOfWeek) : []

  // Today, always. Opening on Pending because something is late makes the app
  // greet you with what you failed at; today is what you came to look at.
  const defaultOpen: GroupId | null = 'today'
  const activeGroup = touched ? openGroup : defaultOpen

  function toggle(id: GroupId) {
    setTouched(true)
    setOpenGroup((current) => ((touched ? current : defaultOpen) === id ? null : id))
  }

  async function handleComplete(todo: Todo, duration: number | null) {
    await completeTodo(todo.id, duration)
    track('todo_completed', { with_duration: duration != null })
    notify('Moved to your day', { label: 'Undo', onClick: () => void reopenTodo(todo.id) })
  }

  async function handleDelete(todo: Todo) {
    setEditing(null)
    await deleteTodo(todo.id)
    notify('Task deleted', {
      label: 'Undo',
      onClick: () => void updateTodo(todo.id, { deletedAt: null }),
    })
  }

  const transition = reducedMotion ? { duration: 0 } : { duration: 0.2, ease: [0.22, 1, 0.36, 1] as const }

  return (
    <div className="flex flex-col gap-5">
      <TodoComposer />

      {!todos ? (
        <PaneSkeleton rows={4} />
      ) : groups.every((group) => group.todos.length === 0) ? (
        <EmptyState
          title="Nothing planned yet."
          hint="Add something you'd like to get done today. It takes one line."
          glyph="plan"
        />
      ) : (
        <div className="space-y-5">
          {groups.map((group) => {
            const isCollapsed = activeGroup !== group.id
            const isPending = group.id === 'pending'

            return (
              <section key={group.id} aria-labelledby={`group-${group.id}`}>
                <div className="mb-2 flex items-center gap-2">
                  <button
                    type="button"
                    id={`group-${group.id}`}
                    onClick={() => toggle(group.id)}
                    aria-expanded={!isCollapsed}
                    className="group -ml-1 inline-flex items-center gap-1.5 rounded-md py-1 pl-1 pr-1.5 transition-colors hover:bg-surface-hover"
                  >
                    <ChevronRight
                      className={cn(
                        'size-3 text-fg-faint transition-transform duration-200',
                        !isCollapsed && 'rotate-90',
                      )}
                      strokeWidth={2.5}
                      aria-hidden="true"
                    />
                    <span
                      className={cn(
                        'text-[14px] font-semibold tracking-[-0.01em]',
                        isPending ? 'text-danger' : 'text-fg',
                      )}
                    >
                      {group.label}
                    </span>
                    {group.todos.length > 0 && (
                      <span className="tnum grid h-5 min-w-5 place-items-center rounded-full bg-bg-sunk px-1.5 text-[11px] font-semibold text-fg-muted">
                        {group.todos.length}
                      </span>
                    )}
                  </button>

                  {isPending && group.todos.length > 0 && (
                    <button
                      type="button"
                      onClick={async () => {
                        const moved = await rollOverOverdue()
                        if (moved > 0) {
                          track('todo_rescheduled', { count: moved, bulk: true })
                          notify(`${moved} task${moved === 1 ? '' : 's'} moved to today`)
                        }
                      }}
                      className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11.5px] text-fg-subtle transition-colors hover:bg-surface-hover hover:text-fg"
                    >
                      <CornerUpRight className="size-3" strokeWidth={2} aria-hidden="true" />
                      Move to today
                    </button>
                  )}
                </div>

                {!isCollapsed && (
                  <div className="rounded-2xl border border-card-line bg-surface">
                    <AnimatePresence initial={false} mode="popLayout">
                      {/* The divider lives on the wrapper, not the row: the row
                          is an only child here, so `last:` on it would always
                          match and no rule would ever be drawn. */}
                      {group.todos.map((todo) => (
                        <motion.div
                          key={todo.id}
                          className="relative [&:not(:last-child)]:after:absolute [&:not(:last-child)]:after:inset-x-3.5 [&:not(:last-child)]:after:bottom-0 [&:not(:last-child)]:after:h-px [&:not(:last-child)]:after:bg-card-line"
                          layout={!reducedMotion}
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, x: 12, transition: { duration: reducedMotion ? 0 : 0.16 } }}
                          transition={transition}
                        >
                          <TodoRow
                            todo={todo}
                            category={
                              todo.categoryId ? categories.get(todo.categoryId) : undefined
                            }
                            hideDate={groupImpliesDate(group.id)}
                            onComplete={(duration) => void handleComplete(todo, duration)}
                            onReopen={() => void reopenTodo(todo.id)}
                            onEdit={() => setEditing(todo)}
                            onDelete={() => void handleDelete(todo)}
                            onStartTimer={() => {
                              void startTimer({
                                title: todo.title,
                                todoId: todo.id,
                                categoryId: todo.categoryId,
                              })
                              track('timer_started', { from_todo: true })
                            }}
                          />
                        </motion.div>
                      ))}
                    </AnimatePresence>

                    {group.todos.length === 0 && (
                      <p className="px-3.5 py-3 text-[13px] text-fg-faint">
                        Nothing scheduled. Enjoy the space, or add something above.
                      </p>
                    )}
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}

      <TodoDetail
        todo={editing}
        onClose={() => setEditing(null)}
        onDelete={(todo) => void handleDelete(todo)}
      />
    </div>
  )
}
