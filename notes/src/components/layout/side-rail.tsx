'use client'

import { BarChart3, ListTodo, Plus, Settings, Tag } from 'lucide-react'
import { BrandMark } from './brand'
import { useUi } from '@/store/ui-context'
import { cn } from '@/lib/utils/cn'
import type { Mode } from '@/lib/tab-url'

interface RailItem {
  mode: Mode
  label: string
  icon: typeof ListTodo
}

/**
 * The three surfaces in the order a day moves through them: what you mean to
 * do, what that added up to, what you want to keep. Order is the argument, so
 * it does not change with usage.
 */
const ITEMS: RailItem[] = [
  { mode: 'day', label: 'Tasks', icon: ListTodo },
  { mode: 'insights', label: 'Insights', icon: BarChart3 },
  { mode: 'notes', label: 'Notes', icon: Tag },
]

/**
 * A permanent column of destinations on anything wider than a phone. It holds
 * the brand, the three surfaces, and the two actions that are always available
 * — add something, change something. Phones get `BottomNav` instead.
 */
export function SideRail() {
  const { mode, setMode, openQuickAdd, openSettings } = useUi()

  return (
    <div className="fixed inset-y-0 left-0 z-50 hidden w-[76px] flex-col items-center border-r border-line bg-surface sm:flex">
      <div className="grid h-[76px] w-full shrink-0 place-items-center">
        <BrandMark className="size-8 rounded-[10px]" />
      </div>

      <nav aria-label="Surfaces" className="flex w-full flex-col items-center gap-1.5 pt-3">
        {ITEMS.map((item) => {
          const active = mode === item.mode
          const Icon = item.icon
          return (
            <button
              key={item.mode}
              type="button"
              onClick={() => setMode(item.mode)}
              aria-current={active ? 'page' : undefined}
              aria-label={item.label}
              title={item.label}
              className={cn(
                'relative grid size-10 place-items-center rounded-xl transition-colors duration-150',
                active
                  ? 'bg-accent-soft text-accent'
                  : 'text-fg-faint hover:bg-surface-hover hover:text-fg-muted',
              )}
            >
              {/* Flush to the window edge, so the active surface reads even
                  when the icon itself is ambiguous at a glance. */}
              {active && (
                <span
                  aria-hidden="true"
                  className="absolute -left-[13px] h-5 w-[3px] rounded-r-full bg-accent"
                />
              )}
              <Icon className="size-[19px]" strokeWidth={2} aria-hidden="true" />
            </button>
          )
        })}
      </nav>

      <div className="mt-auto flex flex-col items-center gap-2 pb-4">
        <button
          type="button"
          onClick={() => openQuickAdd()}
          aria-label="Quick add"
          title="Quick add"
          className="grid size-11 place-items-center rounded-full bg-accent text-accent-fg shadow-pop transition-[transform,background-color] duration-150 hover:bg-accent-hover active:scale-95"
        >
          <Plus className="size-5" strokeWidth={2.4} aria-hidden="true" />
        </button>

        <button
          type="button"
          onClick={openSettings}
          aria-label="Settings"
          title="Settings"
          className="grid size-10 place-items-center rounded-xl text-fg-faint transition-colors duration-150 hover:bg-surface-hover hover:text-fg-muted"
        >
          <Settings className="size-[18px]" strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
