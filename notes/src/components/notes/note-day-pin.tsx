'use client'

import { PinIcon } from '@/lib/app-icons'
import { pinNoteForDay, unpinNoteFromDay } from '@/features/notes/api'
import { formatDayLabel } from '@/lib/date/format'
import { todayKey } from '@/lib/date/day-key'
import { Button } from '@/components/common/button'
import type { DayKey, Note } from '@/types'

export function NoteDayPin({ note, day = todayKey() }: { note: Note; day?: DayKey }) {
  const pinned = note.pinnedDay === day
  const label = formatDayLabel(day)

  async function toggle() {
    if (pinned) await unpinNoteFromDay(note.id)
    else await pinNoteForDay(note.id, day)
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={() => void toggle()}
      className={pinned ? 'text-accent' : undefined}
    >
      <PinIcon size="sm" strokeWidth={pinned ? 2 : undefined} />
      {pinned ? `Pinned for ${label}` : `Pin for ${label}`}
    </Button>
  )
}
