'use client'

import { CalendarDays, Moon, Settings2, Sun } from 'lucide-react'
import { BrandMark } from './brand'
import { IconButton } from '@/components/common/icon-button'
import { SegmentedControl } from '@/components/common/segmented-control'
import { useTheme } from '@/hooks/use-theme'
import { useUi, type Pane } from '@/store/ui-context'
import { useIsWide } from '@/hooks/use-media-query'
import { formatDayFull, formatWallClock } from '@/lib/date/format'
import { todayKey } from '@/lib/date/day-key'
import { useNow } from '@/hooks/use-now'

/**
 * The header answers three questions and no more: what is this, which half of
 * the day am I looking at, and what day is it. Navigation between surfaces
 * lives in the rail, so the centre stays free for the one switch that changes
 * what the page is showing.
 */
export function AppHeader() {
  const { resolved, toggle } = useTheme()
  const { mode, pane, setPane, openSettings } = useUi()
  const isWide = useIsWide()
  // Seconds are on show, so this ticks every one of them. It also carries the
  // date over midnight, which a static render would not.
  const now = useNow(1000)
  const clock = new Date(now)

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface">
      <div className="relative flex h-[76px] items-center gap-4 px-4 sm:px-7">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark className="size-7 rounded-lg sm:hidden" />
          <div className="min-w-0">
            <p className="truncate text-[17px] font-semibold leading-tight tracking-[-0.02em] text-fg">
              Simply Notes
            </p>
            <p className="mt-0.5 hidden text-[12.5px] leading-none text-fg-faint sm:block">
              Plan less. Remember more.
            </p>
          </div>
        </div>

        {/* Only the day surface is split in two, and only while the two halves
            are exclusive — once both fit side by side the switch would have
            nothing left to switch, so it goes rather than sitting there inert. */}
        {mode === 'day' && !isWide && (
          <SegmentedControl<Pane>
            aria-label="Section"
            value={pane}
            onChange={setPane}
            className="absolute left-1/2 hidden -translate-x-1/2 sm:inline-flex"
            options={[
              { value: 'plan', label: 'To Do' },
              { value: 'today', label: 'What I Did' },
            ]}
          />
        )}

        <div className="ml-auto flex items-center gap-2">
          <p className="hidden items-center gap-2 rounded-full bg-bg-sunk px-3.5 py-2 text-[13px] font-medium text-fg-muted sm:inline-flex">
            <CalendarDays className="size-4 text-fg-faint" strokeWidth={2} aria-hidden="true" />
            {/* The date is the first thing to go when the bar gets tight: the
                clock is the half that changes, and the one you glance up for. */}
            <span className="hidden md:inline">{formatDayFull(todayKey(clock))}</span>
            <span className="hidden text-fg-faint md:inline" aria-hidden="true">
              ·
            </span>
            <span className="tnum">{formatWallClock(clock)}</span>
          </p>

          <IconButton
            label={resolved === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            onClick={toggle}
            className="size-9 rounded-full"
          >
            {resolved === 'dark' ? (
              <Sun className="size-[18px]" strokeWidth={2} />
            ) : (
              <Moon className="size-[18px]" strokeWidth={2} />
            )}
          </IconButton>

          <IconButton
            label="Settings"
            onClick={openSettings}
            className="size-9 rounded-full bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent"
          >
            <Settings2 className="size-[18px]" strokeWidth={2} />
          </IconButton>
        </div>
      </div>
    </header>
  )
}
