'use client'

import { useRef, useState } from 'react'
import { Download, Eraser, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/common/button'
import {
  BackupError,
  backupFilename,
  clearAllData,
  exportBackup,
  importBackup,
  parseBackup,
} from '@/lib/db/backup'
import { ConfirmDialog } from '@/components/common/confirm-dialog'
import { SegmentedControl } from '@/components/common/segmented-control'
import {
  previewCleanup,
  runCleanup,
  RETENTION_LABELS,
  type CleanupPreview,
  type RetentionWindow,
} from '@/features/settings/cleanup'
import { formatDayFull } from '@/lib/date/format'
import { track } from '@/lib/analytics'
import { useUi } from '@/store/ui-context'

export function DataSection() {
  const fileInput = useRef<HTMLInputElement>(null)
  const { notify } = useUi()
  const [error, setError] = useState<string | null>(null)
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [keep, setKeep] = useState<RetentionWindow>('month')
  // Held rather than recomputed at confirm time: the sentence the user agreed
  // to and the rows that get deleted have to be the same set.
  const [pendingSweep, setPendingSweep] = useState<CleanupPreview | null>(null)

  async function handleExport() {
    const backup = await exportBackup()
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = backupFilename()
    anchor.click()
    URL.revokeObjectURL(url)
    track('data_exported', {
      todos: backup.counts.todos ?? 0,
      activities: backup.counts.activities ?? 0,
    })
    notify('Backup downloaded')
  }

  async function handleImport(file: File) {
    setError(null)
    try {
      const backup = parseBackup(await file.text())
      const result = await importBackup(backup, 'merge')
      track('data_imported', { todos: result.todos, activities: result.activities })
      notify(`Imported ${result.todos} tasks and ${result.activities} activities`)
    } catch (cause) {
      setError(cause instanceof BackupError ? cause.message : 'That import could not be completed.')
    }
  }

  async function reviewSweep() {
    const preview = await previewCleanup(keep)
    if (preview.total === 0) {
      notify('Nothing old enough to clear')
      return
    }
    setPendingSweep(preview)
  }

  async function confirmSweep() {
    const preview = pendingSweep
    setPendingSweep(null)
    if (!preview) return
    const result = await runCleanup(keep)
    track('data_swept', { window: keep, removed: result.total })
    notify(`Removed ${result.total} old ${result.total === 1 ? 'record' : 'records'}`)
  }

  /**
   * Spells out the exact blast radius; there is no undo behind this button.
   *
   * The two halves are separate sentences on purpose: bin rows are purged
   * whatever their date, and folding them into the "from before X" clause
   * would promise a cutoff that does not apply to them.
   */
  function sweepSentence(preview: CleanupPreview): string {
    const aged: string[] = []
    if (preview.activities.length > 0) {
      aged.push(
        `${preview.activities.length} logged ${preview.activities.length === 1 ? 'activity' : 'activities'}`,
      )
    }
    if (preview.todos.length > 0) {
      aged.push(`${preview.todos.length} finished ${preview.todos.length === 1 ? 'task' : 'tasks'}`)
    }

    const sentences: string[] = []
    if (aged.length > 0) {
      sentences.push(
        `This permanently removes ${aged.join(' and ')} from before ${formatDayFull(preview.cutoff)}.`,
      )
    }

    const binned = preview.notes.length + preview.folders.length
    if (binned > 0) {
      sentences.push(
        `It also empties the bin: ${binned} already-deleted ${binned === 1 ? 'item' : 'items'}, of any age, ${binned === 1 ? 'becomes' : 'become'} unrecoverable.`,
      )
    }

    sentences.push(
      'Tasks you have not finished are kept whatever their age, and notes are never removed for being old.',
    )
    sentences.push('This cannot be undone — export a backup first if you are unsure.')
    return sentences.join(' ')
  }

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-[13px] font-semibold text-fg">Your data</h3>
        <p className="mt-0.5 text-[12.5px] leading-relaxed text-fg-muted">
          Everything lives in this browser on this device. Export a copy before clearing your
          browser data, or to move to another machine.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={handleExport}>
          <Download className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Export JSON
        </Button>

        <Button size="sm" onClick={() => fileInput.current?.click()}>
          <Upload className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Import
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void handleImport(file)
            event.target.value = ''
          }}
        />

      </div>

      {/* Between "export everything" and "destroy everything" there was nothing.
          Most people want neither — they want the last few months and none of
          the years behind it. */}
      <div className="space-y-2 rounded-xl bg-bg-sunk p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[12.5px] font-medium text-fg">Clear older history</p>
          <SegmentedControl<RetentionWindow>
            aria-label="How much to keep"
            value={keep}
            onChange={setKeep}
            options={(Object.keys(RETENTION_LABELS) as RetentionWindow[]).map((value) => ({
              value,
              label: RETENTION_LABELS[value].replace('Last ', ''),
            }))}
          />
        </div>
        <p className="text-[12px] leading-relaxed text-fg-muted">
          Keeps the {RETENTION_LABELS[keep].toLowerCase()} of logged activity and finished tasks,
          and removes what came before. Unfinished tasks and all notes are kept.
        </p>
        <Button size="sm" onClick={() => void reviewSweep()}>
          <Eraser className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Review what would go
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        {confirmingClear ? (
          <span className="inline-flex items-center gap-2 rounded-md bg-danger-soft px-2 py-1">
            <span className="text-[12.5px] text-danger">Delete everything?</span>
            <Button
              size="sm"
              variant="danger"
              className="h-6 px-2 text-[12px]"
              onClick={async () => {
                await clearAllData()
                track('data_cleared')
                setConfirmingClear(false)
                notify('All data cleared')
              }}
            >
              Yes, delete
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[12px]"
              onClick={() => setConfirmingClear(false)}
            >
              Cancel
            </Button>
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-danger hover:bg-danger-soft hover:text-danger"
            onClick={() => setConfirmingClear(true)}
          >
            <Trash2 className="size-3.5" strokeWidth={2} aria-hidden="true" />
            Clear all data
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={pendingSweep !== null}
        title={`Remove ${pendingSweep?.total ?? 0} older ${pendingSweep?.total === 1 ? 'record' : 'records'}?`}
        body={pendingSweep ? sweepSentence(pendingSweep) : ''}
        confirmLabel="Remove them"
        onConfirm={() => void confirmSweep()}
        onClose={() => setPendingSweep(null)}
      />

      {error && (
        <p role="alert" className="text-[12.5px] text-danger">
          {error}
        </p>
      )}
      <p className="text-[12px] text-fg-faint">
        Import merges with what is already here, keeping whichever copy of a record was edited most
        recently.
      </p>
    </section>
  )
}
