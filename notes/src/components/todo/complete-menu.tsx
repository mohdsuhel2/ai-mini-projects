'use client'

import type { ReactNode } from 'react'
import { Popover } from '@/components/common/popover'
import { Chip } from '@/components/common/chip'
import { formatDuration } from '@/lib/date/format'

const PRESETS = [15, 30, 45, 60, 120]

interface CompleteMenuProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  estimated: number | null
  onComplete: (duration: number | null) => void
  trigger: ReactNode
}

/**
 * Ticking the box completes the task immediately; this menu is the opt-in path
 * for recording how long it actually took. Time tracking is never a toll gate
 * on finishing something.
 */
export function CompleteMenu({
  open,
  onOpenChange,
  estimated,
  onComplete,
  trigger,
}: CompleteMenuProps) {
  const options = estimated && !PRESETS.includes(estimated) ? [estimated, ...PRESETS] : PRESETS

  return (
    <Popover
      open={open}
      onClose={() => onOpenChange(false)}
      trigger={trigger}
      className="w-[15.5rem] p-3"
    >
      <p className="mb-2 text-[12.5px] font-medium text-fg">How long did it take?</p>
      <div className="flex flex-wrap gap-1.5">
        {options.map((minutes) => (
          <Chip key={minutes} onClick={() => onComplete(minutes)}>
            {formatDuration(minutes)}
          </Chip>
        ))}
      </div>
      <button
        type="button"
        onClick={() => onComplete(null)}
        className="mt-2.5 w-full rounded-md py-1.5 text-[12.5px] text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg"
      >
        Just mark it done
      </button>
    </Popover>
  )
}
