import { db, TIMER_KEY } from '@/lib/db/database'
import { now } from '@/lib/db/records'
import { instantToDayKey, instantToMinutes } from '@/lib/date/day-key'
import { createActivity } from '@/features/activities/api'
import { completeTodo } from '@/features/todos/api'
import type { Id, TimerState } from '@/types'

/**
 * The running timer lives in the database rather than component state so it
 * survives a reload, a closed tab, or a phone locking mid-task.
 */

export async function getTimer(): Promise<TimerState | undefined> {
  return db().timer.get(TIMER_KEY)
}

export function elapsedMs(timer: TimerState, at: number = Date.now()): number {
  const live = timer.runningSince != null ? at - timer.runningSince : 0
  return Math.max(0, timer.accumulatedMs + live)
}

export function elapsedMinutes(timer: TimerState, at: number = Date.now()): number {
  return Math.round(elapsedMs(timer, at) / 60_000)
}

export async function startTimer(input: {
  title: string
  todoId?: Id | null
  categoryId?: Id | null
}): Promise<void> {
  const stamp = now()
  await db().timer.put({
    id: TIMER_KEY,
    title: input.title.trim(),
    todoId: input.todoId ?? null,
    categoryId: input.categoryId ?? null,
    runningSince: stamp,
    accumulatedMs: 0,
    startedAt: stamp,
    updatedAt: stamp,
  })
}

export async function pauseTimer(): Promise<void> {
  const timer = await getTimer()
  if (!timer || timer.runningSince == null) return
  const stamp = now()
  await db().timer.put({
    ...timer,
    accumulatedMs: timer.accumulatedMs + (stamp - timer.runningSince),
    runningSince: null,
    updatedAt: stamp,
  })
}

export async function resumeTimer(): Promise<void> {
  const timer = await getTimer()
  if (!timer || timer.runningSince != null) return
  const stamp = now()
  await db().timer.put({ ...timer, runningSince: stamp, updatedAt: stamp })
}

export async function discardTimer(): Promise<void> {
  await db().timer.delete(TIMER_KEY)
}

/**
 * Ends the timer and writes what it measured. When the timer was started from a
 * todo, that todo is completed with the measured duration rather than a guess.
 */
export async function completeTimer(): Promise<number | null> {
  const timer = await getTimer()
  if (!timer) return null

  const stamp = now()
  const minutes = Math.max(1, elapsedMinutes(timer, stamp))

  if (timer.todoId) {
    await completeTodo(timer.todoId, minutes)
  } else {
    const endTime = instantToMinutes(stamp)
    await createActivity({
      title: timer.title,
      categoryId: timer.categoryId,
      date: instantToDayKey(stamp),
      duration: minutes,
      startTime: Math.max(0, endTime - minutes),
      endTime,
      source: 'TIMER',
    })
  }

  await discardTimer()
  return minutes
}
