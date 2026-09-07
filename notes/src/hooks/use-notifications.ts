'use client'

import { useEffect, useMemo, useRef } from 'react'
import { applyBadge, badgeCount } from '@/features/notifications/badge'
import { dueReminders } from '@/features/notifications/reminders'
import { notificationAccess, showReminder } from '@/features/notifications/permission'
import { useOpenTodos } from './use-data'
import { useNow } from './use-now'
import { formatClock } from '@/lib/date/format'
import { minutesOfDay, todayKey } from '@/lib/date/day-key'
import type { Id } from '@/types'

/** Keeps the count on the app icon in step with what is late or due today. */
export function useAppBadge(): void {
  const todos = useOpenTodos()
  // Only to catch the date rolling over; the count itself is already reactive.
  const now = useNow(60_000)
  const today = todayKey(new Date(now))

  const count = useMemo(() => (todos ? badgeCount(todos, today) : 0), [todos, today])

  useEffect(() => {
    void applyBadge(count)
  }, [count])
}

/**
 * Fires a notification as each timed task comes round, while the app is alive.
 *
 * Checked twice a minute so a reminder is never more than a few seconds late,
 * and the ids already fired are held in a ref: a re-render must not re-notify,
 * and neither must the same minute being observed twice.
 */
export function useReminders(enabled: boolean): void {
  const todos = useOpenTodos()
  const active = enabled && notificationAccess() === 'granted'
  const now = useNow(30_000, active)
  const notified = useRef<Set<Id>>(new Set())

  useEffect(() => {
    if (!active || !todos) return

    const at = new Date(now)
    const due = dueReminders(todos, minutesOfDay(at), todayKey(at), notified.current)

    for (const todo of due) {
      notified.current.add(todo.id)
      void showReminder(
        todo.title,
        `Due at ${formatClock(todo.plannedTime!)}`,
        `todo-${todo.id}`,
      )
    }
  }, [active, todos, now])
}
