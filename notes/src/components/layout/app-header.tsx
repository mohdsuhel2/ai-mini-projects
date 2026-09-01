'use client'

import { Moon, Settings2, Sun } from 'lucide-react'
import { BrandMark } from './brand'
import { IconButton } from '@/components/common/icon-button'
import { useTheme } from '@/hooks/use-theme'
import { useUi } from '@/store/ui-context'
import { formatDayFull } from '@/lib/date/format'
import { todayKey } from '@/lib/date/day-key'

export function AppHeader() {
  const { resolved, toggle } = useTheme()
  const { openSettings } = useUi()

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1220px] items-center gap-3 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark />
          <div className="min-w-0">
            <p className="text-[14px] font-semibold leading-none tracking-[-0.015em] text-fg">
              Simply Notes
            </p>
            <p className="mt-1 hidden text-[11.5px] leading-none text-fg-faint sm:block">
              Plan less. Remember more.
            </p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-1">
          <p className="mr-1 hidden text-[12.5px] text-fg-subtle md:block">
            {formatDayFull(todayKey())}
          </p>

          <IconButton
            label={resolved === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            onClick={toggle}
          >
            {resolved === 'dark' ? (
              <Sun className="size-4" strokeWidth={2} />
            ) : (
              <Moon className="size-4" strokeWidth={2} />
            )}
          </IconButton>

          <IconButton label="Settings" onClick={openSettings}>
            <Settings2 className="size-4" strokeWidth={2} />
          </IconButton>
        </div>
      </div>
    </header>
  )
}
