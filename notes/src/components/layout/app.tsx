'use client'

import { useMemo, useState } from 'react'
import { AppHeader } from './app-header'
import { BottomNav } from './bottom-nav'
import { SideRail } from './side-rail'
import { PlanPane } from '@/components/todo/plan-pane'
import { NotesPane } from '@/components/notes/notes-pane'
import { InsightsPane } from '@/components/insights/insights-pane'
import { TodayPane } from './today-pane'
import { FocusTimer } from '@/components/timer/focus-timer'
import { QuickAdd } from '@/components/quick-add/quick-add'
import { SettingsDialog } from '@/components/settings/settings-dialog'
import { Toaster } from '@/components/common/toaster'
import { SegmentedControl } from '@/components/common/segmented-control'
import { PaneSkeleton } from '@/components/common/skeleton'
import { UiProvider, useUi, type Pane } from '@/store/ui-context'
import { useHotkeys, type Hotkey } from '@/hooks/use-hotkeys'
import { useMounted } from '@/hooks/use-mounted'
import { useIsWide } from '@/hooks/use-media-query'
import { useOpenTodos, useRecurringRollForward, useSettings } from '@/hooks/use-data'
import { useAppBadge, useReminders } from '@/hooks/use-notifications'
import { groupIdFor } from '@/features/todos/grouping'
import { todayKey } from '@/lib/date/day-key'
import { cn } from '@/lib/utils/cn'

/**
 * The two halves of a day are one surface, not two pages: on a wide screen they
 * sit side by side, divided by a hairline, so a completed task visibly crosses
 * from plan into record. Below 1100px the same two become a switch in the
 * header, and below 640px a bottom bar. Surfaces themselves live in the rail.
 */
function Workspace() {
  const { mode, setMode, pane, setPane, openQuickAdd, openSettings } = useUi()
  const isWide = useIsWide()
  const [day, setDay] = useState(() => todayKey())
  const openTodos = useOpenTodos()
  const settings = useSettings()
  useRecurringRollForward()
  useAppBadge()
  useReminders(Boolean(settings.remindersEnabled))

  const todayCount = useMemo(
    () =>
      (openTodos ?? []).filter((t) => {
        const group = groupIdFor(t.plannedDate)
        return group === 'pending' || group === 'today' || group === 'tomorrow'
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
      { key: 'i', handler: () => setMode(mode === 'insights' ? 'day' : 'insights') },
      { key: '?', shift: true, handler: openSettings },
    ],
    [openQuickAdd, openSettings, setPane, setMode, mode],
  )
  useHotkeys(hotkeys)

  const showPlan = mode === 'day' && (isWide || pane === 'plan')
  const showToday = mode === 'day' && (isWide || pane === 'today')

  return (
    <div className="min-h-dvh bg-bg sm:pl-[76px]">
      <SideRail />
      <AppHeader />

      <main className="px-4 pb-28 pt-6 sm:px-7 sm:pb-12 sm:pt-7">
        <FocusTimer className="mb-5" />

        {mode === 'notes' && <NotesPane />}
        {mode === 'insights' && <InsightsPane />}

        {mode === 'day' && !isWide && (
          <SegmentedControl<Pane>
            aria-label="Section"
            value={pane}
            onChange={setPane}
            className="mb-5 flex w-full [&>button]:flex-1 [&>button]:justify-center sm:hidden"
            options={[
              { value: 'plan', label: 'To Do', count: todayCount },
              { value: 'today', label: 'What I Did' },
            ]}
          />
        )}

        <div
          className={cn(
            'gap-x-9',
            mode === 'day' ? 'grid' : 'hidden',
            isWide && 'grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]',
          )}
        >
          {showPlan && (
            <section aria-label="Things to do" className="min-w-0">
              <PaneHeading
                title="Things to do"
                detail={
                  todayCount > 0
                    ? `${todayCount} due soon`
                    : 'Nothing due — add what matters today'
                }
              />
              <PlanPane />
            </section>
          )}

          {showToday && (
            <section
              aria-label="What I did"
              className={cn('min-w-0', isWide && 'border-l border-line pl-8')}
            >
              <PaneHeading title="What I did" />
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

function PaneHeading({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">{title}</h2>
      {detail && <p className="mt-1 text-[13px] text-fg-muted">{detail}</p>}
    </div>
  )
}

/**
 * The app renders nothing real until it is mounted in the browser: every value
 * it shows comes from IndexedDB, which does not exist during server rendering.
 * The skeleton below is what the crawler and the first paint both see.
 */
function BootSkeleton() {
  return (
    <div className="min-h-dvh bg-bg sm:pl-[76px]">
      <div className="fixed inset-y-0 left-0 hidden w-[76px] border-r border-line bg-surface sm:block" />
      <div className="h-[76px] border-b border-line bg-surface" />
      <div className="px-4 pt-7 sm:px-7">
        <div className="mb-6 h-7 w-48 animate-pulse rounded-md bg-line/70" />
        <div className="grid gap-9 lg:grid-cols-2">
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
