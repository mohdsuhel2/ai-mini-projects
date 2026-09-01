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
  const [collapsed, setCollapsed] = useState<Set<GroupId>>(new Set())
  const [editing, setEditing] = useState<Todo | null>(null)

  const groups = todos ? groupTodos(todos, undefined, settings.firstDayOfWeek) : []

  function toggle(id: GroupId) {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
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
            const isCollapsed = collapsed.has(group.id)
            const isOverdue = group.id === 'overdue'

            return (
              <section key={group.id} aria-labelledby={`group-${group.id}`}>
                <div className="mb-1 flex items-center gap-2 px-3">
                  <button
                    type="button"
                    id={`group-${group.id}`}
                    onClick={() => toggle(group.id)}
                    aria-expanded={!isCollapsed}
                    className="group -ml-1 inline-flex items-center gap-1 rounded-md py-1 pl-1 pr-1.5 transition-colors hover:bg-surface-hover"
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
                        'text-[10.5px] font-bold uppercase tracking-[0.1em]',
                        isOverdue ? 'text-danger' : 'text-fg-subtle',
                      )}
                    >
                      {group.label}
                    </span>
                    <span className="tnum ml-0.5 text-[11px] font-medium text-fg-faint">
                      {group.todos.length > 0 ? group.todos.length : ''}
                    </span>
                  </button>

                  {isOverdue && group.todos.length > 0 && (
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
                  <div className="space-y-0.5">
                    <AnimatePresence initial={false} mode="popLayout">
                      {group.todos.map((todo) => (
                        <motion.div
                          key={todo.id}
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
                      <p className="px-3 py-2 text-[13px] text-fg-faint">
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
