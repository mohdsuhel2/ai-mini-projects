'use client'

import { useMemo, useState } from 'react'
import { AppHeader } from './app-header'
import { BottomNav } from './bottom-nav'
import { PlanPane } from '@/components/todo/plan-pane'
import { NotesPane } from '@/components/notes/notes-pane'
import { TodayPane } from './today-pane'
import { FocusTimer } from '@/components/timer/focus-timer'
import { QuickAdd } from '@/components/quick-add/quick-add'
import { SettingsDialog } from '@/components/settings/settings-dialog'
import { Toaster } from '@/components/common/toaster'
import { SegmentedControl } from '@/components/common/segmented-control'
import { PaneSkeleton } from '@/components/common/skeleton'
import { UiProvider, useUi } from '@/store/ui-context'
import { useHotkeys, type Hotkey } from '@/hooks/use-hotkeys'
import { useMounted } from '@/hooks/use-mounted'
import { useIsWide } from '@/hooks/use-media-query'
import { useDailySummary, useOpenTodos } from '@/hooks/use-data'
import { groupIdFor } from '@/features/todos/grouping'
import { todayKey } from '@/lib/date/day-key'
import { greetingFor } from '@/lib/date/format'
import { cn } from '@/lib/utils/cn'

/**
 * The two panes are one surface, not two pages: on a wide screen they sit side
 * by side so a completed task visibly crosses from plan into record. Below
 * 1100px the same two panes become a segmented switch, and below 640px a
 * bottom bar.
 */
function Workspace() {
  const { mode, setMode, pane, setPane, openQuickAdd, openSettings } = useUi()
  const isWide = useIsWide()
  const [day, setDay] = useState(() => todayKey())
  const openTodos = useOpenTodos()
  const summary = useDailySummary(day)

  const todayCount = useMemo(
    () =>
      (openTodos ?? []).filter((t) => {
        const group = groupIdFor(t.plannedDate)
        return group === 'overdue' || group === 'today' || group === 'tomorrow'
      }).length,
    [openTodos],
  )

  const hotkeys = useMemo<Hotkey[]>(
    () => [
      { key: 'k', meta: true, handler: () => openQuickAdd() },
      { key: 'n', handler: () => openQuickAdd('todo') },
      {
        key: 'a',
        handler: () => {
          setPane('today')
          openQuickAdd('activity')
        },
      },
      {
        key: 't',
        handler: () => {
          setDay(todayKey())
          setMode('day')
          setPane('today')
        },
      },
      { key: 'e', handler: () => setMode(mode === 'notes' ? 'day' : 'notes') },
      { key: '?', shift: true, handler: openSettings },
    ],
    [openQuickAdd, openSettings, setPane, setMode, mode],
  )
  useHotkeys(hotkeys)

  const showPlan = mode === 'day' && (isWide || pane === 'plan')
  const showToday = mode === 'day' && (isWide || pane === 'today')

  return (
    <div className="min-h-dvh bg-bg">
      <AppHeader />

      <main className="mx-auto max-w-[1220px] px-4 pb-28 pt-6 sm:px-6 sm:pb-16 sm:pt-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[21px] font-semibold leading-tight tracking-[-0.02em] text-fg sm:text-[24px]">
              {mode === 'notes' ? 'Notes' : greetingFor()}
            </h1>
            <p className="mt-1 text-[13px] text-fg-muted">
              {mode === 'notes'
                ? 'Everything worth keeping that has no deadline.'
                : summary && summary.completedCount > 0
                  ? `${summary.completedCount} done today · ${todayCount} still on the list`
                  : todayCount > 0
                    ? `${todayCount} thing${todayCount === 1 ? '' : 's'} on your list`
                    : 'Nothing on the list. Add something, or take the afternoon.'}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <SegmentedControl
              aria-label="Surface"
              value={mode}
              onChange={setMode}
              options={[
                { value: 'notes', label: 'Notes' },
                { value: 'day', label: 'Todos' },
              ]}
              className="hidden sm:inline-flex"
            />

            {!isWide && mode === 'day' && (
              <SegmentedControl
                aria-label="Section"
                value={pane}
                onChange={setPane}
                options={[
                  { value: 'plan', label: 'Plan', count: todayCount },
                  { value: 'today', label: 'Today' },
                ]}
                className="hidden sm:inline-flex"
              />
            )}
          </div>
        </div>

        <FocusTimer className="mb-5" />

        {mode === 'notes' && <NotesPane />}

        <div
          className={cn(
            'grid gap-x-10 gap-y-8',
            mode !== 'day' && 'hidden',
            isWide && 'grid-cols-[minmax(0,1.12fr)_minmax(0,1fr)]',
          )}
        >
          {showPlan && (
            <section aria-label="Things to do" className="min-w-0">
              {isWide && <PaneHeading>Things to do</PaneHeading>}
              <PlanPane />
            </section>
          )}

          {showToday && (
            <section
              aria-label="What I did"
              className={cn('min-w-0', isWide && 'border-l border-line pl-10')}
            >
              {isWide && <PaneHeading>What I did</PaneHeading>}
              <TodayPane day={day} onDayChange={setDay} />
            </section>
          )}
        </div>
      </main>

      <BottomNav openCount={todayCount} />
      <QuickAdd />
      <SettingsDialog />
      <Toaster />
    </div>
  )
}

function PaneHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-3 px-1 text-[10.5px] font-bold uppercase tracking-[0.11em] text-fg-subtle">
      {children}
    </h2>
  )
}

/**
 * The app renders nothing real until it is mounted in the browser: every value
 * it shows comes from IndexedDB, which does not exist during server rendering.
 * The skeleton below is what the crawler and the first paint both see.
 */
function BootSkeleton() {
  return (
    <div className="min-h-dvh bg-bg">
      <div className="h-14 border-b border-line" />
      <div className="mx-auto max-w-[1220px] px-4 pt-8 sm:px-6">
        <div className="mb-8 h-[3.25rem] w-64 animate-pulse rounded-md bg-line/70" />
        <div className="grid gap-10 lg:grid-cols-2">
          <PaneSkeleton rows={5} />
          <div className="hidden lg:block">
            <PaneSkeleton rows={4} />
          </div>
        </div>
      </div>
    </div>
  )
}

export function SimplyNotesApp() {
  const mounted = useMounted()
  if (!mounted) return <BootSkeleton />

  return (
    <UiProvider>
      <Workspace />
    </UiProvider>
  )
}
