'use client'

import { Monitor, Moon, Sun } from 'lucide-react'
import { Dialog } from '@/components/common/dialog'
import { DataSection } from './data-section'
import { useTheme } from '@/hooks/use-theme'
import { useUi } from '@/store/ui-context'
import { GA_MEASUREMENT_ID } from '@/lib/analytics'
import { cn } from '@/lib/utils/cn'
import type { ThemePreference } from '@/types'

const THEMES: Array<{ value: ThemePreference; label: string; icon: typeof Sun }> = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

const SHORTCUTS: Array<[string, string]> = [
  ['N', 'New task'],
  ['A', 'Log an activity'],
  ['⌘ K', 'Quick add'],
  ['T', 'Jump to today'],
  ['E', 'Toggle Notes'],
  ['?', 'Open settings'],
  ['Esc', 'Close what is open'],
]

export function SettingsDialog() {
  const { settingsOpen, closeSettings } = useUi()
  const { preference, setTheme } = useTheme()

  return (
    <Dialog
      open={settingsOpen}
      onClose={closeSettings}
      title="Settings"
      description="Simply Notes keeps everything on this device."
    >
      <div className="flex-1 space-y-7 overflow-y-auto px-5 py-5">
        <section className="space-y-3">
          <h3 className="text-[13px] font-semibold text-fg">Appearance</h3>
          <div
            role="radiogroup"
            aria-label="Theme"
            className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-bg-sunk p-0.5"
          >
            {THEMES.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={preference === value}
                onClick={() => void setTheme(value)}
                className={cn(
                  'inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12.5px] font-medium transition-colors duration-150',
                  preference === value
                    ? 'bg-surface text-fg shadow-[0_1px_2px_rgba(0,0,0,0.04)]'
                    : 'text-fg-muted hover:text-fg',
                )}
              >
                <Icon className="size-3.5" strokeWidth={2} aria-hidden="true" />
                {label}
              </button>
            ))}
          </div>
        </section>

        <DataSection />

        <section className="space-y-3">
          <h3 className="text-[13px] font-semibold text-fg">Keyboard</h3>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {SHORTCUTS.map(([key, description]) => (
              <li key={key} className="flex items-center gap-2 text-[12.5px]">
                <kbd className="tnum inline-flex min-w-[2.25rem] justify-center rounded border border-line bg-bg-sunk px-1.5 py-0.5 text-[11px] text-fg-muted">
                  {key}
                </kbd>
                <span className="text-fg-muted">{description}</span>
              </li>
            ))}
          </ul>
          <p className="text-[12px] text-fg-faint">
            Single-key shortcuts stay out of the way while you are typing.
          </p>
        </section>

        <section className="space-y-2 border-t border-line pt-5">
          <h3 className="text-[13px] font-semibold text-fg">Privacy</h3>
          <p className="text-[12.5px] leading-relaxed text-fg-muted">
            Your tasks, activities and notes are stored only in this browser. They are never sent to
            a server, because there is no server to send them to.
          </p>
          <p className="text-[12px] leading-relaxed text-fg-faint">
            {GA_MEASUREMENT_ID
              ? 'Anonymous usage counts (how often features are used, never what you wrote) are sent to Google Analytics. Enabling Do Not Track in your browser turns this off.'
              : 'Analytics is switched off in this deployment.'}
          </p>
        </section>
      </div>
    </Dialog>
  )
}
