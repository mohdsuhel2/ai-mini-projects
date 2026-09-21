'use client'

import { Button } from '@/components/common/button'
import { carryForwardCandidates } from '@/features/analytics/review'
import { rollOverOverdue, rescheduleTodo } from '@/features/todos/api'
import { useOpenTodos, useWeeklyReview } from '@/hooks/use-data'
import { useUi } from '@/store/ui-context'
import { formatDayLabel, formatDuration } from '@/lib/date/format'
import { shiftDay, todayKey } from '@/lib/date/day-key'
import type { RangeId } from '@/features/analytics/range'
import { cn } from '@/lib/utils/cn'

export function WeeklyReviewPanel({ range }: { range: RangeId }) {
  const review = useWeeklyReview(range)
  const openTodos = useOpenTodos()
  const { notify, showDay } = useUi()
  const today = todayKey()
  const tomorrow = shiftDay(today, 1)

  if (!review || !openTodos) return null

  const carry = carryForwardCandidates(openTodos, today)

  async function moveAllOverdueToToday() {
    const count = await rollOverOverdue(today)
    if (count > 0) notify(`Moved ${count} overdue task${count === 1 ? '' : 's'} to today`)
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <ReviewStat label="Tracked" value={formatDuration(review.trackedMinutes) || '0m'} />
        <ReviewStat
          label="Plan completion"
          value={review.plan.rate != null ? `${Math.round(review.plan.rate * 100)}%` : '—'}
          detail={`${review.plan.completed} of ${review.plan.planned}`}
        />
        <ReviewStat label="Logging streak" value={`${review.streak.current}d`} />
        <ReviewStat label="Someday backlog" value={String(review.somedayCount)} />
      </div>

      {(review.overdueCount > 0 || carry.length > 0) && (
        <section className="rounded-2xl border border-card-line bg-surface p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-[14px] font-semibold text-fg">Carry forward</h3>
              <p className="mt-1 text-[13px] text-fg-subtle">
                {review.overdueCount > 0
                  ? `${review.overdueCount} overdue · ${carry.length} still open from this window`
                  : `${review.unfinished.length} dated tasks still open in this window`}
              </p>
            </div>
            {review.overdueCount > 0 && (
              <Button variant="secondary" size="sm" onClick={() => void moveAllOverdueToToday()}>
                Move overdue to today
              </Button>
            )}
          </div>
          {carry.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {carry.slice(0, 6).map((todo) => (
                <li
                  key={todo.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-bg-sunk/80 px-3 py-2"
                >
                  <span className="min-w-0 truncate text-[13px] font-medium text-fg">{todo.title}</span>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className="text-[12px] text-fg-subtle">
                      {todo.plannedDate ? formatDayLabel(todo.plannedDate) : 'Someday'}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void rescheduleTodo(todo.id, tomorrow)}
                    >
                      Tomorrow
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="rounded-2xl border border-card-line bg-surface p-4 sm:p-5">
        <h3 className="text-[14px] font-semibold text-fg">Next steps</h3>
        <ul className="mt-3 space-y-2 text-[13px] text-fg-subtle">
          {review.somedayCount > 0 && (
            <li>
              You have {review.somedayCount} Someday task{review.somedayCount === 1 ? '' : 's'} — pick one
              for {formatDayLabel(tomorrow)}.
            </li>
          )}
          {review.plan.rate != null && review.plan.rate < 0.6 && (
            <li>Planned completion was below 60% — try fewer dated tasks per day.</li>
          )}
          {review.streak.current === 0 && (
            <li>Log one activity today to restart your tracking streak.</li>
          )}
        </ul>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" size="sm" onClick={() => showDay('plan')}>
            Open plan
          </Button>
          <Button variant="secondary" size="sm" onClick={() => showDay('today')}>
            Open today
          </Button>
        </div>
      </section>
    </div>
  )
}

function ReviewStat({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string
}) {
  return (
    <div className="rounded-2xl border border-card-line bg-surface p-3.5">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-faint">{label}</p>
      <p className={cn('tnum mt-1 text-[19px] font-semibold tracking-[-0.02em] text-fg')}>{value}</p>
      {detail && <p className="tnum mt-0.5 text-[11.5px] text-fg-faint">{detail}</p>}
    </div>
  )
}
