'use client'

import { ICON_STROKE_STRONG, PlusIcon, surfaceNavIcons } from '@/lib/app-icons'
import { AddMenu } from '@/components/quick-add/add-menu'
import { useUi } from '@/store/ui-context'
import { cn } from '@/lib/utils/cn'

/**
 * Thumb-level navigation on phones. The centre action is quick-add rather than
 * a third destination, because adding is what people open this app to do.
 */
export function BottomNav({ openCount }: { openCount: number }) {
  const { mode, setMode, pane, showDay } = useUi()

  return (
    <nav
      aria-label="Sections"
      className="fixed inset-x-0 bottom-0 z-40 px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:hidden"
    >
      <div className="mx-auto flex h-[4.25rem] max-w-md items-center justify-around rounded-2xl border border-line bg-surface/95 px-2 shadow-pop backdrop-blur-xl">
        <NavButton
          active={mode === 'day'}
          onClick={() => showDay(pane)}
          icon={<surfaceNavIcons.day size="nav" />}
          label="Tasks"
          badge={openCount}
        />

        <NavButton
          active={mode === 'insights'}
          onClick={() => setMode('insights')}
          icon={<surfaceNavIcons.insights size="nav" />}
          label="Insights"
        />

        <AddMenu align="start" side="top">
          {({ onClick, expanded }) => (
            <button
              type="button"
              onClick={onClick}
              aria-label="Add"
              aria-haspopup="menu"
              aria-expanded={expanded}
              className="grid size-12 place-items-center rounded-xl bg-accent text-accent-fg transition-transform duration-150 active:scale-95"
            >
              <PlusIcon
                size="xl"
                strokeWidth={ICON_STROKE_STRONG}
                className={cn('transition-transform duration-200', expanded && 'rotate-45')}
              />
            </button>
          )}
        </AddMenu>

        <NavButton
          active={mode === 'notes'}
          onClick={() => setMode('notes')}
          icon={<surfaceNavIcons.notes size="nav" />}
          label="Notes"
        />
      </div>
    </nav>
  )
}

function NavButton({
  active,
  onClick,
  icon,
  label,
  badge,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
  badge?: number
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'relative flex w-[4.25rem] flex-col items-center gap-1 rounded-2xl py-2 transition-colors duration-150',
        active ? 'text-accent' : 'text-fg-subtle',
      )}
    >
      {icon}
      <span className="text-[10.5px] font-medium">{label}</span>
      {badge != null && badge > 0 && (
        <span className="tnum absolute right-2 top-0.5 min-w-[15px] rounded-full bg-line px-1 text-[9.5px] font-semibold leading-[15px] text-fg-muted">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  )
}
