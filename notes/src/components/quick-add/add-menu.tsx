'use client'

import { useState, type ReactNode } from 'react'
import {
  CheckIcon,
  ICON_STROKE_STRONG,
  ListTodoIcon,
  NotebookPenIcon,
  PlayIcon,
  SparklesIcon,
} from '@/lib/app-icons'
import { Popover, PopoverItem } from '@/components/common/popover'
import { useUi } from '@/store/ui-context'
import { cn } from '@/lib/utils/cn'

interface AddMenuProps {
  /** The button itself, so the rail and the bottom bar can style their own. */
  children: (props: { onClick: () => void; expanded: boolean }) => ReactNode
  align?: 'start' | 'end'
  side?: 'bottom' | 'top'
  className?: string
}

/**
 * What the global + actually offers.
 *
 * It used to open the quick-add palette wherever you pressed it, which meant
 * pressing + on the Notes surface handed you a task parser — and there was no
 * way at all to reach a new note, or to start a timer, from the one button the
 * app presents as "make something". The four things this app creates are now
 * all one tap away, each routed to the surface that owns it.
 *
 * The palette keeps its own row rather than being the whole button: it is the
 * fastest path once you know the syntax, and the slowest to discover.
 */
export function AddMenu({ children, align = 'start', side = 'bottom', className }: AddMenuProps) {
  const [open, setOpen] = useState(false)
  const { openQuickAdd, requestCompose, setMode, showDay } = useUi()

  function run(action: () => void) {
    setOpen(false)
    action()
  }

  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      align={align}
      side={side}
      className={cn('sm:min-w-[15rem]', className)}
      trigger={children({ onClick: () => setOpen((v) => !v), expanded: open })}
    >
      <PopoverItem onClick={() => run(() => openQuickAdd('todo'))}>
        <ListTodoIcon size="sm" className="text-fg-subtle" />
        New task
      </PopoverItem>

      <PopoverItem onClick={() => run(() => openQuickAdd('activity'))}>
        <CheckIcon size="sm" className="text-fg-subtle" strokeWidth={ICON_STROKE_STRONG} />
        Log what I did
      </PopoverItem>

      {/* Both of these need a surface, not a dialog, so they ask for the pane
          first and leave the intent for it to pick up. */}
      <PopoverItem
        onClick={() =>
          run(() => {
            showDay('today')
            requestCompose('timer')
          })
        }
      >
        <PlayIcon size="sm" className="text-fg-subtle" />
        Start a timer
      </PopoverItem>

      <PopoverItem
        onClick={() =>
          run(() => {
            setMode('notes')
            requestCompose('note')
          })
        }
      >
        <NotebookPenIcon size="sm" className="text-fg-subtle" />
        New note
      </PopoverItem>

      <div className="my-1 h-px bg-line" />

      <PopoverItem onClick={() => run(() => openQuickAdd())}>
        <SparklesIcon size="sm" className="text-accent" />
        Quick add
        <kbd className="ml-auto rounded bg-bg-sunk px-1.5 py-0.5 text-[10px] text-fg-faint">
          ⌘K
        </kbd>
      </PopoverItem>
    </Popover>
  )
}
