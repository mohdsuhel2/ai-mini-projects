'use client'

import { Check, Pause, Play, X } from 'lucide-react'
import {
  completeTimer,
  discardTimer,
  elapsedMs,
  pauseTimer,
  resumeTimer,
} from '@/features/timer/api'
import { useCategoryMap, useTimer } from '@/hooks/use-data'
import { useNow } from '@/hooks/use-now'
import { useUi } from '@/store/ui-context'
import { IconButton } from '@/components/common/icon-button'
import { CategoryDot } from '@/components/common/category-badge'
import { formatDuration, formatStopwatch } from '@/lib/date/format'
import { track } from '@/lib/analytics'
import { cn } from '@/lib/utils/cn'

/**
 * Present only while something is being timed, and quiet even then. The timer
 * is an option the user reached for, never a state the app puts them in.
 */
export function FocusTimer({ className }: { className?: string }) {
  const timer = useTimer()
  const categories = useCategoryMap()
  const { notify } = useUi()
  const running = timer != null && timer.runningSince != null
  // The clock only ticks while a timer is actually running on screen.
  const now = useNow(1000, running)

  if (!timer) return null

  const elapsed = elapsedMs(timer, now)
  const category = timer.categoryId ? categories.get(timer.categoryId) : undefined

  return (
    <div
      role="status"
      aria-live="off"
      className={cn(
        'flex items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2.5',
        'shadow-[var(--shadow-pop)]',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'size-1.5 shrink-0 rounded-full',
          running ? 'animate-pulse bg-accent' : 'bg-fg-faint',
        )}
      />

      <div className="min-w-0 flex-1">
        <p className="truncate text-[11px] uppercase tracking-[0.07em] text-fg-faint">
          {running ? 'Currently doing' : 'Paused'}
        </p>
        <p className="flex items-center gap-1.5 truncate text-[13px] font-medium text-fg">
          <CategoryDot category={category} />
          {timer.title}
        </p>
      </div>

      <span className="tnum shrink-0 text-[15px] font-medium tabular-nums text-fg">
        {formatStopwatch(elapsed)}
      </span>

      <div className="flex shrink-0 items-center gap-0.5">
        <IconButton
          label={running ? 'Pause timer' : 'Resume timer'}
          size="sm"
          onClick={() => void (running ? pauseTimer() : resumeTimer())}
        >
          {running ? (
            <Pause className="size-3.5" strokeWidth={2} />
          ) : (
            <Play className="size-3.5" strokeWidth={2} />
          )}
        </IconButton>

        <IconButton
          label="Complete and record"
          size="sm"
          className="text-success hover:bg-accent-soft"
          onClick={async () => {
            const minutes = await completeTimer()
            track('timer_completed', { minutes: minutes ?? 0 })
            if (minutes) notify(`Recorded ${formatDuration(minutes)}`)
          }}
        >
          <Check className="size-4" strokeWidth={2.4} />
        </IconButton>

        <IconButton
          label="Discard timer"
          size="sm"
          onClick={() => {
            void discardTimer()
            notify('Timer discarded')
          }}
        >
          <X className="size-3.5" strokeWidth={2} />
        </IconButton>
      </div>
    </div>
  )
}
