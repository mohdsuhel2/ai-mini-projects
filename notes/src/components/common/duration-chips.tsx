'use client'

import { useState } from 'react'
import { Chip } from '@/components/common/chip'
import { formatDuration } from '@/lib/date/format'
import { cn } from '@/lib/utils/cn'

const PRESETS = [15, 30, 45, 60, 120]

interface DurationChipsProps {
  value: number | null
  onChange: (minutes: number | null) => void
  className?: string
  /** Rendered before the presets, e.g. "How long did it take?" */
  label?: string
}

export function DurationChips({ value, onChange, className, label }: DurationChipsProps) {
  const [customOpen, setCustomOpen] = useState(false)
  const isCustom = value != null && !PRESETS.includes(value)

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {label && <span className="mr-0.5 text-[12.5px] text-fg-subtle">{label}</span>}

      {PRESETS.map((minutes) => (
        <Chip
          key={minutes}
          active={value === minutes}
          onClick={() => onChange(value === minutes ? null : minutes)}
        >
          {formatDuration(minutes)}
        </Chip>
      ))}

      {customOpen || isCustom ? (
        <label className="inline-flex h-7 items-center gap-1 rounded-md border border-accent-line bg-accent-soft px-2 text-[12.5px] text-accent">
          <input
            type="number"
            min={1}
            max={1440}
            autoFocus
            value={value ?? ''}
            aria-label="Duration in minutes"
            onChange={(event) => {
              const next = Number(event.target.value)
              onChange(Number.isFinite(next) && next > 0 ? next : null)
            }}
            onBlur={() => value == null && setCustomOpen(false)}
            className="tnum w-11 bg-transparent text-right outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
          />
          min
        </label>
      ) : (
        <Chip onClick={() => setCustomOpen(true)}>Custom</Chip>
      )}
    </div>
  )
}
